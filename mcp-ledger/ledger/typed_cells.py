"""Checked typed edits, persistent dependencies and atomic recalculation receipts."""

from copy import deepcopy
import hashlib
import json
from typing import Any
from uuid import uuid4

import asyncpg

from ledger import grid
from ledger.connections import ConnectionProvider
from ledger.approvals import check_approval, prepare_approval
from ledger.errors import IdempotencyConflictError, RevisionConflictError
from ledger.evidence import bounded_evidence
from ledger.formula_engine import (
    ENGINE_VERSION,
    MAX_TYPED_CELLS,
    Formula,
    evaluate_graph,
)
from ledger.resources import ResourceNotFoundError
from ledger.typed_contracts import FormulaEdit, InputEdit, TypedEditProposal, TypedValue
from ledger.typed_storage import lock_workbook, project_cells, read_snapshot, save_cell


async def inspect_workbook(
    pool: ConnectionProvider, user_id: int, workspace: str
) -> dict[str, Any]:
    """Return owned typed state and a fingerprint from one SQL snapshot.

    Args:
        pool: Ledger connection provider.
        user_id: Authenticated owner.
        workspace: Selected workbook identity.

    Returns:
        Bounded typed cells and source revisions, without unrelated grid values.

    Raises:
        ResourceNotFoundError: The workbook is absent or foreign.
        ValueError: Source or evidence exceeds synchronous budgets.
    """
    async with pool.acquire() as connection:
        snapshot = await read_snapshot(connection, user_id, workspace)
    return inspection_evidence(snapshot)


def inspection_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build bounded evidence that can supply the next edit's snapshot token.

    Args:
        snapshot: Owned source state, including grids used only for fingerprinting.

    Returns:
        Typed state and source revisions without unrelated grid contents.

    Raises:
        ValueError: The workbook cannot fit the inspection response budget.
    """
    return bounded_evidence(
        {
            **snapshot,
            "sheets": [
                {key: value for key, value in sheet.items() if key != "grid"}
                for sheet in snapshot["sheets"]
            ],
            "engine_version": ENGINE_VERSION,
        }
    )


def apply_edits(
    snapshot: dict[str, Any], edits: list[InputEdit | FormulaEdit]
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Plan typed declarations without changing persisted values or identities.

    Args:
        snapshot: Locked workbook state.
        edits: Exact requested input or formula changes.

    Returns:
        Planned cells and directly changed identities.

    Raises:
        ValueError: The edit changes a computed cell as an input or exceeds limits.
        ResourceNotFoundError: A target sheet is outside the owned workbook.
    """
    cells = {cell["cell_id"]: deepcopy(cell) for cell in snapshot["cells"]}
    positions = {
        (cell["sheet_id"], cell["row_number"], cell["column_number"]): cell["cell_id"]
        for cell in cells.values()
    }
    sheets = {sheet["sheet_id"] for sheet in snapshot["sheets"]}
    changed = set()
    for edit in edits:
        if edit.sheet_id not in sheets:
            raise ResourceNotFoundError("Target sheet not found")
        for table in snapshot["tables"]:
            first_row, first_column, _, last_column = grid.parse_range(
                table["table_range"]
            )
            if (
                table["sheet_id"] == edit.sheet_id
                and edit.row == first_row + 1
                and first_column < edit.column <= last_column + 1
            ):
                raise ValueError("Typed edits cannot change registered table headers")
        identity = positions.get(
            (edit.sheet_id, edit.row, edit.column), "cell_" + uuid4().hex
        )
        old = cells.get(identity)
        if isinstance(edit, InputEdit) and old and old["expression"] is not None:
            raise ValueError("Input edits cannot replace computed cells")
        formula = edit.formula if isinstance(edit, FormulaEdit) else None
        declared = edit.input if isinstance(edit, InputEdit) else None
        cells[identity] = {
            "cell_id": identity,
            "sheet_id": edit.sheet_id,
            "row_number": edit.row,
            "column_number": edit.column,
            "kind": declared.kind if declared else "decimal",
            "raw_value": declared.value if declared else None,
            "unit": declared.unit if declared else edit.unit,
            "display_format": declared.display_format
            if declared
            else edit.display_format,
            "expression": formula.model_dump() if formula else None,
            "dependencies": list(formula.dependencies()) if formula else [],
            "calculated_value": None,
            "calculation_status": "pending" if formula else "input",
            "calculation_error": None,
            "engine_version": ENGINE_VERSION if formula else None,
        }
        changed.add(identity)
    if len(cells) > MAX_TYPED_CELLS:
        raise ValueError("Typed workbook exceeds 256 managed cells")
    return cells, changed


