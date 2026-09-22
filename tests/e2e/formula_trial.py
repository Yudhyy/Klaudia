"""Shared formula HTTP workflows without live-test environment binding."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
import sys
from uuid import uuid4

import httpx

from ledger.catalogue import CatalogueStore
from ledger.resources import TableRegistration
from ledger.store import LedgerStore
from ledger.typed_storage import read_snapshot
from tests.e2e.checks import ResponseView, evaluate
from tests.e2e.formula_cases import (
    check_formula_cells,
    check_formula_grids,
    formula_prompt,
    formula_expectation,
)
from tests.e2e.schema import Expect


@dataclass(frozen=True)
class FormulaTrial:
    """Fresh identities and server-owned storage for one acceptance workflow."""

    store: LedgerStore
    user_id: int
    target: str
    distractor: str
    sheets: tuple[int, int]
    scenario: str


@asynccontextmanager
async def formula_workbooks(store, user_id):
    """Create a target and active distractor with cleanup limited to their IDs.

    Args:
        store: Connected isolated ledger.
        user_id: Fresh test owner.

    Yields:
        Target, distractor and sheet identities.
    """
    workbooks = []
    try:
        for name in ("Formula acceptance", "Formula distractor"):
            workbook = await store.create_spreadsheet(user_id, name)
            workspace = workbook["spreadsheetId"]
            workbooks.append(workspace)
            inputs = await store.create_sheet(
                workspace,
                "Inputs",
                [
                    ["Amount", "Code", "Approved", "Date", "Large", "Fraction"],
                    [None] * 6,
                ],
            )
            totals = await store.create_sheet(workspace, "Totals", [["Total"], [None]])
            await CatalogueStore(store.pool).register(
                workspace,
                TableRegistration(
                    sheet_id=inputs["sheetId"],
                    expected_sheet_revision=0,
                    table_range="A1:F2",
                    name=name,
                ),
            )
            if len(workbooks) == 1:
                sheets = (inputs["sheetId"], totals["sheetId"])
        yield workbooks[0], workbooks[1], sheets
    finally:
        for workspace in reversed(workbooks):
            await store.delete_spreadsheet(workspace)


async def observe_workbook(pool, user_id, workbook_id):
    """Read full grids and typed state outside the model's bounded tool evidence.

    Args:
        pool: Isolated ledger connections.
        user_id: Fixture owner.
        workbook_id: Fixture workbook identity.

    Returns:
        Exact database snapshot including complete grid text.
    """
    async with pool.acquire() as connection:
        return await read_snapshot(connection, user_id, workbook_id)


async def post_evidence(client, request, observed):
    """Retain an HTTP response before asserting status so failures remain visible.

    Args:
        client: Authenticated test client.
        request: Route and optional JSON payload.
        observed: Current trial evidence.

    Returns:
        Decoded successful API response.
    """
    route, payload = request
    response = await client.post(route, json=payload)
    observed.setdefault("responses", []).append(
        {
            "route": route,
            "request": payload,
            "status_code": response.status_code,
            "body": response.text,
        }
    )
    assert response.status_code == 200, response.text
    return response.json()


async def run_formula_trial(
    client: httpx.AsyncClient, fixture: FormulaTrial, observed: dict[str, Any]
) -> None:
    """Execute fixed user turns, grade state and repeat completed task recovery.

    Args:
        client: Authenticated HTTP client using the real application services.
        fixture: Store, owner, workbook, sheet and scenario identities.
        observed: Mutable evidence for this independent trial.
    """
    store, user_id = fixture.store, fixture.user_id
    target, distractor = fixture.target, fixture.distractor
    sheets, scenario = fixture.sheets, fixture.scenario
    distractor_before = await observe_workbook(store.pool, user_id, distractor)
    stages = {
        "discovery": ("literals",),
        "recalculation": ("inputs", "created", "edited"),
        "failure_repair": ("inputs", "failed", "repaired"),
        "approval_resume": ("inputs",),
    }[scenario]
    session_id = None
    for stage in stages:
        before = await observe_workbook(store.pool, user_id, target)
        request = {
            "messages": [{"role": "user", "content": formula_prompt(stage)}],
            "spreadsheet_id": distractor,
        }
        if session_id is not None:
            request["session_id"] = session_id
            request["request_key"] = uuid4().hex
        body = await post_evidence(client, ("/v1/chat", request), observed)
        session_id = body["session_id"]
        if scenario == "approval_resume":
            assert body["run_status"] == "awaiting_approval", body
            assert await observe_workbook(store.pool, user_id, target) == before
            assert len(body["pending_approvals"]) == 1
            approval = body["pending_approvals"][0]
            decision = await post_evidence(
                client,
                (f"/v1/approvals/{approval['approval_id']}", {"decision": "approve"}),
                observed,
            )
            body = await post_evidence(
                client, (f"/v1/tasks/{body['task_id']}/resume", None), observed
            )
            assert body["operation_receipts"] == [decision["result"]]
        assert body["runtime"] == "main"
        assert body["run_status"] == "answered", body
        snapshot = await observe_workbook(store.pool, user_id, target)
        observed.setdefault("snapshots", []).append(snapshot)
        check_formula_grids(before, snapshot, stage)
        assert (
            await observe_workbook(store.pool, user_id, distractor) == distractor_before
        )
        if stage == "literals":
            check_literals(snapshot["cells"], sheets[0])
        else:
            check_formula_cells(snapshot["cells"], sheets, stage)
        receipts = body["operation_receipts"]
        if stage != "literals":
            total = next(
                (cell for cell in snapshot["cells"] if cell["sheet_id"] == sheets[1]),
                None,
            )
            expected = formula_expectation(
                target,
                sheets[0] if stage in ("inputs", "edited") else sheets[1],
                total["cell_id"] if total else None,
                total["calculated_value"] if total else None,
            )
            expectations = [expected]
        else:
            assert receipts
            expectations = [formula_expectation(target, sheets[0]) for _ in receipts]
        check_receipts(body, expectations)
        for _ in range(2):
            replay = await post_evidence(
                client, (f"/v1/tasks/{body['task_id']}/resume", None), observed
            )
            assert replay["operation_receipts"] == receipts
            assert await observe_workbook(store.pool, user_id, target) == snapshot
            assert (
                await observe_workbook(store.pool, user_id, distractor)
                == distractor_before
            )
        observed.setdefault("final_answers", []).append(
            {"stage": stage, "text": body["message"]["content"]}
        )
    observed["prose_review"] = "pending human or separate semantic review"


def check_receipts(body: dict[str, Any], expectations: list[dict[str, Any]]) -> None:
    """Require exact ordered, distinct receipts with the shared strict grader.

    Args:
        body: Actual HTTP chat response.
        expectations: Independently checked calculation expectations.

    Raises:
        AssertionError: Receipt evidence differs from the expected operations.
    """
    grade = evaluate(
        Expect(formula_receipts=expectations),
        ResponseView(
            body["message"]["content"],
            body["tools_used"],
            0,
            operation_receipts=body["operation_receipts"],
        ),
    )
    assert grade.passed, grade


async def retain_final_state(fixture: FormulaTrial, observed: dict[str, Any]) -> None:
    """Preserve both workbook snapshots before cleanup, even after a failed turn.

    Args:
        fixture: Fresh trial identities and ledger.
        observed: Mutable trial evidence.

    Raises:
        Exception: State observation failed without an earlier trial error.
    """
    pending_error = sys.exc_info()[0] is not None
    failures = []
    for name, workspace in (
        ("target", fixture.target),
        ("distractor", fixture.distractor),
    ):
        try:
            observed.setdefault("final_state", {})[name] = await observe_workbook(
                fixture.store.pool, fixture.user_id, workspace
            )
        except Exception as exc:
            observed.setdefault("observation_errors", {})[name] = type(exc).__name__
            failures.append(exc)
    if failures and not pending_error:
        raise failures[0]


def check_literals(cells, sheet_id):
    """Require exact requested types and literals at all six managed positions.

    Args:
        cells: Persisted typed cells.
        sheet_id: Expected input sheet.
    """
    expected = [
        ("decimal", "+001.2300"),
        ("text", "00123"),
        ("boolean", True),
        ("date", "2026-09-21"),
        ("decimal", "9007199254740993"),
        ("decimal", "0.123456789012345678901234567890"),
    ]
    assert len(cells) == len(expected)
    for column, (kind, value) in enumerate(expected, 1):
        cell = next(
            cell
            for cell in cells
            if (cell["sheet_id"], cell["row_number"], cell["column_number"])
            == (sheet_id, 2, column)
        )
        assert cell["kind"] == kind
        assert type(cell["raw_value"]) is type(value)
        assert cell["raw_value"] == value
        assert cell["expression"] is None
