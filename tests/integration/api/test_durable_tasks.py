"""Real PostgreSQL task recovery, retry identity and concurrent execution checks."""

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.routes.v1.tasks import router
from app.services.core.main_chat import MainChatService
from app.services.core.operations import OperationService
from app.services.workflow.store import TaskSession, TaskStore
from tests.integration.api.test_main_chat_routes import main_chat_client  # noqa: F401
from tests.integration.postgres import POSTGRES_TEST_URL
from tests.unit.test_main_agent import ScriptedModel


@pytest.fixture
async def durable_client(main_chat_client):  # noqa: F811
    """Add production task storage to the real authenticated chat fixture."""
    fixture = main_chat_client
    tasks = TaskStore(POSTGRES_TEST_URL)
    await tasks.connect()
    fixture.container.tasks = tasks
    fixture.container.main_chat = MainChatService(
        fixture.model,
        fixture.container.main_chat._catalogue,
        fixture.container.db_client,
        operations=OperationService(fixture.fixture.store),
        tasks=tasks,
    )
    fixture.client._transport.app.include_router(router, prefix="/v1")
    fixture.session_id = await fixture.container.db_client.create_session(
        fixture.user_id
    )
    fixture.request = {
        "session_id": fixture.session_id,
        "spreadsheet_id": fixture.active_id,
        "request_key": "first-append",
        "messages": [{"role": "user", "content": fixture.fixture.case.turns[0].user}],
    }
    try:
        yield fixture
    finally:
        await tasks.close()


