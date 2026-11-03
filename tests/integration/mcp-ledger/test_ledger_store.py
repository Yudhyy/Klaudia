"""LedgerStore multi-tenancy tests: user -> spreadsheets -> sheets.

Exercises the spreadsheet layer directly against Postgres (no MCP
subprocess): CRUD, per-user listing, cross-spreadsheet isolation, FK
enforcement, cascade delete, and legacy-workspace adoption on connect.
Skipped when Postgres is unreachable.
"""

import secrets
import uuid

import asyncpg
import pytest

from ledger.store import (
    LedgerStore,
    SheetNotFoundError,
    SpreadsheetExistsError,
    SpreadsheetNotFoundError,
)
from tests.integration.postgres import POSTGRES_TEST_URL

_PG_URL = POSTGRES_TEST_URL


async def _pg_available() -> bool:
    try:
        conn = await asyncpg.connect(_PG_URL, timeout=3)
        await conn.close()
        return True
    except Exception:
        return False


@pytest.fixture
async def store():
    if not await _pg_available():
        pytest.skip("The isolated PostgreSQL test database is unavailable")
    ledger_store = LedgerStore(_PG_URL)
    await ledger_store.connect()
    yield ledger_store
    await ledger_store.close()


def _user_id() -> int:
    """Random user id so tests are isolated on the shared database."""
    return secrets.randbelow(2_000_000_000) + 1_000


async def test_spreadsheet_crud(store):
    user = _user_id()

    created = await store.create_spreadsheet(user, "Keuangan Pribadi")
    assert created["name"] == "Keuangan Pribadi"
    assert isinstance(created["spreadsheetId"], str) and created["spreadsheetId"]

    listed = await store.list_spreadsheets(user)
    assert [s["name"] for s in listed] == ["Keuangan Pribadi"]
    assert listed[0]["spreadsheetId"] == created["spreadsheetId"]

    fetched = await store.get_spreadsheet(created["spreadsheetId"])
    assert fetched["userId"] == user
    assert fetched["name"] == "Keuangan Pribadi"

    await store.rename_spreadsheet(created["spreadsheetId"], "Bisnis")
    assert (await store.get_spreadsheet(created["spreadsheetId"]))["name"] == "Bisnis"

    await store.delete_spreadsheet(created["spreadsheetId"])
    assert await store.list_spreadsheets(user) == []
    with pytest.raises(SpreadsheetNotFoundError):
        await store.get_spreadsheet(created["spreadsheetId"])


async def test_duplicate_spreadsheet_name_rejected(store):
    user = _user_id()
    await store.create_spreadsheet(user, "Utama")
    with pytest.raises(SpreadsheetExistsError):
        await store.create_spreadsheet(user, "Utama")
    # Same name under a different user is fine.
    other = await store.create_spreadsheet(_user_id(), "Utama")
    assert other["name"] == "Utama"


async def test_list_spreadsheets_is_per_user(store):
    user_a, user_b = _user_id(), _user_id()
    await store.create_spreadsheet(user_a, "A-only")
    await store.create_spreadsheet(user_b, "B-only")

    names_a = [s["name"] for s in await store.list_spreadsheets(user_a)]
    names_b = [s["name"] for s in await store.list_spreadsheets(user_b)]
    assert names_a == ["A-only"]
    assert names_b == ["B-only"]


async def test_sheet_isolation_across_spreadsheets(store):
    user = _user_id()
    ss_a = await store.create_spreadsheet(user, "Pribadi")
    ss_b = await store.create_spreadsheet(user, "Bisnis")

    await store.create_sheet(ss_a["spreadsheetId"], "Jul", grid=[["a"]])
    await store.create_sheet(ss_b["spreadsheetId"], "Jul", grid=[["b"]])

    assert await store.get_grid(ss_a["spreadsheetId"], "Jul") == [["a"]]
    assert await store.get_grid(ss_b["spreadsheetId"], "Jul") == [["b"]]

    titles_a = [s["title"] for s in await store.list_sheets(ss_a["spreadsheetId"])]
    assert titles_a == ["Jul"]

    await store.delete_sheet(ss_a["spreadsheetId"], "Jul")
    with pytest.raises(SheetNotFoundError):
        await store.get_grid(ss_a["spreadsheetId"], "Jul")
    assert await store.get_grid(ss_b["spreadsheetId"], "Jul") == [["b"]]


