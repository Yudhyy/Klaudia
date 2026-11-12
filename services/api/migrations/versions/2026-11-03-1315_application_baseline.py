"""Install the owned identity, chat, document, memory and workflow baseline.

Revision ID: app_0001
Revises:
Create Date: 2026-11-03 13:15:20+00:00

"""

from pathlib import Path

from alembic import op

from ledger_postgres.migrations.runtime import execute_baseline

revision = "app_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the reviewed application objects with the existing seed user."""
    execute_baseline(
        op.get_bind(), (Path(__file__).parents[1] / "app_0001.sql").read_text()
    )
    op.execute(
        "SELECT setval(pg_get_serial_sequence('\"user\"', 'user_id'), GREATEST((SELECT COALESCE(MAX(user_id), 1) FROM \"user\"), 1))"
    )


def downgrade() -> None:
    """Reject destructive removal of application records and checkpoints.

    Raises:
        RuntimeError: Baseline rollback needs a tested backup restoration.
    """
    raise RuntimeError(
        "Restore a verified backup to roll back the application baseline"
    )
