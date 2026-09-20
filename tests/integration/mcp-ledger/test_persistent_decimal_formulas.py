"""Typed edits persist exact values, dependency results and truthful receipts."""

import asyncio
import json
import uuid

import asyncpg
import pytest

from ledger.errors import RevisionConflictError
from ledger.store import LedgerStore
from ledger.typed_cells import inspect_workbook, prepare_typed_edit, execute_typed_edit
from ledger.typed_contracts import TypedEditProposal
from tests.integration.postgres import POSTGRES_TEST_URL


@pytest.fixture
async def typed_workbook():
    """Create an isolated two-sheet workbook and remove only this fixture."""
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    workbook = await store.create_spreadsheet(90231, f"typed-{uuid.uuid4().hex}")
    workspace = workbook["spreadsheetId"]
    try:
        first = await store.create_sheet(
            workspace, "Inputs", [["Amount", "Other"], [1, "keep"]]
        )
        second = await store.create_sheet(workspace, "Totals", [["Total"], [None]])
        yield store, workspace, first["sheetId"], second["sheetId"]
    finally:
        await store.delete_spreadsheet(workspace)
        await store.close()


async def edit(fixture, edits):
    """Prepare and execute an exact batch from a fresh owned snapshot."""
    store, workspace, _, _ = fixture
    snapshot = await inspect_workbook(store.pool, 90231, workspace)
    request = TypedEditProposal(
        workbook_id=workspace, snapshot=snapshot["snapshot"], edits=edits
    )
    prepared = await prepare_typed_edit(store.pool, 90231, request)
    return await execute_typed_edit(store.pool, 90231, prepared["operation_ref"])


def input_edit(sheet_id, value):
    """Declare a decimal input with its original literal and display metadata."""
    return {
        "action": "set_input",
        "sheet_id": sheet_id,
        "row": 2,
        "column": 1,
        "input": {
            "kind": "decimal",
            "value": value,
            "unit": "USD",
            "display_format": "0.00",
        },
    }


def formula_edit(sheet_id, dependency, operation="add", literal="0.2"):
    """Define a same-workbook dependency and an explicit monetary scale."""
    return {
        "action": "set_formula",
        "sheet_id": sheet_id,
        "row": 2,
        "column": 1,
        "unit": "USD",
        "formula": {
            "operation": operation,
            "operands": [{"cell_id": dependency}, {"literal": literal}],
            "rounding": {"places": 2, "mode": "ROUND_HALF_EVEN"},
        },
    }


async def test_input_edit_recalculates_cross_sheet_and_replays_receipt(typed_workbook):
    """Committed input edits update dependencies and replay without extra writes."""
    store, workspace, first, second = typed_workbook
    await edit(typed_workbook, [input_edit(first, "0.1")])
    observed = await inspect_workbook(store.pool, 90231, workspace)
    input_id = observed["cells"][0]["cell_id"]
    receipt = await edit(typed_workbook, [formula_edit(second, input_id)])
    assert receipt["calculation_status"] == "current"
    assert receipt["calculation"]["recalculated"] == 1
    assert (await store.get_snapshot(workspace, "Totals")).values[1][0] == 0.3
    receipt = await edit(typed_workbook, [input_edit(first, "0.3")])
    assert receipt["calculation"]["invalidated"] == 1
    assert receipt["calculation"]["results"][0]["value"] == "0.50"
    replay = await execute_typed_edit(store.pool, 90231, receipt["operation_ref"])
    assert replay == receipt
    observed = await inspect_workbook(store.pool, 90231, workspace)
    managed = next(cell for cell in observed["cells"] if cell["cell_id"] == input_id)
    assert managed["raw_value"] == "0.3"
    assert managed["display_format"] == "0.00"


@pytest.mark.parametrize(
    "kind,value",
    [
        ("decimal", "1.234567890123456789"),
        ("decimal", "9007199254740993"),
        ("decimal", "+001.2300"),
        ("text", "00123"),
        ("boolean", False),
        ("date", "2026-09-20"),
    ],
)
async def test_typed_inspection_preserves_raw_values_in_json(
    typed_workbook, kind, value
):
    """Owned typed evidence retains the original value and kind through JSON.

    Args:
        typed_workbook: Isolated owned workbook and ledger store.
        kind: Declared semantic type.
        value: Exact input payload to persist and inspect.
    """
    store, workspace, first, _ = typed_workbook
    declaration = input_edit(first, value)
    declaration["input"]["kind"] = kind
    await edit(typed_workbook, [declaration])
    evidence = await inspect_workbook(store.pool, 90231, workspace)
    cell = json.loads(json.dumps(evidence))["cells"][0]
    assert cell["kind"] == kind
    assert type(cell["raw_value"]) is type(value)
    assert cell["raw_value"] == value
    assert cell["calculation_status"] == "input"


