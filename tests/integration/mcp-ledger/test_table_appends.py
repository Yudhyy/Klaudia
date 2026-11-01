"""Owned table appends preserve neighbouring regions and replay committed receipts."""

from app.adapters.agent.operations import OperationAdapter

from app.adapters.agent.catalogue import CatalogueAdapter

import asyncio
import uuid

import pytest

from ledger.catalogue import CatalogueStore
from ledger.resources import TableRegistration, ResourceNotFoundError
from ledger.errors import IdempotencyConflictError, RevisionConflictError
from ledger.store import LedgerStore
from ledger.table_operations import TableAppend, execute_table_append
from tests.integration.postgres import POSTGRES_TEST_URL


@pytest.fixture
async def append_setup():
    """Create two separated tables with a neighbouring exact decimal value."""
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    workbook = await store.create_spreadsheet(90401, f"append-{uuid.uuid4().hex}")
    workspace = workbook["spreadsheetId"]
    sheet = await store.create_sheet(
        workspace,
        "Finance",
        [["Employee", "Amount"], ["A", 10], [], [], ["Other", "Value"], ["B", 999]],
    )
    catalogue = CatalogueStore(store.pool)
    table = await catalogue.register(
        workspace,
        TableRegistration(
            sheet_id=sheet["sheetId"],
            expected_sheet_revision=0,
            table_range="A1:B2",
            name="Claims",
        ),
    )
    await catalogue.register(
        workspace,
        TableRegistration(
            sheet_id=sheet["sheetId"],
            expected_sheet_revision=0,
            table_range="A5:B6",
            name="Other",
        ),
    )
    try:
        yield store, catalogue, workspace, sheet["sheetId"], table["table_id"]
    finally:
        await store.pool.execute(
            "DELETE FROM ledger_spreadsheet WHERE spreadsheet_id = $1", workspace
        )
        await store.pool.execute(
            "DELETE FROM ledger_table_operation WHERE workspace = $1", workspace
        )
        await store.close()


def request(table_id: str, **changes) -> TableAppend:
    """Create a checked named-record append with test-specific overrides."""
    return TableAppend(
        **(
            {
                "table_id": table_id,
                "expected_sheet_revision": 0,
                "expected_catalogue_revision": 1,
                "idempotency_key": f"task:{table_id}",
                "records": [{"Employee": "C", "Amount": 20}],
            }
            | changes
        )
    )


async def test_append_commits_grid_bounds_and_receipt_together(append_setup):
    """A table grows into empty cells without shifting a neighbouring table."""
    store, catalogue, workspace, sheet_id, table_id = append_setup
    operation = request(table_id)
    receipt = await execute_table_append(store.pool, 90401, operation)
    assert receipt["status"] == "committed"
    assert receipt["target"]["table_id"] == table_id
    assert receipt["before_revision"] == 0
    assert receipt["after_revision"] == 1
    assert receipt["after_catalogue_revision"] == 2
    assert receipt["changes"]["range"] == "A3:B3"
    assert receipt["calculation_status"] == "not_supported"
    observed = await catalogue.inspect_owned(90401, table_id)
    assert observed["range"] == "A1:B3"
    assert observed["record_count"] == 2
    assert observed["freshness"] == "current"
    values = await store.get_grid(workspace, "Finance")
    assert values[2] == ["C", 20]
    assert values[4:] == [["Other", "Value"], ["B", 999]]
    assert await execute_table_append(store.pool, 90401, operation) == receipt
    assert (await store.get_snapshot(workspace, "Finance")).revision == 1
    with pytest.raises(IdempotencyConflictError):
        await execute_table_append(
            store.pool,
            90401,
            request(table_id, records=[{"Employee": "D", "Amount": 20}]),
        )


