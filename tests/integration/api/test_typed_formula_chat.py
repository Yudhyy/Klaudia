"""Main chat retains typed operation receipts through approvals and durable replay."""

import json

import pytest
from langchain_core.messages import AIMessage

from app.services.core.main_chat import MainChatService
from app.services.core.operations import OperationService
from app.services.workflow.store import TaskSession
from ledger.typed_cells import inspect_workbook
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
                "input": {
                    "kind": "decimal",
                    "value": "0.1" if self.stage == 0 else "0.3",
                    "unit": "USD",
                },
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
    assert [receipt["calculation_status"] for receipt in receipts] == [
        "not_required",
        "current",
        "current",
    ]
    assert receipts[-1]["calculation"]["results"][0]["value"] == "0.50"
    assert (
        await fixture.fixture.store.get_snapshot(fixture.active_id, "Totals")
    ).values[1][0] == 0.5
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