async def test_computed_cells_and_managed_inputs_reject_ordinary_writes(typed_workbook):
    """Legacy and direct grid edits cannot bypass the managed-cell guard."""
    from ledger import grid

    store, workspace, first, second = typed_workbook
    await edit(typed_workbook, [input_edit(first, "1")])
    input_id = (await inspect_workbook(store.pool, 90231, workspace))["cells"][0][
        "cell_id"
    ]
    await edit(typed_workbook, [formula_edit(second, input_id)])
    for title in ("Inputs", "Totals"):
        with pytest.raises(asyncpg.CheckViolationError):
            await store.mutate_grid(
                workspace, title, lambda rows: grid.write_range(rows, "A2", [[99]])
            )
    with pytest.raises(ValueError, match="computed"):
        await edit(typed_workbook, [input_edit(second, "5")])
    await store.mutate_grid(
        workspace, "Inputs", lambda rows: grid.write_range(rows, "B2", [["changed"]])
    )
    assert (await store.get_snapshot(workspace, "Totals")).values[1][0] == 1.2


async def test_failed_recalculation_commits_honest_status_and_null_cache(
    typed_workbook,
):
    """A calculation failure cannot leave an old amount marked current."""
    store, workspace, first, second = typed_workbook
    await edit(typed_workbook, [input_edit(first, "1")])
    identity = (await inspect_workbook(store.pool, 90231, workspace))["cells"][0][
        "cell_id"
    ]
    receipt = await edit(
        typed_workbook, [formula_edit(second, identity, "divide", "0")]
    )
    assert receipt["status"] == "committed"
    assert receipt["calculation_status"] == "failed"
    assert receipt["calculation"]["failed"] == 1
    assert (
        await store.pool.fetchval(
            "SELECT grid -> 1 ->> 0 FROM ledger_sheet WHERE sheet_id=$1", second
        )
        is None
    )
    with pytest.raises(ValueError, match="failed or pending"):
        await store.get_snapshot(workspace, "Totals")


async def test_stale_workbook_snapshot_and_foreign_dependencies_reject(typed_workbook):
    """Whole-workbook revision checks prevent unseen source changes."""
    store, workspace, first, second = typed_workbook
    snapshot = await inspect_workbook(store.pool, 90231, workspace)
    request = TypedEditProposal(
        workbook_id=workspace,
        snapshot=snapshot["snapshot"],
        edits=[input_edit(first, "1")],
    )
    prepared = await prepare_typed_edit(store.pool, 90231, request)
    await store.pool.execute(
        "UPDATE ledger_sheet SET grid=grid WHERE sheet_id=$1", second
    )
    with pytest.raises(RevisionConflictError):
        await execute_typed_edit(store.pool, 90231, prepared["operation_ref"])
    with pytest.raises(ValueError, match="Unknown"):
        await edit(typed_workbook, [formula_edit(second, "cell_not_in_this_workbook")])


async def test_real_cross_workbook_dependency_rejects_even_for_same_owner(
    typed_workbook,
):
    """Owning both workbooks does not enable external formula references.

    Args:
        typed_workbook: Isolated owned workbook and ledger store.
    """
    store, workspace, _, second = typed_workbook
    external = await store.create_spreadsheet(90231, "External formula source")
    external_id = external["spreadsheetId"]
    try:
        source = await store.create_sheet(external_id, "Source", [["Amount"], [1]])
        source_fixture = (store, external_id, source["sheetId"], source["sheetId"])
        await edit(source_fixture, [input_edit(source["sheetId"], "7")])
        source_state = await inspect_workbook(store.pool, 90231, external_id)
        before = await inspect_workbook(store.pool, 90231, workspace)
        with pytest.raises(ValueError, match="Unknown formula dependency"):
            await edit(
                typed_workbook,
                [formula_edit(second, source_state["cells"][0]["cell_id"])],
            )
        assert await inspect_workbook(store.pool, 90231, workspace) == before
    finally:
        await store.delete_spreadsheet(external_id)


