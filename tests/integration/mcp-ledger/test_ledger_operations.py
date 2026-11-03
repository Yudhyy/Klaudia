"""Check committed append receipts against the isolated PostgreSQL store."""

import asyncio
import uuid

import pytest
import asyncpg

from ledger.errors import IdempotencyConflictError, RevisionConflictError
from ledger.operations import AppendRows
from ledger.store import LedgerStore, SheetNotFoundError
from tests.integration.postgres import POSTGRES_TEST_URL


@pytest.fixture
async def operation_store():
    """Create and clean up a workbook in the isolated integration database."""
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    workbook = await store.create_spreadsheet(90199, f"operations-{uuid.uuid4().hex}")
    workspace = workbook["spreadsheetId"]
    sheet = await store.create_sheet(workspace, "Expenses", [["Amount"], [100]])
    try:
        yield store, workspace, sheet["sheetId"]
    finally:
        await store.pool.execute(
            "DELETE FROM ledger_operation WHERE workspace = $1", workspace
        )
        await store.delete_spreadsheet(workspace)
        await store.close()


def append_request(sheet_id: int, **changes) -> AppendRows:
    """Build a checked append with optional changes for conflict tests."""
    fields = dict(
        sheet_id=sheet_id,
        expected_revision=0,
        idempotency_key="request-1",
        rows=[[185000]],
    )
    return AppendRows(**(fields | changes))


async def test_append_and_receipt_commit_together(operation_store):
    """A successful append reports the exact committed change and revision."""
    store, workspace, sheet_id = operation_store
    receipt = await store.append_checked(workspace, append_request(sheet_id))

    assert receipt["status"] == "committed"
    assert receipt["target"] == {"spreadsheet_id": workspace, "sheet_id": sheet_id}
    assert (receipt["before_revision"], receipt["after_revision"]) == (0, 1)
    assert receipt["changes"] == {
        "rows_inserted": 1,
        "cells_written": 1,
        "first_row": 3,
    }
    assert receipt["calculation_status"] == "not_supported"
    assert receipt["accounting_validation"] == "not_run"
    assert await store.get_grid(workspace, "Expenses") == [["Amount"], [100], [185000]]
    stored = await store.pool.fetchval(
        "SELECT receipt FROM ledger_operation WHERE workspace = $1", workspace
    )
    assert receipt["operation_id"] in stored


async def test_concurrent_retries_append_once(operation_store):
    """Concurrent delivery of one request returns one receipt and writes once."""
    store, workspace, sheet_id = operation_store
    request = append_request(sheet_id)
    receipts = await asyncio.gather(
        *(store.append_checked(workspace, request) for _ in range(4))
    )

    assert all(receipt == receipts[0] for receipt in receipts)
    assert len(await store.get_grid(workspace, "Expenses")) == 3


async def test_reused_key_rejects_changed_payload(operation_store):
    """An idempotency key cannot silently stand for a different append."""
    store, workspace, sheet_id = operation_store
    await store.append_checked(workspace, append_request(sheet_id))
    with pytest.raises(IdempotencyConflictError):
        await store.append_checked(workspace, append_request(sheet_id, rows=[[999]]))
    assert len(await store.get_grid(workspace, "Expenses")) == 3


async def test_stale_revision_rolls_back_request_and_write(operation_store):
    """Failed preconditions leave neither an append nor a reserved key."""
    store, workspace, sheet_id = operation_store
    await store.mutate_grid(workspace, "Expenses", lambda rows: rows + [[200]])
    with pytest.raises(RevisionConflictError):
        await store.append_checked(workspace, append_request(sheet_id))
    assert (
        await store.pool.fetchval(
            "SELECT count(*) FROM ledger_operation WHERE workspace = $1", workspace
        )
        == 0
    )
    receipt = await store.append_checked(
        workspace, append_request(sheet_id, expected_revision=1)
    )
    assert receipt["after_revision"] == 2


async def test_concurrent_distinct_requests_cannot_share_revision(operation_store):
    """Two different appends based on the same read cannot both commit."""
    store, workspace, sheet_id = operation_store
    outcomes = await asyncio.gather(
        store.append_checked(workspace, append_request(sheet_id)),
        store.append_checked(
            workspace, append_request(sheet_id, idempotency_key="request-2")
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, RevisionConflictError) for outcome in outcomes) == 1
    assert len(await store.get_grid(workspace, "Expenses")) == 3