async def test_keyed_retry_returns_original_task_and_receipt(durable_client):
    """Reusing a client key never generates a second append after revisions change."""
    fixture = durable_client
    first = await fixture.client.post(
        "/v1/chat", json=fixture.request, headers=fixture.headers
    )
    assert first.status_code == 200, first.text
    first = first.json()
    assert first["task_id"].startswith("task:")
    assert first["run_status"] == "answered"
    fixture.container.main_chat._model = ScriptedModel([])
    repeated = await fixture.client.post(
        "/v1/chat", json=fixture.request, headers=fixture.headers
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["task_id"] == first["task_id"]
    assert repeated.json()["operation_receipts"] == first["operation_receipts"]
    assert (
        await fixture.fixture.observe_state()
        == fixture.fixture.case.turns[0].expect.ledger_state
    )
    changed = {
        **fixture.request,
        "messages": [{"role": "user", "content": "Different request"}],
    }
    conflict = await fixture.client.post(
        "/v1/chat", json=changed, headers=fixture.headers
    )
    assert conflict.status_code == 409


async def test_restart_after_lost_checkpoint_replays_committed_operation(
    durable_client, monkeypatch
):
    """New service state replays the pending call without duplicating stored rows."""
    fixture = durable_client
    original_save = TaskSession.save

    async def lose_receipt_checkpoint(self, state):
        """Stop after the ledger commits, before checkpoint persistence succeeds."""
        if state["receipts"]:
            raise ConnectionError("Lost checkpoint write")
        await original_save(self, state)

    monkeypatch.setattr(TaskSession, "save", lose_receipt_checkpoint)
    failed = await fixture.client.post(
        "/v1/chat", json=fixture.request, headers=fixture.headers
    )
    assert failed.status_code == 200, failed.text
    task_id = failed.json()["task_id"]
    assert failed.json()["run_status"] == "failed"
    assert len(failed.json()["operation_receipts"]) == 1
    progress = await fixture.client.get(f"/v1/tasks/{task_id}", headers=fixture.headers)
    assert progress.json()["pending_tools"] == ["execute_operation"]
    assert len(progress.json()["operation_receipts"]) == 1
    assert "checkpoint" not in progress.json()
    monkeypatch.setattr(TaskSession, "save", original_save)
    resumed_model = ScriptedModel([AIMessage(content="Original append confirmed.")])
    fixture.container.main_chat._model = resumed_model
    resumed = await fixture.client.post(
        f"/v1/tasks/{task_id}/resume", headers=fixture.headers
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["run_status"] == "answered"
    assert resumed.json()["operation_receipts"] == failed.json()["operation_receipts"]
    assert (
        await fixture.fixture.observe_state()
        == fixture.fixture.case.turns[0].expect.ledger_state
    )
    assert resumed_model.inputs[0][-1].type == "tool"


async def test_running_task_cannot_be_resumed_concurrently(durable_client):
    """An exclusive task connection prevents another worker from advancing it."""
    fixture = durable_client
    response = await fixture.client.post(
        "/v1/chat", json=fixture.request, headers=fixture.headers
    )
    task_id = response.json()["task_id"]
    async with fixture.container.tasks.open(fixture.user_id, task_id):
        busy = await fixture.client.post(
            f"/v1/tasks/{task_id}/resume", headers=fixture.headers
        )
        assert busy.status_code == 409, busy.text
    assert (await fixture.client.get(f"/v1/tasks/{task_id}")).status_code == 401
    missing = await fixture.client.get(
        "/v1/tasks/task:missing", headers=fixture.headers
    )
    assert missing.status_code == 404


@pytest.fixture
async def approval_client(durable_client):
    """Enable explicit checked-append approval through the existing decision API."""
    from app.routes.v1.approvals import router as approvals_router
    from app.services.core.approvals import ApprovalService
    from app.services.workflow.approvals import CheckedApprovals

    fixture = durable_client
    fixture.container.tasks._require_approval = True
    approvals = ApprovalService(fixture.container.db_client, AsyncMock())
    await approvals.ensure_schema()
    approvals.checked = CheckedApprovals(fixture.fixture.store.pool)
    fixture.container.approvals = approvals
    fixture.client._transport.app.include_router(approvals_router, prefix="/v1")
    return fixture


async def test_human_approval_executes_exact_proposal_then_task_resumes(
    approval_client,
):
    """Approval and continuation retain one operation identity and one committed row."""
    fixture = approval_client
    before = await fixture.fixture.observe_state()
    paused = await fixture.client.post(
        "/v1/chat", json=fixture.request, headers=fixture.headers
    )
    assert paused.status_code == 200, paused.text
    paused = paused.json()
    assert paused["run_status"] == "awaiting_approval"
    assert await fixture.fixture.observe_state() == before
    proposal = paused["pending_approvals"][0]
    assert proposal["proposal"]["records"][0]["Amount"] == 185000
    assert proposal["proposal"]["records"][0]["Merchant"] == "Taxi vendor"
    pending = await fixture.client.get("/v1/approvals", headers=fixture.headers)
    assert pending.status_code == 200, pending.text
    assert proposal in pending.json()
    decision = await fixture.client.post(
        f"/v1/approvals/{proposal['approval_id']}",
        json={"decision": "approve"},
        headers=fixture.headers,
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["executed"] is True
    resumed = await fixture.client.post(
        f"/v1/tasks/{paused['task_id']}/resume", headers=fixture.headers
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["run_status"] == "answered"
    assert resumed.json()["operation_receipts"] == [decision.json()["result"]]
    assert (
        await fixture.fixture.observe_state()
        == fixture.fixture.case.turns[0].expect.ledger_state
    )
    again = await fixture.client.post(
        f"/v1/approvals/{proposal['approval_id']}",
        json={"decision": "approve"},
        headers=fixture.headers,
    )
    assert again.json()["result"] == decision.json()["result"]


async def test_first_failure_returns_discoverable_resumable_task(durable_client):
    """A failure before any tool call still returns a durable continuation identity."""
    fixture = durable_client
    broken_model = AsyncMock()
    broken_model.bind_tools = lambda tools: broken_model
    broken_model.ainvoke.side_effect = ConnectionError("Provider unavailable")
    fixture.container.main_chat._model = broken_model
    request = {**fixture.request}
    request.pop("request_key")
    failed = await fixture.client.post(
        "/v1/chat", json=request, headers=fixture.headers
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["run_status"] == "failed"
    task_id = failed.json()["task_id"]
    tasks = await fixture.client.get(
        "/v1/tasks", params={"session_id": fixture.session_id}, headers=fixture.headers
    )
    assert tasks.status_code == 200, tasks.text
    assert tasks.json()["tasks"][0]["task_id"] == task_id
    fixture.container.main_chat._model = ScriptedModel(
        [AIMessage(content="Ready to continue.")]
    )
    resumed = await fixture.client.post(
        f"/v1/tasks/{task_id}/resume", headers=fixture.headers
    )
    assert resumed.json()["run_status"] == "answered"


async def test_inspection_survives_malformed_pending_model_arguments(durable_client):
    """A pending unvalidated call cannot make the progress endpoint fail."""
    fixture = durable_client
    response = await fixture.client.post(
        "/v1/chat", json=fixture.request, headers=fixture.headers
    )
    task_id = response.json()["task_id"]
    async with fixture.container.tasks.open(fixture.user_id, task_id) as task:
        state = await task.load()
        state["pending_calls"] = [
            {"name": "execute_operation", "args": {}, "id": "invalid"}
        ]
        await task.save(state)
    progress = await fixture.client.get(f"/v1/tasks/{task_id}", headers=fixture.headers)
    assert progress.status_code == 200, progress.text
    assert progress.json()["pending_tools"] == ["execute_operation"]


async def test_checkpoint_replay_rechecks_saved_workbook_access(durable_client):
    """A finished task cannot replay cached financial evidence after access loss."""
    fixture = durable_client
    response = await fixture.client.post(
        "/v1/chat", json=fixture.request, headers=fixture.headers
    )
    task_id = response.json()["task_id"]
    store = fixture.fixture.store
    try:
        await store.pool.execute(
            "UPDATE ledger_spreadsheet SET user_id = $1 WHERE spreadsheet_id = $2",
            fixture.user_id + 1000000,
            fixture.fixture.workbook_id,
        )
        fixture.container.main_chat._model = ScriptedModel([])
        denied = await fixture.client.post(
            f"/v1/tasks/{task_id}/resume", headers=fixture.headers
        )
        assert denied.status_code == 404, denied.text
        assert fixture.container.main_chat._model.inputs == []
    finally:
        await store.pool.execute(
            "UPDATE ledger_spreadsheet SET user_id = $1 WHERE spreadsheet_id = $2",
            fixture.user_id,
            fixture.fixture.workbook_id,
        )


async def test_saturated_execution_pool_keeps_progress_readable(durable_client):
    """Task inspection uses separate capacity and resume returns a bounded conflict."""
    from contextlib import AsyncExitStack

    fixture = durable_client
    response = await fixture.client.post(
        "/v1/chat", json=fixture.request, headers=fixture.headers
    )
    task_id = response.json()["task_id"]
    async with AsyncExitStack() as stack:
        for _ in range(4):
            await stack.enter_async_context(fixture.container.tasks._pool.acquire())
        progress = await fixture.client.get(
            f"/v1/tasks/{task_id}", headers=fixture.headers
        )
        assert progress.status_code == 200, progress.text
        busy = await fixture.client.post(
            f"/v1/tasks/{task_id}/resume", headers=fixture.headers
        )
        assert busy.status_code == 409, busy.text


async def test_stream_exposes_approval_and_reuses_task_identity(approval_client):
    """Streaming clients receive the same approval and task contracts as chat."""
    import json

    fixture = approval_client
    response = await fixture.client.post(
        "/v1/chat/stream", json=fixture.request, headers=fixture.headers
    )
    assert response.status_code == 200, response.text
    frames = response.text.split("\n\n")
    assert any(frame.startswith("event: approval_required") for frame in frames)
    done = next(
        json.loads(frame.split("data: ", 1)[1])
        for frame in frames
        if frame.startswith("event: done")
    )
    assert done["run_status"] == "awaiting_approval"
    repeated = await fixture.client.post(
        "/v1/chat", json=fixture.request, headers=fixture.headers
    )
    assert repeated.json()["task_id"] == done["task_id"]


async def test_later_rejection_preserves_earlier_committed_receipt(approval_client):
    """A multi-step task retains its first commit when a later approval is rejected."""
    import json

    from tests.integration.api.test_main_chat_routes import AppendChatModel
    from tests.unit.test_main_agent import call

    class TwoAppendModel(AppendChatModel):
        """Request a second checked append after the first committed receipt."""

        async def ainvoke(self, messages, config=None):
            """Continue from the previous operation's actual evidence."""
            previous = messages[-1]
            if previous.type == "tool" and previous.name == "execute_operation":
                evidence = json.loads(previous.content)
                if "error" in evidence:
                    return AIMessage(
                        content="The first append committed. The second append was rejected."
                    )
                return call(
                    "inspect_resource",
                    {"table_id": evidence["target"]["table_id"]},
                    "inspect-next",
                )
            return await super().ainvoke(messages, config)

    fixture = approval_client
    fixture.container.main_chat._model = TwoAppendModel()
    first = (
        await fixture.client.post(
            "/v1/chat", json=fixture.request, headers=fixture.headers
        )
    ).json()
    first_id = first["pending_approvals"][0]["approval_id"]
    approved = await fixture.client.post(
        f"/v1/approvals/{first_id}",
        json={"decision": "approve"},
        headers=fixture.headers,
    )
    assert approved.status_code == 200, approved.text
    second = await fixture.client.post(
        f"/v1/tasks/{first['task_id']}/resume", headers=fixture.headers
    )
    assert second.status_code == 200, second.text
    assert second.json()["run_status"] == "awaiting_approval"
    assert second.json()["operation_receipts"] == [approved.json()["result"]]
    second_id = second.json()["pending_approvals"][0]["approval_id"]
    assert second_id != first_id
    rejected = await fixture.client.post(
        f"/v1/approvals/{second_id}",
        json={"decision": "reject"},
        headers=fixture.headers,
    )
    assert rejected.status_code == 200, rejected.text
    final = await fixture.client.post(
        f"/v1/tasks/{first['task_id']}/resume", headers=fixture.headers
    )
    assert final.json()["run_status"] == "answered"
    assert final.json()["operation_receipts"] == [approved.json()["result"]]
    assert (
        await fixture.fixture.observe_state()
        == fixture.fixture.case.turns[0].expect.ledger_state
    )