async def test_catalogue_refresh_exposes_exact_recalculated_amount(typed_workbook):
    """Registered queries require fresh metadata and preserve large decimal totals.

    Args:
        typed_workbook: Isolated owned workbook and ledger store.
    """
    from ledger.authoring import AuthoringProposal, prepare_authoring, execute_authoring
    from ledger.calculations import CheckedCalculation
    from ledger.catalogue import CatalogueStore
    from ledger.resources import TableRegistration

    store, workspace, first, second = typed_workbook
    await edit(typed_workbook, [input_edit(first, "9007199254740993")])
    source = (await inspect_workbook(store.pool, 90231, workspace))["cells"][0]
    await edit(typed_workbook, [formula_edit(second, source["cell_id"])])
    catalogue = CatalogueStore(store.pool)
    revision = await store.pool.fetchval(
        "SELECT revision FROM ledger_sheet WHERE sheet_id=$1", second
    )
    table = await catalogue.register(
        workspace,
        TableRegistration(
            sheet_id=second,
            expected_sheet_revision=revision,
            table_range="A1:A2",
            name="Exact totals",
        ),
    )
    receipt = await edit(typed_workbook, [input_edit(first, "9007199254740994")])
    assert receipt["catalogue_status"] == "refresh_required"
    revision = next(
        sheet["after_revision"]
        for sheet in receipt["changed_sheets"]
        if sheet["sheet_id"] == second
    )
    request = CheckedCalculation(
        table_id=table["table_id"],
        expected_sheet_revision=revision,
        expected_catalogue_revision=table["catalogue_revision"],
        metrics=[{"column": "Total", "operation": "sum"}],
    )
    with pytest.raises(RevisionConflictError):
        await catalogue.calculate_owned(90231, request)
    prepared = await prepare_authoring(
        store.pool,
        90231,
        AuthoringProposal(
            action="refresh_table",
            table_id=table["table_id"],
            expected_sheet_revision=revision,
            expected_catalogue_revision=table["catalogue_revision"],
        ),
    )
    await execute_authoring(store.pool, 90231, prepared["operation_ref"])
    observed = await catalogue.inspect_owned(90231, table["table_id"])
    evidence = await catalogue.calculate_owned(
        90231,
        request.model_copy(
            update={"expected_catalogue_revision": observed["catalogue_revision"]}
        ),
    )
    assert evidence["groups"][0]["metrics"][0]["value"] == "9007199254740994.20"


async def test_cycle_rejects_without_changing_inputs_or_cached_values(typed_workbook):
    """Changing an existing input into a cyclic formula rolls back the proposal."""
    store, workspace, first, second = typed_workbook
    await edit(typed_workbook, [input_edit(first, "1")])
    input_id = (await inspect_workbook(store.pool, 90231, workspace))["cells"][0][
        "cell_id"
    ]
    await edit(typed_workbook, [formula_edit(second, input_id)])
    before = await inspect_workbook(store.pool, 90231, workspace)
    total_id = next(
        cell["cell_id"] for cell in before["cells"] if cell["sheet_id"] == second
    )
    with pytest.raises(ValueError, match="cycle"):
        await edit(typed_workbook, [formula_edit(first, total_id)])
    assert await inspect_workbook(store.pool, 90231, workspace) == before


async def test_concurrent_retry_commits_once_and_new_stale_proposal_fails(
    typed_workbook,
):
    """Stored references deduplicate delivery while snapshots reject competing edits."""
    import asyncio

    store, workspace, first, _ = typed_workbook
    snapshot = await inspect_workbook(store.pool, 90231, workspace)
    request = TypedEditProposal(
        workbook_id=workspace,
        snapshot=snapshot["snapshot"],
        edits=[input_edit(first, "1")],
    )
    prepared = await prepare_typed_edit(store.pool, 90231, request)
    other = await prepare_typed_edit(store.pool, 90231, request)
    receipts = await asyncio.gather(
        *(
            execute_typed_edit(store.pool, 90231, prepared["operation_ref"])
            for _ in range(2)
        )
    )
    assert receipts[0] == receipts[1]
    with pytest.raises(RevisionConflictError):
        await execute_typed_edit(store.pool, 90231, other["operation_ref"])


