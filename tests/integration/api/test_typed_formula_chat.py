"""Main chat retains typed operation receipts through approvals and durable replay."""

import json

import pytest
from langchain_core.messages import AIMessage

from app.services.core.main_chat import MainChatService
from app.services.core.operations import OperationService
from app.services.workflow.store import TaskSession
from ledger.typed_cells import inspect_workbook
from tests.e2e.capability_cases import CapabilityFixture
from tests.e2e.checks import ResponseView, evaluate
from tests.e2e.schema import Expect
from tests.integration.api.test_durable_tasks import durable_client, approval_client  # noqa: F401
from tests.integration.api.test_main_chat_routes import main_chat_client  # noqa: F401
from tests.unit.test_main_agent import ScriptedModel, call


class TypedChatModel(ScriptedModel):
    """Use actual typed snapshots to declare an input, formula and later input edit."""

    def __init__(self, workspace, sheets, *, operations=3):
        """Retain fixture destinations and the requested workflow length."""
        self.workspace = workspace
        self.sheets = sheets
        self.stage = 0
        self.operations = operations
        self.formula_operation = "add"
        self.formula_literal = "0.2"
        self.input_value = {"kind": "decimal", "value": "0.1", "unit": "USD"}
        super().__init__([call("inspect_typed_workbook", {"workbook_id": workspace})])

    async def ainvoke(self, messages, config=None):
        """Choose the next typed operation from actual returned evidence."""
        previous = messages[-1]
        if previous.type != "tool":
            return await super().ainvoke(messages, config)
        self.inputs.append(list(messages))
        evidence = json.loads(previous.content)
        if previous.name == "inspect_typed_workbook":
            edit = {
                "action": "set_input",
                "sheet_id": self.sheets[0],
                "row": 2,
                "column": 1,
                "input": self.input_value
                if self.stage == 0
                else {"kind": "decimal", "value": "0.3", "unit": "USD"},
            }
            if self.stage == 1:
                identity = next(
                    cell["cell_id"]
                    for cell in evidence["cells"]
                    if cell["sheet_id"] == self.sheets[0]
                )
                edit = {
                    "action": "set_formula",
                    "sheet_id": self.sheets[1],
                    "row": 2,
                    "column": 1,
                    "unit": "USD",
                    "formula": {
                        "operation": self.formula_operation,
                        "operands": [
                            {"cell_id": identity},
                            {"literal": self.formula_literal},
                        ],
                        "rounding": {"places": 2, "mode": "ROUND_HALF_EVEN"},
                    },
                }
            return call(
                "prepare_typed_edit",
                {
                    "workbook_id": self.workspace,
                    "snapshot": evidence["snapshot"],
                    "edits": [edit],
                },
                f"prepare-{self.stage}",
            )
        if previous.name == "prepare_typed_edit":
            return call(
                "execute_operation",
                {"operation_ref": evidence["operation_ref"]},
                f"execute-{self.stage}",
            )
        if previous.name == "execute_operation":
            self.stage += 1
            if self.stage < self.operations:
                return call(
                    "inspect_typed_workbook",
                    {"workbook_id": self.workspace},
                    f"inspect-{self.stage}",
                )
        return AIMessage(content="Typed edits committed with their calculation status.")


async def install_model(fixture, *, operations=3):
    """Create workbook sheets and use a deterministic typed-edit model."""
    store = fixture.fixture.store
    first = await store.create_sheet(fixture.active_id, "Inputs", [["Amount"], [0]])
    second = await store.create_sheet(fixture.active_id, "Totals", [["Total"], [None]])
    fixture.container.main_chat._model = TypedChatModel(
        fixture.active_id, (first["sheetId"], second["sheetId"]), operations=operations
    )
    fixture.request["messages"] = [
        {
            "role": "user",
            "content": "Declare the typed input and native formula with explicit half-even monetary rounding.",
        }
    ]


