"""Exercise the public release commands against disposable PostgreSQL stores."""

import asyncio
from pathlib import Path
import sys

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "klaudia_api.adapters.persistence",
        "ledger.migrations",
        "ledger_postgres.migrations",
    ],
)
async def test_release_cli_upgrade_and_read_only_check(migration_database, module_name):
    """Run upgrade, check, and repeated upgrade through the actual CLI."""
    database_url, connection = migration_database
    expected = ["ledger: ledger_0001"]
    if module_name == "klaudia_api.adapters.persistence":
        expected.append("application: app_0001")
    for action in ("upgrade", "check", "upgrade"):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            module_name,
            action,
            "--database-url",
            database_url,
            cwd=Path(__file__).resolve().parents[3],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, diagnostics = await asyncio.wait_for(
                process.communicate(), timeout=30
            )
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 0, diagnostics.decode()
        assert output.decode().splitlines() == expected
    application_table = await connection.fetchval(
        "SELECT to_regclass('public.session')"
    )
    assert (application_table is not None) == (
        module_name == "klaudia_api.adapters.persistence"
    )
