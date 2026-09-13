"""Checked table lifecycle preserves cells, identities and retry receipts."""

import asyncio
import uuid

import pytest

from ledger.authoring import AuthoringProposal, prepare_authoring, execute_authoring
from ledger.catalogue import CatalogueStore
from ledger.errors import RevisionConflictError
from ledger.resources import ResourceNotFoundError, ResourceSearch
from ledger.store import LedgerStore
from tests.integration.postgres import POSTGRES_TEST_URL


@pytest.fixture
async def authoring_store():
    """Seed two separate header regions and leave space for a third table."""
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    workbook = await store.create_spreadsheet(90411, "authoring-" + uuid.uuid4().hex)
    workspace = workbook["spreadsheetId"]
    sheet = await store.create_sheet(
        workspace,
        "Finance",
        [
            ["Date", "Amount"],
            ["2026-09-13", 10],
            [],
            ["Customer", "Balance"],
            ["Acme", 20],
        ],
    )
    try:
        yield store, workspace, sheet["sheetId"]
    finally:
        await store.delete_spreadsheet(workspace)
        await store.pool.execute(
            "DELETE FROM ledger_table_operation WHERE workspace = $1", workspace
        )
        await store.close()


def proposal(sheet_id, action="register_table", **changes):
    """Build an explicit registration with checked source revisions."""
    return AuthoringProposal.model_validate(
        {
            "action": action,
            "definition": {
                "sheet_id": sheet_id,
                "expected_sheet_revision": 0,
                "table_range": "A1:B2",
                "name": "Claims",
            },
            **changes,
        }
    )


async def execute(store, request):
    """Prepare and commit one operation under the fixture owner."""
    prepared = await prepare_authoring(store.pool, 90411, request)
    if "approval_id" in prepared:
        from ledger.approvals import decide_approval

        await decide_approval(store.pool, 90411, prepared["approval_id"], approve=True)
    return await execute_authoring(store.pool, 90411, prepared["operation_ref"])


async def test_register_create_update_refresh_unregister(authoring_store):
    """All lifecycle steps retain identity and leave unrelated financial cells alone."""
    store, workspace, sheet_id = authoring_store
    catalogue = CatalogueStore(store.pool)
    first = await execute(store, proposal(sheet_id))
    table_id = first["target"]["table_id"]
    original = await catalogue.inspect_owned(90411, table_id)
    created = await execute(
        store,
        proposal(
            sheet_id,
            "create_table",
            definition={
                "sheet_id": sheet_id,
                "expected_sheet_revision": 0,
                "table_range": "D1:E3",
                "name": "Budget",
            },
            headers=["Category", "Budget"],
        ),
    )
    assert created["changes"]["cells_changed"] == 2
    await store.mutate_grid(
        workspace, "Finance", lambda rows: [["Date", "Cost"], *rows[1:]]
    )
    source = await store.pool.fetchval(
        "SELECT revision FROM ledger_sheet WHERE sheet_id=$1", sheet_id
    )
    updated = await execute(
        store,
        proposal(
            sheet_id,
            "update_table",
            table_id=table_id,
            expected_catalogue_revision=1,
            definition={
                "sheet_id": sheet_id,
                "expected_sheet_revision": source,
                "table_range": "A1:B2",
                "name": "Operating claims",
                "aliases": ["Travel claims"],
            },
            column_ids=[column["column_id"] for column in original["columns"]],
        ),
    )
    assert updated["target"]["table_id"] == table_id
    descriptor = await catalogue.inspect_owned(90411, table_id)
    assert [c["column_id"] for c in descriptor["columns"]] == [
        c["column_id"] for c in original["columns"]
    ]
    assert descriptor["columns"][1]["name"] == "Cost"
    assert (
        await catalogue.search_owned(90411, ResourceSearch(intent="Travel claims"))
    )["candidates"][0]["table_id"] == table_id
    before = await store.get_grid(workspace, "Finance")
    removed = await execute(
        store,
        AuthoringProposal(
            action="unregister_table",
            table_id=table_id,
            expected_catalogue_revision=2,
            expected_sheet_revision=source,
        ),
    )
    assert removed["changes"]["cells_changed"] == 0
    assert await store.get_grid(workspace, "Finance") == before
    with pytest.raises(ResourceNotFoundError):
        await catalogue.inspect_owned(90411, table_id)


async def test_retry_and_foreign_execution(authoring_store):
    """Concurrent execution registers once and never grants a foreign caller access."""
    store, _, sheet_id = authoring_store
    prepared = await prepare_authoring(store.pool, 90411, proposal(sheet_id))
    receipts = await asyncio.gather(
        *[
            execute_authoring(store.pool, 90411, prepared["operation_ref"])
            for _ in range(2)
        ]
    )
    assert receipts[0] == receipts[1]
    with pytest.raises(ResourceNotFoundError):
        await execute_authoring(store.pool, 90412, prepared["operation_ref"])
    with pytest.raises(ResourceNotFoundError):
        await prepare_authoring(store.pool, 90412, proposal(sheet_id))