async def test_failed_append_leaves_no_receipt_or_partial_grid(append_setup):
    """Collision and revision failures roll back the entire operation."""
    store, catalogue, workspace, _, table_id = append_setup
    before = await store.get_grid(workspace, "Finance")
    with pytest.raises(ValueError, match="overlap|occupied"):
        await execute_table_append(
            store.pool,
            90401,
            request(table_id, records=[{"Employee": "C", "Amount": 20}] * 3),
        )
    assert await store.get_grid(workspace, "Finance") == before
    assert (await catalogue.inspect_owned(90401, table_id))["catalogue_revision"] == 1
    assert (
        await store.pool.fetchval(
            "SELECT count(*) FROM ledger_table_operation WHERE user_id = 90401 AND idempotency_key = $1",
            f"task:{table_id}",
        )
        == 0
    )
    with pytest.raises(RevisionConflictError):
        await execute_table_append(
            store.pool, 90401, request(table_id, expected_sheet_revision=9)
        )


async def test_concurrent_replays_commit_once(append_setup):
    """Concurrent identical requests share one receipt and one mutation."""
    store, _, workspace, _, table_id = append_setup
    receipts = await asyncio.gather(
        *[execute_table_append(store.pool, 90401, request(table_id)) for _ in range(3)]
    )
    assert receipts[0] == receipts[1] == receipts[2]
    assert (await store.get_snapshot(workspace, "Finance")).revision == 1


async def test_foreign_write_and_replay_recheck_ownership(append_setup):
    """A stored operation receipt does not grant continuing access to its workbook."""
    store, _, workspace, _, table_id = append_setup
    with pytest.raises(ResourceNotFoundError):
        await execute_table_append(store.pool, 90402, request(table_id))
    operation = request(table_id)
    await execute_table_append(store.pool, 90401, operation)
    await store.pool.execute(
        "UPDATE ledger_spreadsheet SET user_id = 90402 WHERE spreadsheet_id = $1",
        workspace,
    )
    with pytest.raises(ResourceNotFoundError):
        await execute_table_append(store.pool, 90401, operation)


async def test_failure_after_cell_write_rolls_back_every_change(
    append_setup, monkeypatch
):
    """A failure after SQL writes still restores both grid and catalogue state."""
    from ledger import table_operations

    store, catalogue, workspace, _, table_id = append_setup
    write_records = table_operations._write_records

    async def fail_after_write(*arguments):
        """Inject an execution failure after the cell mutation has occurred."""
        await write_records(*arguments)
        raise RuntimeError("injected failure")

    monkeypatch.setattr(table_operations, "_write_records", fail_after_write)
    with pytest.raises(RuntimeError, match="injected failure"):
        await store.append_table_owned(90401, request(table_id))
    assert (await store.get_snapshot(workspace, "Finance")).revision == 0
    assert (await catalogue.inspect_owned(90401, table_id))["catalogue_revision"] == 1
    assert (
        await store.pool.fetchval(
            "SELECT count(*) FROM ledger_table_operation WHERE user_id = 90401 AND idempotency_key = $1",
            f"task:{table_id}",
        )
        == 0
    )


async def test_ownership_cannot_change_during_append(append_setup, monkeypatch):
    """The ownership lock spans the write and receipt commit."""
    from ledger import table_operations

    store, _, workspace, _, table_id = append_setup
    write_records = table_operations._write_records
    writing = asyncio.Event()
    resume = asyncio.Event()

    async def pause_write(*arguments):
        """Pause while the transaction holds the ownership and source locks."""
        writing.set()
        await resume.wait()
        return await write_records(*arguments)

    monkeypatch.setattr(table_operations, "_write_records", pause_write)
    append = asyncio.create_task(store.append_table_owned(90401, request(table_id)))
    await asyncio.wait_for(writing.wait(), timeout=5)
    transfer = asyncio.create_task(
        store.pool.execute(
            "UPDATE ledger_spreadsheet SET user_id = 90402 WHERE spreadsheet_id = $1",
            workspace,
        )
    )
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(transfer), timeout=0.05)
    finally:
        resume.set()
        receipt, _ = await asyncio.gather(append, transfer)
    assert receipt["status"] == "committed"
    with pytest.raises(ResourceNotFoundError):
        await store.append_table_owned(90401, request(table_id))


