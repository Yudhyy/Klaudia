"""Run the standalone ledger Alembic environment on the release connection."""

from ledger.migrations import LEDGER_HISTORY
from ledger.migrations.authoring import run_environment

run_environment(LEDGER_HISTORY)