async def test_approval_binds_typed_snapshot_and_rechecks_revision(typed_workbook):
    """Approvals cannot authorise changed typed sources or bypass an explicit wait."""
    from ledger.approvals import decide_approval
    from ledger.errors import ApprovalRequiredError

    store, workspace, first, second = typed_workbook
    snapshot = await inspect_workbook(store.pool, 90231, workspace)
    request = TypedEditProposal(
        workbook_id=workspace,
        snapshot=snapshot["snapshot"],
        edits=[input_edit(first, "1")],
    )
    proposal = await prepare_typed_edit(
        store.pool, 90231, request, require_approval=True
    )
    with pytest.raises(ApprovalRequiredError):
        await execute_typed_edit(store.pool, 90231, proposal["operation_ref"])
    await decide_approval(store.pool, 90231, proposal["approval_id"], approve=True)
    await store.pool.execute(
        "UPDATE ledger_sheet SET grid=grid WHERE sheet_id=$1", second
    )
    with pytest.raises(RevisionConflictError):
        await execute_typed_edit(store.pool, 90231, proposal["operation_ref"])


async def test_concurrent_typed_approvals_serialize_without_lock_upgrade_deadlock(
    typed_workbook, monkeypatch
):
    """Distinct approvals serialize before either can upgrade a workbook lock.

    Args:
        typed_workbook: Isolated owned workbook and ledger store.
        monkeypatch: Pause one approval while the other requests its workbook lock.
    """
    from ledger.approvals import decide_approval
    import ledger.typed_cells as typed_cells

    store, workspace, first, _ = typed_workbook
    snapshot = await inspect_workbook(store.pool, 90231, workspace)
    proposals = [
        await prepare_typed_edit(
            store.pool,
            90231,
            TypedEditProposal(
                workbook_id=workspace,
                snapshot=snapshot["snapshot"],
                edits=[input_edit(first, value)],
            ),
            require_approval=True,
        )
        for value in ("1", "2")
    ]
    original_validate = typed_cells.validate_snapshot
    entered = asyncio.Event()
    release = asyncio.Event()
    first_pid = None

    async def pause_first_validation(connection, user_id, request):
        """Retain the first approval's initial lock until a contender waits.

        Args:
            connection: Approval transaction connection.
            user_id: Authenticated owner.
            request: Stored typed proposal.

        Returns:
            Validated workbook snapshot.
        """
        nonlocal first_pid
        if not entered.is_set():
            first_pid = connection.get_server_pid()
            entered.set()
            await release.wait()
        return await original_validate(connection, user_id, request)

    async def wait_for_blocked_approval():
        """Observe a real lock wait rather than relying on task timing."""
        while not await store.pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE $1=ANY(pg_blocking_pids(pid)))",
            first_pid,
        ):
            await asyncio.sleep(0.01)

    monkeypatch.setattr(typed_cells, "validate_snapshot", pause_first_validation)
    approvals = [
        asyncio.create_task(
            decide_approval(store.pool, 90231, proposal["approval_id"], approve=True)
        )
        for proposal in proposals
    ]
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        await asyncio.wait_for(wait_for_blocked_approval(), timeout=5)
    finally:
        release.set()
        decisions = await asyncio.wait_for(
            asyncio.gather(*approvals, return_exceptions=True), timeout=5
        )
    assert all(isinstance(decision, dict) for decision in decisions), decisions
    assert all(decision["approved"] for decision in decisions)
    assert await inspect_workbook(store.pool, 90231, workspace) == snapshot


@pytest.mark.parametrize("commit_first", [False, True])
async def test_ownership_is_rechecked_for_execution_and_replay(
    typed_workbook, commit_first
):
    """Neither a proposal nor a receipt grants access after ownership changes.

    Args:
        typed_workbook: Isolated owned workbook and ledger store.
        commit_first: Whether execution already committed before revocation.
    """
    from ledger.resources import ResourceNotFoundError

    store, workspace, first, _ = typed_workbook
    snapshot = await inspect_workbook(store.pool, 90231, workspace)
    proposal = await prepare_typed_edit(
        store.pool,
        90231,
        TypedEditProposal(
            workbook_id=workspace,
            snapshot=snapshot["snapshot"],
            edits=[input_edit(first, "1")],
        ),
    )
    if commit_first:
        await execute_typed_edit(store.pool, 90231, proposal["operation_ref"])
    await store.pool.execute(
        "UPDATE ledger_spreadsheet SET user_id=$1 WHERE spreadsheet_id=$2",
        90232,
        workspace,
    )
    with pytest.raises(ResourceNotFoundError):
        await execute_typed_edit(store.pool, 90231, proposal["operation_ref"])
    with pytest.raises(ResourceNotFoundError):
        await inspect_workbook(store.pool, 90231, workspace)


