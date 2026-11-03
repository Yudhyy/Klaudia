"""Provide explicit upgrade and read-only check commands for owned histories."""

import argparse
import asyncio
import os

import asyncpg
from dotenv import load_dotenv

from ledger.migrations.runtime import History, check_history, upgrade_histories


async def _run_command(
    arguments: argparse.Namespace, histories: tuple[History, ...]
) -> None:
    """Run the selected command and close its dedicated connection.

    Args:
        arguments: Parsed action and database URL.
        histories: Histories selected by the application or standalone entrypoint.
    """
    if arguments.action == "upgrade":
        await upgrade_histories(arguments.database_url, histories)
    else:
        connection = await asyncpg.connect(
            arguments.database_url,
            server_settings={"default_transaction_read_only": "on"},
        )
        try:
            for history in histories:
                await check_history(connection, history)
        finally:
            await connection.close()
    for history in histories:
        print(f"{history.name}: {history.revision}")


def run_cli(histories: tuple[History, ...]) -> None:
    """Read the explicit target and run the shared migration command.

    Args:
        histories: Independent histories in release order.
    """
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Upgrade or check owned PostgreSQL histories"
    )
    parser.add_argument("action", choices=["upgrade", "check"])
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    arguments = parser.parse_args()
    if not arguments.database_url:
        parser.error("Set DATABASE_URL or --database-url")
    asyncio.run(_run_command(arguments, histories))