async def test_sql_patch_preserves_side_cells_and_stored_fractional_digits(
    append_setup,
):
    """Appending to offset columns preserves the exact JSONB values beside them."""
    store, catalogue, workspace, _, _ = append_setup
    sheet = await store.create_sheet(workspace, "Offset", [])
    await store.pool.execute(
        "UPDATE ledger_sheet SET grid = $1::jsonb WHERE sheet_id = $2",
        '[[null,"Employee","Amount"],[0.123456789012345678901,"A",10],[0.987654321098765432109,null,null,"side"]]',
        sheet["sheetId"],
    )
    table = await catalogue.register(
        workspace,
        TableRegistration(
            sheet_id=sheet["sheetId"],
            expected_sheet_revision=1,
            table_range="B1:C2",
            name="Offset claims",
        ),
    )
    receipt = await store.append_table_owned(
        90401, request(table["table_id"], expected_sheet_revision=1)
    )
    assert receipt["changes"]["range"] == "B3:C3"
    assert (
        await store.pool.fetchval(
            "SELECT grid -> 1 ->> 0 FROM ledger_sheet WHERE sheet_id = $1",
            sheet["sheetId"],
        )
        == "0.123456789012345678901"
    )
    assert (
        await store.pool.fetchval(
            "SELECT grid -> 2 ->> 0 FROM ledger_sheet WHERE sheet_id = $1",
            sheet["sheetId"],
        )
        == "0.987654321098765432109"
    )
    assert (
        await store.pool.fetchval(
            "SELECT grid -> 2 ->> 3 FROM ledger_sheet WHERE sheet_id = $1",
            sheet["sheetId"],
        )
        == "side"
    )


@pytest.mark.parametrize(
    "records",
    [
        [{"Employee": "C"}],
        [{"Employee": "C", "Amount": "=1+1"}],
        [{"Employee": None, "Amount": None}],
    ],
)
async def test_unsafe_literal_records_fail_without_mutation(append_setup, records):
    """Missing fields, formula expressions and empty records cannot commit."""
    store, _, workspace, _, table_id = append_setup
    with pytest.raises(ValueError):
        await store.append_table_owned(90401, request(table_id, records=records))
    assert (await store.get_snapshot(workspace, "Finance")).revision == 0


async def test_formula_table_rejects_append_without_propagation(append_setup):
    """An append cannot silently leave formula columns without expressions."""
    store, catalogue, workspace, _, _ = append_setup
    sheet = await store.create_sheet(
        workspace, "Formula", [["Employee", "Amount"], ["A", "=1+1"]]
    )
    table = await catalogue.register(
        workspace,
        TableRegistration(
            sheet_id=sheet["sheetId"],
            expected_sheet_revision=0,
            table_range="A1:B2",
            name="Formula claims",
        ),
    )
    with pytest.raises(ValueError, match="formula propagation"):
        await store.append_table_owned(90401, request(table["table_id"]))


async def test_receipt_replays_after_table_deletion_but_not_workbook_deletion(
    append_setup,
):
    """Stored receipts survive deletion while current workbook ownership gates access."""
    store, _, workspace, _, table_id = append_setup
    operation = request(table_id)
    receipt = await store.append_table_owned(90401, operation)
    await store.delete_sheet(workspace, "Finance")
    assert await store.append_table_owned(90401, operation) == receipt
    await store.delete_spreadsheet(workspace)
    with pytest.raises(ResourceNotFoundError):
        await store.append_table_owned(90401, operation)


async def test_competing_new_operations_require_fresh_revisions(append_setup):
    """Different keys cannot both write against the same observed source revision."""
    store, _, workspace, _, table_id = append_setup
    outcomes = await asyncio.gather(
        *[
            store.append_table_owned(
                90401, request(table_id, idempotency_key=f"{table_id}:{index}")
            )
            for index in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, RevisionConflictError) for outcome in outcomes) == 1
    assert (await store.get_snapshot(workspace, "Finance")).revision == 1