@pytest.mark.parametrize("decision", ["rejected", "expired"])
async def test_rejected_or_expired_typed_approval_cannot_commit(
    typed_workbook, decision
):
    """Unusable consent leaves typed state and operation receipts unchanged.

    Args:
        typed_workbook: Isolated owned workbook and ledger store.
        decision: Approval state that must prohibit execution.
    """
    from ledger.approvals import decide_approval

    store, workspace, first, _ = typed_workbook
    before = await inspect_workbook(store.pool, 90231, workspace)
    proposal = await prepare_typed_edit(
        store.pool,
        90231,
        TypedEditProposal(
            workbook_id=workspace,
            snapshot=before["snapshot"],
            edits=[input_edit(first, "2")],
        ),
        require_approval=True,
    )
    if decision == "rejected":
        await decide_approval(store.pool, 90231, proposal["approval_id"], approve=False)
    else:
        await store.pool.execute(
            "UPDATE ledger_table_operation SET approval_expires_at=clock_timestamp()-interval '1 second' WHERE user_id=$1 AND idempotency_key=$2",
            90231,
            proposal["operation_ref"],
        )
    with pytest.raises(ValueError, match=decision):
        await execute_typed_edit(store.pool, 90231, proposal["operation_ref"])
    assert await inspect_workbook(store.pool, 90231, workspace) == before
    assert await store.pool.fetchval(
        "SELECT receipt IS NULL FROM ledger_table_operation WHERE user_id=$1 AND idempotency_key=$2",
        90231,
        proposal["operation_ref"],
    )


async def test_transitive_failure_and_repair_have_actual_calculation_counts(
    typed_workbook,
):
    """A source edit updates dependent failures without inventing invalidations.

    Args:
        typed_workbook: Isolated owned workbook and ledger store.
    """
    store, workspace, first, second = typed_workbook
    await edit(typed_workbook, [input_edit(first, "2")])
    identity = (await inspect_workbook(store.pool, 90231, workspace))["cells"][0][
        "cell_id"
    ]
    quotient = formula_edit(second, identity, "divide")
    quotient["formula"]["operands"] = [{"literal": "1"}, {"cell_id": identity}]
    await edit(typed_workbook, [quotient])
    observed = await inspect_workbook(store.pool, 90231, workspace)
    quotient_id = next(
        cell["cell_id"] for cell in observed["cells"] if cell["expression"] is not None
    )
    dependent = formula_edit(second, quotient_id, "multiply", "3")
    dependent["row"] = 3
    independent = formula_edit(second, identity)
    independent["row"] = 4
    independent["formula"]["operands"] = [{"literal": "1"}, {"literal": "2"}]
    await edit(typed_workbook, [dependent, independent])
    failed = await edit(typed_workbook, [input_edit(first, "0")])
    assert failed["calculation_status"] == "failed"
    assert failed["calculation"]["invalidated"] == 2
    assert failed["calculation"]["recalculated"] == 3
    assert failed["calculation"]["failed"] == 2
    assert {cell["error"] for cell in failed["calculation"]["results"]} == {
        None,
        "dependency_failed",
        "invalid_or_inexact_arithmetic",
    }
    repaired = await edit(typed_workbook, [input_edit(first, "4")])
    assert repaired["calculation_status"] == "current"
    assert repaired["calculation"]["failed"] == 0
    assert (await store.get_snapshot(workspace, "Totals")).values[1:] == [
        [0.25],
        [0.75],
        [3.0],
    ]


async def test_typed_metadata_and_formulas_survive_a_new_store(typed_workbook):
    """Reopening the store retains identities, raw types, formulas and exact caches."""
    store, workspace, first, second = typed_workbook
    await edit(typed_workbook, [input_edit(first, "1.234567890123456789")])
    input_id = (await inspect_workbook(store.pool, 90231, workspace))["cells"][0][
        "cell_id"
    ]
    await edit(typed_workbook, [formula_edit(second, input_id)])
    before = await inspect_workbook(store.pool, 90231, workspace)
    reopened = LedgerStore(POSTGRES_TEST_URL)
    await reopened.connect()
    try:
        assert await inspect_workbook(reopened.pool, 90231, workspace) == before
    finally:
        await reopened.close()


