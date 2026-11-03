"""Owned ledger migration entrypoints without an application dependency."""

from pathlib import Path

from ledger.migrations.metadata import metadata
from ledger.migrations.runtime import History, upgrade_histories

LEDGER_HISTORY = History(
    "ledger", "ledger_0001", "ledger_alembic_version", Path(__file__).parent, metadata
)


async def upgrade_ledger(database_url: str) -> None:
    """Install or adopt only ledger objects under the shared migration lock.

    Args:
        database_url: Explicit PostgreSQL release target.
    """
    await upgrade_histories(database_url, (LEDGER_HISTORY,))