async def test_stale_create_and_occupied_destination_do_not_write(authoring_store):
    """Creating a table never overwrites cells or bypasses a source revision."""
    store, workspace, sheet_id = authoring_store
    before = await store.get_grid(workspace, "Finance")
    with pytest.raises(ValueError, match="blank"):
        await execute(
            store, proposal(sheet_id, "create_table", headers=["Date", "Amount"])
        )
    assert await store.get_grid(workspace, "Finance") == before
    prepared = await prepare_authoring(store.pool, 90411, proposal(sheet_id))
    await store.pool.execute(
        "UPDATE ledger_sheet SET title=title WHERE sheet_id=$1", sheet_id
    )
    with pytest.raises(RevisionConflictError):
        await execute_authoring(store.pool, 90411, prepared["operation_ref"])


async def test_refresh_preserves_column_ids_and_rejects_changed_headers(
    authoring_store,
):
    """Refreshing observes new rows but never guesses a changed column's identity."""
    store, workspace, sheet_id = authoring_store
    created = await execute(store, proposal(sheet_id))
    table_id = created["target"]["table_id"]
    catalogue = CatalogueStore(store.pool)
    original = await catalogue.inspect_owned(90411, table_id)
    await store.mutate_grid(workspace, "Finance", lambda rows: [rows[0], [], *rows[2:]])
    refreshed = await execute(
        store,
        AuthoringProposal(
            action="refresh_table",
            table_id=table_id,
            expected_sheet_revision=1,
            expected_catalogue_revision=1,
        ),
    )
    assert refreshed["after_catalogue_revision"] == 2
    current = await catalogue.inspect_owned(90411, table_id)
    assert current["record_count"] == 0
    assert current["columns"] == original["columns"]
    await store.mutate_grid(
        workspace, "Finance", lambda rows: [["Date", "Cost"], *rows[1:]]
    )
    with pytest.raises(ValueError, match="remapping"):
        await execute(
            store,
            AuthoringProposal(
                action="refresh_table",
                table_id=table_id,
                expected_sheet_revision=2,
                expected_catalogue_revision=2,
            ),
        )


async def test_header_write_preserves_unrelated_exact_numeric_values(authoring_store):
    """Creating nearby headers never round-trips other JSONB numbers through floats."""
    store, _, sheet_id = authoring_store
    await store.pool.execute(
        "UPDATE ledger_sheet SET grid=jsonb_set(grid,'{1,1}','0.12345678901234567890123456789'::jsonb) WHERE sheet_id=$1",
        sheet_id,
    )
    await execute(
        store,
        proposal(
            sheet_id,
            "create_table",
            definition={
                "sheet_id": sheet_id,
                "expected_sheet_revision": 1,
                "table_range": "D1:E2",
                "name": "Budget",
            },
            headers=["Category", "Budget"],
        ),
    )
    assert (
        await store.pool.fetchval(
            "SELECT grid->1->>1 FROM ledger_sheet WHERE sheet_id=$1", sheet_id
        )
        == "0.12345678901234567890123456789"
    )


async def test_unregister_waits_for_approval_and_stale_decision_fails(authoring_store):
    """Unregister cannot bypass human consent or use consent for a changed source."""
    from ledger.approvals import decide_approval
    from ledger.errors import ApprovalRequiredError

    store, _, sheet_id = authoring_store
    registered = await execute(store, proposal(sheet_id))
    pending = await prepare_authoring(
        store.pool,
        90411,
        AuthoringProposal(
            action="unregister_table",
            table_id=registered["target"]["table_id"],
            expected_sheet_revision=0,
            expected_catalogue_revision=1,
        ),
    )
    with pytest.raises(ApprovalRequiredError):
        await execute_authoring(store.pool, 90411, pending["operation_ref"])
    await store.pool.execute(
        "UPDATE ledger_sheet SET title=title WHERE sheet_id=$1", sheet_id
    )
    with pytest.raises(RevisionConflictError):
        await decide_approval(store.pool, 90411, pending["approval_id"], approve=True)


async def test_reregister_after_unregister_has_a_new_identity(authoring_store):
    """A later registration cannot replay the receipt of a removed catalogue entry."""
    store, _, sheet_id = authoring_store
    first = await execute(store, proposal(sheet_id))
    await execute(
        store,
        AuthoringProposal(
            action="unregister_table",
            table_id=first["target"]["table_id"],
            expected_sheet_revision=0,
            expected_catalogue_revision=1,
        ),
    )
    second = await execute(store, proposal(sheet_id))
    assert first["target"]["table_id"] != second["target"]["table_id"]
    assert (
        await CatalogueStore(store.pool).inspect_owned(
            90411, second["target"]["table_id"]
        )
    )["freshness"] == "current"