async def test_snapshot_and_rename_preserve_identity(operation_store):
    """Renaming changes the revision without changing the stable sheet ID."""
    store, workspace, sheet_id = operation_store
    before = await store.get_snapshot(workspace, "Expenses")
    await store.rename_sheet(workspace, "Expenses", "Operating expenses")
    after = await store.get_snapshot(workspace, "Operating expenses")
    assert before.sheet_id == after.sheet_id == sheet_id
    assert (before.revision, after.revision) == (0, 1)
    with pytest.raises(RevisionConflictError):
        await store.append_checked(workspace, append_request(sheet_id))
    await store.append_checked(workspace, append_request(sheet_id, expected_revision=1))
    assert len(await store.get_grid(workspace, "Operating expenses")) == 3


async def test_foreign_sheet_id_is_not_writable(operation_store):
    """A stable ID does not bypass the request's workbook boundary."""
    store, workspace, sheet_id = operation_store
    with pytest.raises(SheetNotFoundError):
        await store.append_checked("another-workbook", append_request(sheet_id))
    assert len(await store.get_grid(workspace, "Expenses")) == 2


async def test_replay_survives_later_edits(operation_store):
    """A committed retry returns its original receipt after later mutations."""
    store, workspace, sheet_id = operation_store
    request = append_request(sheet_id)
    original = await store.append_checked(workspace, request)
    await store.rename_sheet(workspace, "Expenses", "Renamed")
    assert await store.append_checked(workspace, request) == original
    assert (await store.get_snapshot(workspace, "Renamed")).revision == 2


async def test_receipt_storage_failure_rolls_back_append(operation_store):
    """A database failure saving the receipt rolls back the preceding grid write."""
    store, workspace, sheet_id = operation_store
    constraint = f"reject_receipt_{uuid.uuid4().hex}"
    # The generated workspace contains only a fixed prefix and a UUID.
    await store.pool.execute(
        f"ALTER TABLE ledger_operation ADD CONSTRAINT {constraint} "
        f"CHECK (workspace <> '{workspace}' OR receipt IS NULL)"
    )
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await store.append_checked(workspace, append_request(sheet_id))
        snapshot = await store.get_snapshot(workspace, "Expenses")
        assert snapshot.revision == 0
        assert snapshot.values == [["Amount"], [100]]
        assert (
            await store.pool.fetchval(
                "SELECT count(*) FROM ledger_operation WHERE workspace = $1", workspace
            )
            == 0
        )
    finally:
        await store.pool.execute(
            f"ALTER TABLE ledger_operation DROP CONSTRAINT {constraint}"
        )


async def test_idempotency_keys_are_scoped_to_workbooks(operation_store):
    """Two workbooks may use the same key without sharing an operation."""
    store, workspace, sheet_id = operation_store
    other = await store.create_spreadsheet(90199, f"other-{uuid.uuid4().hex}")
    other_workspace = other["spreadsheetId"]
    try:
        other_sheet = await store.create_sheet(other_workspace, "Claims")
        first = await store.append_checked(workspace, append_request(sheet_id))
        second = await store.append_checked(
            other_workspace, append_request(other_sheet["sheetId"])
        )
        assert first["operation_id"] != second["operation_id"]
        assert second["target"]["spreadsheet_id"] == other_workspace
    finally:
        await store.pool.execute(
            "DELETE FROM ledger_operation WHERE workspace = $1", other_workspace
        )
        await store.delete_spreadsheet(other_workspace)


async def test_revision_migration_preserves_existing_grid(migration_database):
    """Upgrade the supplied old ledger layout twice without replacing its grid."""
    from ledger.migrations import upgrade_ledger

    database_url, connection = migration_database
    await connection.execute("""
        CREATE TABLE ledger_sheet (
            sheet_id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            workspace TEXT NOT NULL, title TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            grid JSONB NOT NULL DEFAULT '[]',
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (workspace, title)
        );
        INSERT INTO ledger_sheet (workspace, title, grid)
        VALUES ('existing-workbook', 'Existing sheet', '[[123]]');
    """)
    await upgrade_ledger(database_url)
    assert dict(
        await connection.fetchrow("SELECT sheet_id,grid,revision FROM ledger_sheet")
    ) == {"sheet_id": 1, "grid": "[[123]]", "revision": 0}
    await connection.execute("UPDATE ledger_sheet SET grid='[[456]]'")
    await upgrade_ledger(database_url)
    assert await connection.fetchval("SELECT revision FROM ledger_sheet") == 1
    assert await connection.fetchval("SELECT grid FROM ledger_sheet") == "[[456]]"


async def test_direct_grid_edit_also_invalidates_snapshot(operation_store):
    """Database revisions cover edits made outside the Python store as well."""
    store, workspace, sheet_id = operation_store
    await store.pool.execute(
        "UPDATE ledger_sheet SET grid = '[[999]]' WHERE sheet_id = $1", sheet_id
    )
    with pytest.raises(RevisionConflictError):
        await store.append_checked(workspace, append_request(sheet_id))
    assert (await store.get_snapshot(workspace, "Expenses")).revision == 1
