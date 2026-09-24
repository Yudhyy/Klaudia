"""Runtime rollback preserves pending approvals and committed ledger effects."""

import pytest

from app.services.core.main_chat import MainChatService
from app.services.core.operations import OperationService
from app.services.workflow.store import TaskStore
from tests.integration.api.test_durable_tasks import approval_client, durable_client  # noqa: F401
from tests.integration.api.test_main_chat_routes import main_chat_client  # noqa: F401
from tests.integration.postgres import POSTGRES_TEST_URL


@pytest.mark.parametrize("committed", [False, True])
async def test_runtime_switch_retains_tasks_and_resumes_original_operation(
    approval_client,  # noqa: F811
    committed,
):
    """Disable main services, retain storage, then reopen and recover exactly once."""
    fixture = approval_client
    client, container = fixture.client, fixture.container
    client.headers.update(fixture.headers)
    response = await client.post("/v1/chat", json=fixture.request)
    assert response.status_code == 200, response.text
    paused = response.json()
    assert paused["run_status"] == "awaiting_approval"
    task_id = paused["task_id"]
    approval_id = paused["pending_approvals"][0]["approval_id"]
    if committed:
        decision = await client.post(
            f"/v1/approvals/{approval_id}", json={"decision": "approve"}
        )
        assert decision.status_code == 200, decision.text
        completion = await client.post(f"/v1/tasks/{task_id}/resume")
        assert completion.status_code == 200, completion.text
        assert completion.json()["run_status"] == "answered"

    before_grid = await fixture.fixture.observe_state()
    before_task = await container.tasks.get(fixture.user_id, task_id)
    before_approval = await container.db_client.fetchone(
        "SELECT * FROM ledger_table_operation WHERE approval_id=$1", (approval_id,)
    )
    old_service, old_tasks = container.main_chat, container.tasks
    await old_tasks.close()
    container.settings.chat_runtime = "legacy"
    container.main_chat, container.tasks = None, None
    for method, route in (
        ("GET", f"/v1/tasks/{task_id}"),
        ("POST", f"/v1/tasks/{task_id}/resume"),
    ):
        unavailable = await client.request(method, route)
        assert unavailable.status_code == 503
    assert await fixture.fixture.observe_state() == before_grid
    assert (
        await container.db_client.fetchone(
            "SELECT * FROM ledger_table_operation WHERE approval_id=$1", (approval_id,)
        )
        == before_approval
    )

    reopened = TaskStore(POSTGRES_TEST_URL, require_approval=True)
    await reopened.connect()
    try:
        assert await reopened.get(fixture.user_id, task_id) == before_task
        container.settings.chat_runtime = "main"
        container.tasks = reopened
        container.main_chat = MainChatService(
            fixture.model,
            old_service._catalogue,
            container.db_client,
            operations=OperationService(fixture.fixture.store),
            tasks=reopened,
        )
        decision = await client.post(
            f"/v1/approvals/{approval_id}", json={"decision": "approve"}
        )
        assert decision.status_code == 200, decision.text
        receipt = decision.json()["result"]
        for _ in range(2):
            resumed = await client.post(f"/v1/tasks/{task_id}/resume")
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["run_status"] == "answered"
            assert resumed.json()["operation_receipts"] == [receipt]
            assert (
                await fixture.fixture.observe_state()
                == fixture.fixture.case.turns[0].expect.ledger_state
            )
    finally:
        await reopened.close()