def calculate_cells(
    cells: dict[str, dict[str, Any]], changed: set[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Validate inputs and evaluate the complete bounded dependency graph.

    Args:
        cells: Planned typed state.
        changed: Directly changed identities.

    Returns:
        Calculated state and actual invalidation, calculation and failure evidence.

    Raises:
        ValueError: Inputs, dependencies, cycles or stored formula policies are invalid.
    """
    cells = deepcopy(cells)
    inputs, formulas = {}, {}
    for identity, cell in cells.items():
        if cell["expression"] is None:
            declared = TypedValue(
                kind=cell["kind"],
                value=cell["raw_value"],
                unit=cell["unit"],
                display_format=cell["display_format"],
            )
            if declared.kind == "decimal":
                inputs[identity] = declared.value
        else:
            expression = Formula.model_validate(cell["expression"])
            if expression.rounding is None:
                raise ValueError("Stored formula lacks financial rounding policy")
            formulas[identity] = expression
    invalidated = set(changed)
    while True:
        affected = {
            identity
            for identity, formula in formulas.items()
            if set(formula.dependencies()) & invalidated
        }
        if affected <= invalidated:
            break
        invalidated.update(affected)
    calculated = evaluate_graph(inputs, formulas)
    for identity, evidence in calculated.items():
        cells[identity].update(
            calculated_value=evidence["value"],
            calculation_status=evidence["status"],
            calculation_error=evidence["error"],
            engine_version=ENGINE_VERSION,
        )
    return cells, {
        "engine_version": ENGINE_VERSION,
        "invalidated": len(invalidated & formulas.keys()),
        "recalculated": len(calculated),
        "failed": sum(value["status"] == "failed" for value in calculated.values()),
        "results": [
            {"cell_id": identity, **value} for identity, value in calculated.items()
        ],
    }


async def validate_snapshot(
    connection: asyncpg.Connection, user_id: int, request: TypedEditProposal
) -> dict[str, Any]:
    """Lock every source and compare its state with the inspected fingerprint.

    Args:
        connection: Active proposal or execution transaction.
        user_id: Authenticated owner.
        request: Exact stored edit intent.

    Returns:
        The matching locked workbook snapshot.

    Raises:
        RevisionConflictError: Any source or typed declaration changed.
        ResourceNotFoundError: Current ownership is absent.
    """
    snapshot = await lock_workbook(connection, user_id, request.workbook_id)
    if snapshot["snapshot"] != request.snapshot:
        raise RevisionConflictError("Typed workbook changed; inspect it again")
    return snapshot


async def prepare_typed_edit(
    pool: ConnectionProvider,
    user_id: int,
    request: TypedEditProposal,
    *,
    require_approval: bool = False,
) -> dict[str, Any]:
    """Persist exact validated intent using the existing operation and approval store.

    Args:
        pool: Ledger connections.
        user_id: Authenticated owner.
        request: Edit batch and observed fingerprint.
        require_approval: Server-selected approval policy.

    Returns:
        A fresh durable operation reference for all execution retries.

    Raises:
        ValueError: Intent, dependency graph or payload is invalid.
        RevisionConflictError: Observed state changed.
        ResourceNotFoundError: Current ownership is absent.
    """
    request = request.model_copy(deep=True)
    payload = json.dumps(request.model_dump(), sort_keys=True, ensure_ascii=False)
    if len(payload.encode()) > 65536:
        raise ValueError("Typed proposal exceeds 65536 bytes")
    reference = "typed:" + uuid4().hex
    async with pool.acquire() as connection:
        async with connection.transaction():
            snapshot = await validate_snapshot(connection, user_id, request)
            cells, changed = apply_edits(snapshot, request.edits)
            calculate_cells(cells, changed)
            await connection.execute(
                "INSERT INTO ledger_table_operation(user_id,idempotency_key,fingerprint,workspace,request_payload) VALUES($1,$2,$3,$4,$5)",
                user_id,
                reference,
                hashlib.sha256(payload.encode()).hexdigest(),
                request.workbook_id,
                payload,
            )
            approval = await prepare_approval(
                connection, (user_id, reference), require_approval
            )
    return {
        "operation_ref": reference,
        "status": "prepared",
        **({"approval_id": approval} if approval else {}),
    }


async def commit_cells(
    connection: asyncpg.Connection, request: TypedEditProposal, snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Persist invalidation, recalculation and the grid projection in one transaction.

    Args:
        connection: Locked execution transaction.
        request: Stored exact edit request.
        snapshot: Checked original source state.

    Returns:
        A receipt body ready to store before transaction commit.

    Raises:
        ValueError: The typed graph or resulting source state exceeds its limits.
    """
    cells, changed = apply_edits(snapshot, request.edits)
    cells, calculation = calculate_cells(cells, changed)
    stored = [
        cell
        for identity, cell in cells.items()
        if identity in changed or cell["expression"] is not None
    ]
    for cell in stored:
        if cell["expression"] is not None:
            pending = {
                **cell,
                "calculated_value": None,
                "calculation_status": "pending",
                "calculation_error": None,
            }
            await save_cell(connection, pending)
    await connection.execute("SELECT set_config('ledger.typed_write','on',true)")
    for cell in stored:
        await save_cell(connection, cell)
    revisions = await project_cells(connection, stored)
    await connection.execute("SELECT set_config('ledger.typed_write','off',true)")
    return {
        "operation_id": "op_" + uuid4().hex,
        "status": "committed",
        "operation_type": "edit_typed_cells",
        "target": {
            "spreadsheet_id": request.workbook_id,
            "sheet_id": request.edits[0].sheet_id,
        },
        "changed_sheets": [
            {"sheet_id": identity, "after_revision": revision}
            for identity, revision in revisions.items()
        ],
        "before_revisions": [
            {"sheet_id": sheet["sheet_id"], "revision": sheet["revision"]}
            for sheet in snapshot["sheets"]
        ],
        "changes": {
            "inputs_or_formulas_edited": len(changed),
            "cell_ids": sorted(changed),
        },
        "calculation_status": "failed"
        if calculation["failed"]
        else "current"
        if calculation["recalculated"]
        else "not_required",
        "calculation": calculation,
        "catalogue_status": "refresh_required"
        if snapshot["tables"]
        else "not_applicable",
        "accounting_validation": "not_run",
    }


async def execute_typed_edit(
    pool: ConnectionProvider, user_id: int, reference: str
) -> dict[str, Any]:
    """Execute or replay an owned proposal with an atomic calculation receipt.

    Args:
        pool: Ledger connections or durable task connection adapter.
        user_id: Authenticated owner.
        reference: Original prepared operation identity.

    Returns:
        The committed receipt, unchanged on replay.

    Raises:
        ResourceNotFoundError: Proposal or current ownership is absent.
        RevisionConflictError: The uncommitted snapshot changed.
        ValueError: Approval or calculation contract is invalid.
        IdempotencyConflictError: Persisted intent changed after preparation.
    """
    async with pool.acquire() as connection:
        async with connection.transaction():
            operation = await connection.fetchrow(
                "SELECT * FROM ledger_table_operation WHERE user_id=$1 AND idempotency_key=$2 FOR UPDATE",
                user_id,
                reference,
            )
            if operation is None:
                raise ResourceNotFoundError("Operation not found")
            owned = await connection.fetchval(
                "SELECT spreadsheet_id FROM ledger_spreadsheet WHERE spreadsheet_id=$1 AND user_id=$2 FOR UPDATE",
                operation["workspace"],
                user_id,
            )
            if owned is None:
                raise ResourceNotFoundError("Operation not found")
            if operation["receipt"] is not None:
                return json.loads(operation["receipt"])
            if (
                hashlib.sha256(operation["request_payload"].encode()).hexdigest()
                != operation["fingerprint"]
            ):
                raise IdempotencyConflictError("Stored typed proposal changed")
            request = TypedEditProposal.model_validate_json(
                operation["request_payload"]
            )
            snapshot = await validate_snapshot(connection, user_id, request)
            await check_approval(connection, operation)
            receipt = await commit_cells(connection, request, snapshot)
            inspection_evidence(
                await read_snapshot(connection, user_id, request.workbook_id)
            )
            receipt["operation_ref"] = reference
            bounded_evidence(receipt)
            await connection.execute(
                "UPDATE ledger_table_operation SET receipt=$3::jsonb WHERE user_id=$1 AND idempotency_key=$2",
                user_id,
                reference,
                json.dumps(receipt),
            )
            return receipt
