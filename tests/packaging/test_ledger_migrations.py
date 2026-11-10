"""Check that installed ledger wheels carry their independent migration history."""

from tests.packaging.test_ledger_core import install_agent_packages, probe


def test_installed_ledger_migration_assets(tmp_path):
    """Load the ledger history outside the checkout without application imports."""
    python = install_agent_packages(
        tmp_path, ("packages/ledger-core", "packages/ledger-postgres", "mcp-ledger")
    )
    assert (
        probe(
            python,
            """
from pathlib import Path
import sys
from ledger.migrations import LEDGER_HISTORY
from ledger.migrations.runtime import baseline_layout
assert Path(LEDGER_HISTORY.directory).resolve().is_relative_to(Path(sys.prefix))
assert LEDGER_HISTORY.scripts().get_heads() == ["ledger_0001"]
assert (LEDGER_HISTORY.directory / "ledger_0001.sql").is_file()
assert not (LEDGER_HISTORY.directory / "alembic.ini").exists()
assert (LEDGER_HISTORY.directory / "script.py.mako").is_file()
assert len(baseline_layout(LEDGER_HISTORY)["tables"]) == 6
assert "app" not in sys.modules
print("installed ledger history verified")
""",
        )
        == "installed ledger history verified"
    )
