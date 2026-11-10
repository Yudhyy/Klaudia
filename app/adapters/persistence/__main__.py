"""Run the integrated ledger and application release migration step."""

from app.adapters.persistence.migrations import HISTORIES
from ledger_postgres.migrations.cli import run_cli

run_cli(tuple(HISTORIES.values()))