@pytest.mark.parametrize("streaming", [False, True])
async def test_durable_chat_recalculates_native_cross_sheet_formula(
    durable_client,  # noqa: F811
    streaming,
):
    """A complete main-agent task records each actual calculation outcome."""
    fixture = durable_client
    await install_model(fixture)
    other_workbook_before = await fixture.fixture.observe_state()
    response = await fixture.client.post(
        "/v1/chat/stream" if streaming else "/v1/chat",
        json=fixture.request,
        headers=fixture.headers,
    )
    assert response.status_code == 200, response.text
    body = (
        next(
            json.loads(frame.split("data: ", 1)[1])
            for frame in response.text.split("\n\n")
            if frame.startswith("event: done")
        )
        if streaming
        else response.json()
    )
    assert body["run_status"] == "answered"
    receipts = body["operation_receipts"]
    snapshot = await inspect_workbook(
        fixture.fixture.store.pool, fixture.user_id, fixture.active_id
    )
    first, second = fixture.container.main_chat._model.sheets
    cells = {cell["sheet_id"]: cell for cell in snapshot["cells"]}
    assert len(snapshot["cells"]) == 2
    assert set(cells) == {first, second}
    assert all(
        (cell["row_number"], cell["column_number"]) == (2, 1) for cell in cells.values()
    )
    assert cells[first]["raw_value"] == "0.3"
    assert cells[first]["kind"] == "decimal"
    assert cells[second]["dependencies"] == [cells[first]["cell_id"]]
    assert cells[second]["expression"] == {
        "operation": "add",
        "operands": [{"cell_id": cells[first]["cell_id"]}, {"literal": "0.2"}],
        "rounding": {"places": 2, "mode": "ROUND_HALF_EVEN"},
    }
    assert cells[second]["calculated_value"] == "0.50"
    assert cells[second]["calculation_status"] == "current"
    expectations = [
        formula_expectation(fixture.active_id, first),
        formula_expectation(
            fixture.active_id, second, cells[second]["cell_id"], "0.30"
        ),
        formula_expectation(fixture.active_id, first, cells[second]["cell_id"], "0.50"),
    ]
    expected_grids = {
        "Inputs": [["Amount"], [0.3]],
        "Totals": [["Total"], [0.5]],
    }
    workbook = CapabilityFixture(
        fixture.fixture.store, fixture.active_id, fixture.fixture.case
    )
    grade = evaluate(
        Expect(formula_receipts=expectations, ledger_state=expected_grids),
        ResponseView(
            content=body["content"] if streaming else body["message"]["content"],
            tools_used=body["tools_used"],
            latency_ms=0,
            operation_receipts=receipts,
            ledger_state=await workbook.observe_state(),
        ),
    )
    assert grade.passed, grade
    assert await fixture.fixture.observe_state() == other_workbook_before
    fixture.container.main_chat._model = ScriptedModel([])
    replay = await fixture.client.post(
        f"/v1/tasks/{body['task_id']}/resume", headers=fixture.headers
    )
    assert replay.json()["operation_receipts"] == receipts


