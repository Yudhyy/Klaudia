"""Run the application Alembic history on the locked release connection."""

from app.adapters.persistence.migrations import APPLICATION_HISTORY
from ledger_postgres.migrations.authoring import run_environment

run_environment(APPLICATION_HISTORY)
