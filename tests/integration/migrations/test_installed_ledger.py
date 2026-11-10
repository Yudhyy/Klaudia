"""Initialize the installed ledger adapter without the MCP or application package."""

from tests.packaging.test_ledger_core import install_agent_packages, probe


async def test_installed_adapter_initializes_a_standalone_ledger(
    migration_database, tmp_path
):
    """Create and read owned workbook rows with no application tables or imports."""
    database_url, connection = migration_database
    python = install_agent_packages(
        tmp_path, ("packages/ledger-core", "packages/ledger-postgres")
    )
    assert (
        probe(
            python,
            f"database_url = {database_url!r}\n"
            + '''
import asyncio
from importlib.util import find_spec
from ledger_postgres.store import LedgerStore

assert find_spec('app') is None
assert find_spec('ledger') is None
assert find_spec('fastmcp') is None

async def standalone():
    """Initialize the installed history and retain workbook data across startup."""
    store = LedgerStore(database_url)
    await store.connect()
    try:
        workbook = await store.create_spreadsheet(17, 'Standalone workbook')
        workspace = workbook['spreadsheetId']
        await store.create_sheet(workspace, 'Claims', [['Amount'], ['0.10']])
        snapshot = await store.get_snapshot(workspace, 'Claims')
        assert snapshot.values == [['Amount'], ['0.10']]
        assert await store.pool.fetchval("SELECT to_regclass('public.session')") is None
        assert await store.pool.fetchval("SELECT to_regclass('public.user')") is None
    finally:
        await store.close()
    await store.connect_existing()
    try:
        assert (await store.list_spreadsheets(17))[0]['spreadsheetId'] == workspace
    finally:
        await store.close()

asyncio.run(standalone())
print('installed standalone ledger initialized')
''',
        )
        == "installed standalone ledger initialized"
    )
    assert await connection.fetchval("SELECT count(*) FROM ledger_spreadsheet") == 1
    assert await connection.fetchval("SELECT to_regclass('public.session')") is None
