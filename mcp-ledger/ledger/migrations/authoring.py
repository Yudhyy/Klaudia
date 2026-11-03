"""Use the standard Alembic authoring CLI with read-only database access."""

import asyncio
from dataclasses import replace
from functools import partial
import os
from pathlib import Path

import asyncpg
from alembic import command, context
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from ledger.migrations import runtime
from ledger.migrations.runtime import History


def _is_authoring_command(configuration: Config) -> bool:
    """Recognize the stock command dispatch used by the locked Alembic release.

    Args:
        configuration: Configuration with the CLI's parsed command options.

    Returns:
        Whether the command can inspect a database to author a revision.
    """
    options = configuration.cmd_opts
    return options is not None and options.cmd[0] in {command.revision, command.check}


def _inspect_history(connection: Connection, history: History) -> None:
    """Compare one current owned schema under the release migration lock.

    Args:
        connection: Dedicated read-only authoring connection.
        history: Owner and metadata selected by the migration environment.

    Raises:
        RuntimeError: The target has not completed its required release migrations.
    """
    with runtime.migration_lock(connection):
        connection.exec_driver_sql("SET search_path TO public, pg_catalog")
        if runtime.classify_layout(connection, history) != "current":
            raise RuntimeError("Run release migrations before authoring a revision")
        connection.commit()
        runtime.run_environment(connection, history)


async def _inspect_database(database_url: str, history: History) -> None:
    """Open and close the original asyncpg DSN in read-only mode.

    Args:
        database_url: Explicit authoring target from DATABASE_URL.
        history: Owner of the candidate metadata change.
    """
    engine = create_async_engine(
        "postgresql+asyncpg://",
        async_creator=partial(
            asyncpg.connect,
            database_url,
            server_settings={"default_transaction_read_only": "on"},
        ),
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_inspect_history, history)
    finally:
        await engine.dispose()


def run_environment(history: History) -> None:
    """Select injected release execution or the stock read-only authoring path.

    Args:
        history: Default owner declared by this migration environment.

    Raises:
        RuntimeError: A raw write command tries to bypass the release coordinator.
        ValueError: Database-backed authoring has no explicit database target.
    """
    if context.config.attributes.get("connection") is not None:
        runtime.run_environment()
        return
    if not _is_authoring_command(context.config):
        raise RuntimeError(
            "Use the coordinated migration runner for upgrades, downgrades and stamps"
        )
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError(
            "Set DATABASE_URL to a current disposable database before authoring"
        )
    selected = replace(history, directory=Path(context.script.dir))
    asyncio.run(_inspect_database(database_url, selected))
