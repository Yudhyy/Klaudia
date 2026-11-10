"""Exercise ledger infrastructure outside the checkout without an MCP host."""

from tests.packaging.test_ledger_core import install_agent_packages, probe


def test_installed_ledger_adapter_has_no_application_or_transport_dependency(tmp_path):
    """Install shared PostgreSQL adapters and their complete standalone history."""
    python = install_agent_packages(
        tmp_path, ("packages/ledger-core", "packages/ledger-postgres")
    )
    assert (
        probe(
            python,
            """
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import asyncpg
import ledger_postgres

asyncpg.connect = Mock(side_effect=AssertionError('connection during import'))
asyncpg.create_pool = Mock(side_effect=AssertionError('pool during import'))
for module in ('approvals', 'approval_service', 'authoring', 'authoring_reads', 'catalogue', 'connections',
               'financial_store', 'operations', 'store', 'table_operations',
               'typed_cells', 'typed_storage', 'migrations',
               'migrations.authoring', 'migrations.catalog', 'migrations.cli',
               'migrations.metadata', 'migrations.runtime'):
    import_module('ledger_postgres.' + module)
for name in ('app', 'config', 'klaudia', 'ledger', 'fastmcp', 'mcp', 'fastapi',
             'agent_core', 'langchain_core', 'redis', 'taskiq'):
    assert find_spec(name) is None, name
assert Path(ledger_postgres.__file__).resolve().is_relative_to(Path(sys.prefix))

from ledger_postgres.migrations import LEDGER_HISTORY
from ledger_postgres.migrations.runtime import baseline_layout
assert Path(LEDGER_HISTORY.directory).resolve().is_relative_to(Path(sys.prefix))
assert LEDGER_HISTORY.scripts().get_heads() == ['ledger_0001']
assert LEDGER_HISTORY.version_table == 'ledger_alembic_version'
assert len(baseline_layout(LEDGER_HISTORY)['tables']) == 6
assert (LEDGER_HISTORY.directory / 'ledger_0001.sql').is_file()
assert (LEDGER_HISTORY.directory / 'script.py.mako').is_file()
assert not (LEDGER_HISTORY.directory / 'alembic.ini').exists()
help_command = subprocess.run(
    [sys.executable, '-I', '-m', 'ledger_postgres.migrations', '--help'],
    capture_output=True, text=True, timeout=20,
)
assert help_command.returncode == 0, help_command.stderr
assert 'upgrade' in help_command.stdout
print('standalone ledger adapters verified')
""",
        )
        == "standalone ledger adapters verified"
    )
