"""Install the owned ledger baseline without application tables.

Revision ID: ledger_0001
Revises:
Create Date: 2026-11-03 13:15:20+00:00

"""

from pathlib import Path

from alembic import op

from ledger.migrations.runtime import execute_baseline

revision = "ledger_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the reviewed ledger objects and adopt supported legacy rows."""
    execute_baseline(
        op.get_bind(), (Path(__file__).parents[1] / "ledger_0001.sql").read_text()
    )


def downgrade() -> None:
    """Reject destructive removal of ledger records and receipts.

    Raises:
        RuntimeError: Baseline rollback needs a tested backup restoration.
    """
    raise RuntimeError("Restore a verified backup to roll back the ledger baseline")
