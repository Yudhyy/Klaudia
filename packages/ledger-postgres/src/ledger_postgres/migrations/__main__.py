"""Run ledger-only upgrades or revision checks from an installed package."""

from ledger_postgres.migrations import LEDGER_HISTORY
from ledger_postgres.migrations.cli import run_cli

run_cli((LEDGER_HISTORY,))
