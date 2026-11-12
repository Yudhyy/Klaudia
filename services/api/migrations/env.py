"""Run the application Alembic history on the locked release connection."""

from klaudia_api.adapters.persistence.migrations import APPLICATION_HISTORY
from ledger_postgres.migrations.authoring import run_environment

run_environment(APPLICATION_HISTORY)
