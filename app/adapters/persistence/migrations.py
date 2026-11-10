"""Compose ledger and application histories in explicit release order."""

from pathlib import Path

import asyncpg

from app.adapters.persistence.metadata import metadata
from ledger_postgres.migrations import LEDGER_HISTORY
from ledger_postgres.migrations.runtime import History, check_history, upgrade_histories

APPLICATION_HISTORY = History(
    "application", "app_0001", "app_alembic_version", Path(__file__).parent, metadata
)
HISTORIES = {"ledger": LEDGER_HISTORY, "application": APPLICATION_HISTORY}


async def upgrade_database(database_url: str) -> None:
    """Upgrade ledger, then application objects, stopping rollout on failure.

    Args:
        database_url: Explicit PostgreSQL release target.
    """
    await upgrade_histories(database_url, tuple(HISTORIES.values()))


async def check_database(connection: asyncpg.Connection) -> None:
    """Check both required revisions on the caller's runtime connection.

    Args:
        connection: Borrowed application or task-store connection.
    """
    for history in HISTORIES.values():
        await check_history(connection, history)