async def test_create_sheet_in_unknown_spreadsheet_rejected(store):
    with pytest.raises(SpreadsheetNotFoundError):
        await store.create_sheet(f"ghost-{uuid.uuid4().hex}", "Jul")


async def test_delete_spreadsheet_cascades_sheets(store):
    user = _user_id()
    ss = await store.create_spreadsheet(user, "Temp")
    await store.create_sheet(ss["spreadsheetId"], "Jul", grid=[["x"]])

    await store.delete_spreadsheet(ss["spreadsheetId"])

    row = await store.pool.fetchrow(
        "SELECT 1 FROM ledger_sheet WHERE workspace = $1", ss["spreadsheetId"]
    )
    assert row is None


async def test_ensure_spreadsheet_is_idempotent(store):
    workspace = f"ws-{uuid.uuid4().hex[:8]}"
    await store.ensure_spreadsheet(workspace)
    await store.ensure_spreadsheet(workspace)  # second call is a no-op

    fetched = await store.get_spreadsheet(workspace)
    assert fetched["userId"] == 0  # system-owned
    assert fetched["name"] == workspace

    # Sheets can be created in an ensured workspace (FK satisfied).
    await store.create_sheet(workspace, "Jul")
    assert [s["title"] for s in await store.list_sheets(workspace)] == ["Jul"]


async def test_connect_adopts_orphan_workspaces(migration_database):
    """Adopt verified unversioned legacy rows through the shared ledger history."""
    from ledger.migrations import LEDGER_HISTORY

    database_url, connection = migration_database
    await connection.execute((LEDGER_HISTORY.directory / "ledger_0001.sql").read_text())
    await connection.execute(
        "ALTER TABLE ledger_sheet DROP CONSTRAINT ledger_sheet_workspace_fkey"
    )
    await connection.execute(
        "INSERT INTO ledger_sheet (workspace,title,grid) VALUES ('legacy','Claims','[]')"
    )
    fresh = LedgerStore(database_url)
    try:
        await fresh.connect()
        adopted = await fresh.get_spreadsheet("legacy")
        assert adopted["userId"] == 0
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM pg_constraint WHERE conname='ledger_sheet_workspace_fkey'"
            )
            == 1
        )
    finally:
        await fresh.close()


async def test_recent_activity_orders_by_last_edit_with_preview(store):
    """Most-recently-edited sheet comes first, with a last-row preview."""
    user = _user_id()
    created = await store.create_spreadsheet(user, f"RA-{uuid.uuid4().hex[:8]}")
    workspace = created["spreadsheetId"]
    await store.create_sheet(workspace, "Jan", [["Tanggal", "Toko", "Jumlah"]])
    await store.create_sheet(workspace, "Feb", [["Tanggal", "Toko", "Jumlah"]])
    # Edit Feb, then Jan, so Jan is the most-recently-edited sheet.
    await store.mutate_grid(workspace, "Feb", lambda g: g + [["2026-02-01", "A", "10"]])
    await store.mutate_grid(
        workspace, "Jan", lambda g: g + [["2026-01-01", "Indomaret", "152000"]]
    )

    activity = await store.get_recent_activity(workspace, limit=3)

    assert [a["title"] for a in activity][0] == "Jan"
    top = activity[0]
    assert top["rows"] == 2  # header + one appended row
    assert top["last_row"] == ["2026-01-01", "Indomaret", "152000"]
    assert top["updated_at"] is not None


async def test_recent_activity_empty_workspace_returns_empty(store):
    user = _user_id()
    created = await store.create_spreadsheet(user, f"RA-empty-{uuid.uuid4().hex[:8]}")
    activity = await store.get_recent_activity(created["spreadsheetId"], limit=3)
    assert activity == []


async def test_recent_activity_respects_limit(store):
    user = _user_id()
    created = await store.create_spreadsheet(user, f"RA-lim-{uuid.uuid4().hex[:8]}")
    workspace = created["spreadsheetId"]
    for name in ("S1", "S2", "S3"):
        await store.create_sheet(workspace, name, [["h"]])

    activity = await store.get_recent_activity(workspace, limit=2)
    assert len(activity) == 2