async def test_failed_formulas_block_registered_calculations(typed_workbook):
    """A failed null cache cannot silently become a zero sum in either query path."""
    from ledger.catalogue import CatalogueStore
    from ledger.calculations import CheckedCalculation
    from ledger.financial_contracts import CheckedFinancialRequest
    from ledger.resources import TableRegistration

    store, workspace, first, second = typed_workbook
    await edit(typed_workbook, [input_edit(first, "1")])
    identity = (await inspect_workbook(store.pool, 90231, workspace))["cells"][0][
        "cell_id"
    ]
    await edit(typed_workbook, [formula_edit(second, identity, "divide", "0")])
    catalogue = CatalogueStore(store.pool)
    revision = await store.pool.fetchval(
        "SELECT revision FROM ledger_sheet WHERE sheet_id=$1", second
    )
    table = await catalogue.register(
        workspace,
        TableRegistration(
            sheet_id=second,
            expected_sheet_revision=revision,
            table_range="A1:A2",
            name="Totals",
        ),
    )
    with pytest.raises(ValueError, match="failed or pending"):
        await catalogue.calculate_owned(
            90231,
            CheckedCalculation(
                table_id=table["table_id"],
                expected_sheet_revision=revision,
                expected_catalogue_revision=1,
                metrics=[{"column": "Total", "operation": "sum"}],
            ),
        )
    with pytest.raises(ValueError, match="failed or pending"):
        await catalogue.financial_owned(
            90231,
            CheckedFinancialRequest(
                table_id=table["table_id"],
                sources=[
                    {
                        "table_id": table["table_id"],
                        "sheet_revision": revision,
                        "catalogue_revision": 1,
                    }
                ],
                query={"operation": "records", "columns": ["Total"]},
            ),
        )


async def test_managed_sheet_layout_is_protected_and_rename_preserves_dependencies(
    typed_workbook,
):
    """Stable sheet/cell identities survive a rename but forbid ordinary movement."""
    from ledger import grid

    store, workspace, first, second = typed_workbook
    await edit(typed_workbook, [input_edit(first, "1")])
    identity = (await inspect_workbook(store.pool, 90231, workspace))["cells"][0][
        "cell_id"
    ]
    await edit(typed_workbook, [formula_edit(second, identity)])
    with pytest.raises(asyncpg.CheckViolationError):
        await store.delete_sheet(workspace, "Inputs")
    with pytest.raises(asyncpg.CheckViolationError):
        await store.mutate_grid(
            workspace, "Inputs", lambda rows: grid.write_range(rows, "A3", [[2]])
        )
    await store.rename_sheet(workspace, "Inputs", "Renamed")
    await edit(typed_workbook, [input_edit(first, "2")])
    assert (await store.get_snapshot(workspace, "Totals")).values[1][0] == 2.2


async def test_projection_failure_rolls_back_typed_state_and_receipt(
    typed_workbook, monkeypatch
):
    """No typed declaration remains after a failed grid projection."""
    import ledger.typed_cells as typed_cells

    store, workspace, first, _ = typed_workbook
    before = await inspect_workbook(store.pool, 90231, workspace)

    async def fail_projection(connection, cells):
        """Fail after typed state has been staged in the transaction."""
        raise RuntimeError("Projection failed")

    monkeypatch.setattr(typed_cells, "project_cells", fail_projection)
    with pytest.raises(RuntimeError, match="Projection failed"):
        await edit(typed_workbook, [input_edit(first, "9")])
    assert await inspect_workbook(store.pool, 90231, workspace) == before


async def test_edit_cannot_commit_a_workbook_too_large_to_inspect(typed_workbook):
    """Evidence limits cannot leave a committed workbook without an edit token.

    Args:
        typed_workbook: Isolated owned workbook and ledger store.
    """
    store, workspace, first, _ = typed_workbook
    for first_row in (2, 34, 66):
        before = await inspect_workbook(store.pool, 90231, workspace)
        edits = [
            {
                "action": "set_input",
                "sheet_id": first,
                "row": row,
                "column": 1,
                "input": {"kind": "text", "value": "x" * 512},
            }
            for row in range(first_row, first_row + 32)
        ]
        if first_row < 66:
            await edit(typed_workbook, edits)
        else:
            proposal = await prepare_typed_edit(
                store.pool,
                90231,
                TypedEditProposal(
                    workbook_id=workspace, snapshot=before["snapshot"], edits=edits
                ),
            )
            with pytest.raises(ValueError, match="Evidence exceeds"):
                await execute_typed_edit(store.pool, 90231, proposal["operation_ref"])
            assert await inspect_workbook(store.pool, 90231, workspace) == before
            assert await store.pool.fetchval(
                "SELECT receipt IS NULL FROM ledger_table_operation WHERE user_id=$1 AND idempotency_key=$2",
                90231,
                proposal["operation_ref"],
            )