async def test_append_uses_blank_reserved_rows_without_growing_bounds(append_setup):
    """Reserved blank rows are consumed before expanding the registered region."""
    store, catalogue, workspace, _, _ = append_setup
    sheet = await store.create_sheet(
        workspace, "Reserved", [["Employee", "Amount"], ["A", 10], [], []]
    )
    table = await catalogue.register(
        workspace,
        TableRegistration(
            sheet_id=sheet["sheetId"],
            expected_sheet_revision=0,
            table_range="A1:B4",
            name="Reserved claims",
        ),
    )
    receipt = await store.append_table_owned(90401, request(table["table_id"]))
    assert receipt["changes"]["range"] == "A3:B3"
    assert receipt["changes"]["table_range"] == "A1:B4"
    assert (await catalogue.inspect_owned(90401, table["table_id"]))[
        "record_count"
    ] == 2


async def test_unregistered_occupied_cells_block_expansion(append_setup):
    """Blank catalogue coverage cannot authorise overwriting a nearby footer."""
    store, catalogue, workspace, _, _ = append_setup
    sheet = await store.create_sheet(
        workspace, "Footer", [["Employee", "Amount"], ["A", 10], ["Footer", 10]]
    )
    table = await catalogue.register(
        workspace,
        TableRegistration(
            sheet_id=sheet["sheetId"],
            expected_sheet_revision=0,
            table_range="A1:B2",
            name="Footer claims",
        ),
    )
    with pytest.raises(ValueError, match="occupied cells"):
        await store.append_table_owned(90401, request(table["table_id"]))
    assert (await store.get_snapshot(workspace, "Footer")).revision == 0


async def test_prepared_append_survives_restart_and_lost_receipt(append_setup):
    """A durable proposal replays its original revisions after execution."""
    from ledger.table_operations import prepare_table_append, execute_prepared_append

    store, _, workspace, _, table_id = append_setup
    proposal = await prepare_table_append(store.pool, 90401, request(table_id))
    assert proposal["status"] == "prepared"
    assert (await store.get_snapshot(workspace, "Finance")).revision == 0
    assert await prepare_table_append(store.pool, 90401, request(table_id)) == proposal
    receipt = await execute_prepared_append(
        store.pool, 90401, proposal["operation_ref"]
    )
    restarted = LedgerStore(POSTGRES_TEST_URL)
    await restarted.connect()
    try:
        assert (
            await execute_prepared_append(
                restarted.pool, 90401, proposal["operation_ref"]
            )
            == receipt
        )
    finally:
        await restarted.close()
    assert (await store.get_snapshot(workspace, "Finance")).revision == 1


async def test_prepared_append_checks_identity_and_current_revision(append_setup):
    """Possessing a proposal reference cannot grant access or bypass revisions."""
    from ledger.table_operations import prepare_table_append, execute_prepared_append

    store, _, workspace, _, table_id = append_setup
    proposal = await prepare_table_append(store.pool, 90401, request(table_id))
    with pytest.raises(ResourceNotFoundError):
        await execute_prepared_append(store.pool, 90402, proposal["operation_ref"])
    await store.append_table_owned(
        90401, request(table_id, idempotency_key="other:" + table_id)
    )
    with pytest.raises(RevisionConflictError):
        await execute_prepared_append(store.pool, 90401, proposal["operation_ref"])
    await store.pool.execute(
        "UPDATE ledger_spreadsheet SET user_id = 90402 WHERE spreadsheet_id = $1",
        workspace,
    )
    with pytest.raises(ResourceNotFoundError):
        await execute_prepared_append(store.pool, 90401, proposal["operation_ref"])


@pytest.mark.parametrize("amount", [1e20, -0.0])
async def test_prepared_append_preserves_exact_numeric_request(append_setup, amount):
    """Persistence must not normalise numeric tokens used by the fingerprint."""
    from ledger.table_operations import prepare_table_append, execute_prepared_append

    store, _, _, _, table_id = append_setup
    proposal = await prepare_table_append(
        store.pool,
        90401,
        request(table_id, records=[{"Employee": "C", "Amount": amount}]),
    )
    receipt = await execute_prepared_append(
        store.pool, 90401, proposal["operation_ref"]
    )
    assert receipt["status"] == "committed"
    assert (
        await execute_prepared_append(store.pool, 90401, proposal["operation_ref"])
        == receipt
    )


