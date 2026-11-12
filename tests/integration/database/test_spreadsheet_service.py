"""SpreadsheetService: per-user spreadsheet resolution and ownership guards.

Runs against real Postgres (skipped when unreachable). Ownership failures
raise SpreadsheetNotFoundError — foreign spreadsheets are indistinguishable
from absent ones (same non-enumerating stance as session 404s).
"""

import secrets

import asyncpg
import pytest

from klaudia_api.adapters.ledger.spreadsheets import SpreadsheetService
from ledger.store import (
    LedgerStore,
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
async def service():
    if not await _pg_available():
        pytest.skip("The isolated PostgreSQL test database is unavailable")
    store = LedgerStore(_PG_URL)
    await store.connect()
    yield SpreadsheetService(store, default_name="Utama")
    await store.close()


def _user_id() -> int:
    return secrets.randbelow(2_000_000_000) + 1_000


async def test_resolve_scope_provisions_default_once(service):
    user = _user_id()

    first = await service.resolve_scope(user)
    second = await service.resolve_scope(user)

    assert first == second
    spreadsheets = await service.list_for_user(user)
    assert [s["name"] for s in spreadsheets] == ["Utama"]


async def test_resolve_scope_validates_requested_ownership(service):
    owner, intruder = _user_id(), _user_id()
    created = await service.create(owner, "Bisnis")

    assert (
        await service.resolve_scope(owner, created["spreadsheetId"])
        == (created["spreadsheetId"])
    )
    with pytest.raises(SpreadsheetNotFoundError):
        await service.resolve_scope(intruder, created["spreadsheetId"])
    with pytest.raises(SpreadsheetNotFoundError):
        await service.resolve_scope(owner, "does-not-exist")


async def test_rename_and_delete_enforce_ownership(service):
    owner, intruder = _user_id(), _user_id()
    created = await service.create(owner, "Bisnis")

    with pytest.raises(SpreadsheetNotFoundError):
        await service.rename(intruder, created["spreadsheetId"], "Hijacked")
    with pytest.raises(SpreadsheetNotFoundError):
        await service.delete(intruder, created["spreadsheetId"])

    await service.rename(owner, created["spreadsheetId"], "Bisnis 2026")
    assert [s["name"] for s in await service.list_for_user(owner)] == ["Bisnis 2026"]
    await service.delete(owner, created["spreadsheetId"])
    assert await service.list_for_user(owner) == []


async def test_create_duplicate_name_rejected(service):
    user = _user_id()
    await service.create(user, "Utama")
    with pytest.raises(SpreadsheetExistsError):
        await service.create(user, "Utama")


async def test_recent_activity_surfaces_last_edited_sheet(service):
    """Continuity read returns the workspace's most-recently-edited sheet."""
    user = _user_id()
    scope = await service.resolve_scope(user)  # provisions default "Utama"
    await service._store.create_sheet(scope, "Jun", [["Tanggal", "Toko"]])
    await service._store.mutate_grid(
        scope, "Jun", lambda g: g + [["2026-06-30", "Indomaret"]]
    )

    activity = await service.recent_activity(scope, limit=3)

    assert activity[0]["title"] == "Jun"
    assert activity[0]["last_row"] == ["2026-06-30", "Indomaret"]
