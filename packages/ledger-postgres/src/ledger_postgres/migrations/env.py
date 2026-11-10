"""Run the standalone ledger Alembic environment on the release connection."""

from ledger_postgres.migrations import LEDGER_HISTORY
from ledger_postgres.migrations.authoring import run_environment

run_environment(LEDGER_HISTORY)