async def test_main_agent_prepares_executes_and_retries_real_append(append_setup):
    """Agent tools cross real ownership, storage and receipt boundaries."""
    import json

    from langchain_core.messages import AIMessage, ToolMessage
    from app.services.catalogue.service import CatalogueService
    from app.services.core.operations import OperationService
    from klaudia.core.agent.agent import MainAgent
    from klaudia.core.agent.context import TaskContext
    from tests.unit.test_main_agent import call

    store, catalogue, workspace, _, table_id = append_setup

    class AppendModel:
        """Use actual prepared evidence rather than inventing operation references."""

        def bind_tools(self, tools):
            """Accept the agent's declared capabilities."""
            return self

        async def ainvoke(self, messages, config=None):
            """Advance a deterministic append script from returned tool evidence."""
            evidence = [
                message for message in messages if isinstance(message, ToolMessage)
            ]
            if not evidence:
                return call("inspect_resource", {"table_id": table_id})
            if len(evidence) == 1:
                return call(
                    "prepare_table_append",
                    {
                        "table_id": table_id,
                        "records": [{"Employee": "C", "Amount": 20}],
                    },
                    "prepare",
                )
            if len(evidence) < 4:
                proposal = json.loads(evidence[1].content)
                return call(
                    "execute_operation",
                    {"operation_ref": proposal["operation_ref"]},
                    f"execute_{len(evidence)}",
                )
            return AIMessage(
                content="Added one record; formulas were not recalculated."
            )

    outcome = await MainAgent(
        AppendModel(),
        CatalogueAdapter(CatalogueService(catalogue)),
        operations=OperationAdapter(OperationService(store)),
    ).run("Add C's claim of 20", TaskContext(user_id=90401))
    assert outcome.status == "answered"
    assert len(outcome.operation_receipts) == 1
    assert outcome.operation_receipts[0]["changes"]["records_appended"] == 1
    assert outcome.operation_receipts[0]["calculation_status"] == "not_supported"
    assert (await store.get_snapshot(workspace, "Finance")).revision == 1
    assert (await store.get_grid(workspace, "Finance"))[2] == ["C", 20]


async def test_lost_commit_response_recovers_through_operation_service(append_setup):
    """An agent failure after commit retains a reference that replays without duplicates."""
    from app.services.catalogue.service import CatalogueService
    from app.services.core.operations import OperationService
    from klaudia.core.agent.agent import MainAgent, AgentExecutionError
    from klaudia.core.agent.context import TaskContext
    from tests.unit.test_main_agent import ScriptedModel, call

    store, catalogue, workspace, _, table_id = append_setup
    operations = OperationService(store)
    proposal = await operations.prepare(90401, request(table_id))

    class LostReceiptService(OperationService):
        """Simulate transport failure after the ledger commits."""

        async def execute(self, user_id, operation_ref):
            """Commit normally and discard the response."""
            await super().execute(user_id, operation_ref)
            raise ConnectionError("response lost after commit")

    model = ScriptedModel(
        [call("execute_operation", {"operation_ref": proposal["operation_ref"]})]
    )
    with pytest.raises(AgentExecutionError) as failure:
        await MainAgent(
            model,
            CatalogueAdapter(CatalogueService(catalogue)),
            operations=OperationAdapter(LostReceiptService(store)),
        ).run("Execute saved append", TaskContext(user_id=90401))
    reference = failure.value.outcome.operation_references[0]
    assert failure.value.outcome.operation_receipts == ()
    receipt = await OperationService(store).execute(90401, reference)
    assert receipt["status"] == "committed"
    assert (await store.get_snapshot(workspace, "Finance")).revision == 1
    await store.pool.execute(
        "UPDATE ledger_spreadsheet SET user_id = 90402 WHERE spreadsheet_id = $1",
        workspace,
    )
    with pytest.raises(ResourceNotFoundError):
        await operations.execute(90401, reference)