async def test_formula_commit_survives_lost_checkpoint(durable_client, monkeypatch):  # noqa: F811
    """A new agent service replays a committed formula without changing revisions.

    Args:
        durable_client: Authenticated chat fixture with real task storage.
        monkeypatch: Scoped checkpoint failure injection.
    """
    fixture = durable_client
    await install_model(fixture, operations=2)
    original_save = TaskSession.save

    async def lose_formula_checkpoint(self, state):
        """Fail only after the formula operation has committed.

        Args:
            self: Active durable task session.
            state: Agent checkpoint containing observed operation receipts.

        Raises:
            ConnectionError: The simulated checkpoint store is unavailable.
        """
        if len(state["receipts"]) == 2:
            raise ConnectionError("Lost formula receipt checkpoint")
        await original_save(self, state)

    monkeypatch.setattr(TaskSession, "save", lose_formula_checkpoint)
    failed = await fixture.client.post(
        "/v1/chat", json=fixture.request, headers=fixture.headers
    )
    assert failed.status_code == 200, failed.text
    outcome = failed.json()
    assert outcome["run_status"] == "failed"
    assert len(outcome["operation_receipts"]) == 2
    assert outcome["operation_receipts"][-1]["calculation_status"] == "current"
    store = fixture.fixture.store
    committed = await inspect_workbook(store.pool, fixture.user_id, fixture.active_id)
    monkeypatch.setattr(TaskSession, "save", original_save)
    fixture.container.main_chat = MainChatService(
        ScriptedModel([AIMessage(content="The original formula commit is confirmed.")]),
        fixture.container.main_chat._catalogue,
        fixture.container.db_client,
        operations=OperationService(store),
        tasks=fixture.container.tasks,
    )
    resumed = await fixture.client.post(
        f"/v1/tasks/{outcome['task_id']}/resume", headers=fixture.headers
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["run_status"] == "answered"
    assert resumed.json()["operation_receipts"] == outcome["operation_receipts"]
    assert (
        await inspect_workbook(store.pool, fixture.user_id, fixture.active_id)
        == committed
    )


async def test_chat_receipt_distinguishes_commit_from_calculation_failure(
    durable_client,  # noqa: F811
):
    """Successful execution retains failed calculation evidence through HTTP.

    Args:
        durable_client: Authenticated chat fixture with real task storage.
    """
    fixture = durable_client
    await install_model(fixture, operations=2)
    fixture.container.main_chat._model.formula_operation = "divide"
    fixture.container.main_chat._model.formula_literal = "0"
    response = await fixture.client.post(
        "/v1/chat", json=fixture.request, headers=fixture.headers
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["operation_receipts"][-1]
    assert receipt["status"] == "committed"
    assert receipt["calculation_status"] == "failed"
    assert receipt["calculation"]["failed"] == 1
    assert receipt["calculation"]["results"][0]["value"] is None
    assert receipt["accounting_validation"] == "not_run"
    snapshot = await inspect_workbook(
        fixture.fixture.store.pool, fixture.user_id, fixture.active_id
    )
    first, second = fixture.container.main_chat._model.sheets
    computed = next(cell for cell in snapshot["cells"] if cell["sheet_id"] == second)
    assert computed["calculated_value"] is None
    assert computed["calculation_status"] == "failed"
    expected = formula_expectation(fixture.active_id, second, computed["cell_id"], None)
    grade = evaluate(
        Expect(
            formula_receipts=[formula_expectation(fixture.active_id, first), expected]
        ),
        ResponseView(
            "", [], 0, operation_receipts=response.json()["operation_receipts"]
        ),
    )
    assert grade.passed, grade


def formula_expectation(workbook_id, sheet_id, cell_id=None, value=None):
    """Describe independent exact outcomes for this fixture's single formula.

    Args:
        workbook_id: Expected operation destination.
        sheet_id: Expected first edited sheet.
        cell_id: Persisted formula identity, absent for input declaration.
        value: Expected exact result, or None for arithmetic failure.

    Returns:
        Complete calculation evidence expected by the shared acceptance grader.
    """
    status = (
        "not_required" if cell_id is None else "failed" if value is None else "current"
    )
    return {
        "workbook_id": workbook_id,
        "sheet_id": sheet_id,
        "calculation_status": status,
        "calculation": {
            "engine_version": "native-decimal-v1",
            "invalidated": int(cell_id is not None),
            "recalculated": int(cell_id is not None),
            "failed": int(status == "failed"),
            "results": []
            if cell_id is None
            else [
                {
                    "cell_id": cell_id,
                    "status": status,
                    "value": value,
                    "error": "invalid_or_inexact_arithmetic"
                    if status == "failed"
                    else None,
                }
            ],
        },
    }


@pytest.mark.parametrize(
    "kind,raw_value",
    [
        ("decimal", "+001.2300"),
        ("decimal", "9007199254740993"),
        ("decimal", "0.123456789012345678901234567890"),
        ("text", "00123"),
        ("boolean", True),
        ("date", "2026-09-21"),
    ],
)
async def test_chat_preserves_declared_input_type_and_literal(
    durable_client,  # noqa: F811
    kind,
    raw_value,
):
    """HTTP tool execution and task replay preserve typed literals exactly.

    Args:
        durable_client: Authenticated chat fixture with real task storage.
        kind: Explicit semantic input type.
        raw_value: Exact fixture literal, including digits and decimal scale.
    """
    fixture = durable_client
    await install_model(fixture, operations=1)
    model = fixture.container.main_chat._model
    model.input_value = {"kind": kind, "value": raw_value}
    response = await fixture.client.post(
        "/v1/chat", json=fixture.request, headers=fixture.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_status"] == "answered"
    snapshot = await inspect_workbook(
        fixture.fixture.store.pool, fixture.user_id, fixture.active_id
    )
    assert len(snapshot["cells"]) == 1
    cell = snapshot["cells"][0]
    assert (cell["sheet_id"], cell["row_number"], cell["column_number"]) == (
        model.sheets[0],
        2,
        1,
    )
    assert cell["kind"] == kind
    assert type(cell["raw_value"]) is type(raw_value)
    assert cell["raw_value"] == raw_value
    assert cell["expression"] is None
    fixture.container.main_chat._model = ScriptedModel([])
    replay = await fixture.client.post(
        f"/v1/tasks/{body['task_id']}/resume", headers=fixture.headers
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["operation_receipts"] == body["operation_receipts"]
    assert (
        await inspect_workbook(
            fixture.fixture.store.pool, fixture.user_id, fixture.active_id
        )
        == snapshot
    )


async def test_typed_approval_executes_original_reference_and_resumes(approval_client):  # noqa: F811
    """Human approval and durable replay use the same typed operation identity."""
    fixture = approval_client
    await install_model(fixture, operations=1)
    response = await fixture.client.post(
        "/v1/chat", json=fixture.request, headers=fixture.headers
    )
    body = response.json()
    assert body["run_status"] == "awaiting_approval", body
    approval = body["pending_approvals"][0]["approval_id"]
    decision = await fixture.client.post(
        f"/v1/approvals/{approval}",
        json={"decision": "approve"},
        headers=fixture.headers,
    )
    assert decision.status_code == 200, decision.text
    receipt = decision.json()["result"]
    assert receipt["calculation_status"] == "not_required"
    resumed = await fixture.client.post(
        f"/v1/tasks/{body['task_id']}/resume", headers=fixture.headers
    )
    assert resumed.json()["operation_receipts"] == [receipt]
    assert resumed.json()["run_status"] == "answered"
