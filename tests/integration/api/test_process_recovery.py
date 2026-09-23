"""Kill real task workers around commits and recover in another process boundary."""

import asyncio
import json
import signal
import sys

import pytest
from langchain_core.messages import AIMessage

from tests.integration.api.test_main_chat_routes import main_chat_client  # noqa: F401
from tests.integration.api.test_durable_tasks import durable_client  # noqa: F401
from tests.unit.test_main_agent import ScriptedModel


async def kill_at_boundary(fixture, boundary):
    """Kill only the child created by this test after its database write signal.

    Args:
        fixture: Isolated owner, services and seeded workbook.
        boundary: Before execution or after its committed result.

    Returns:
        Original task identity whose connection died with the child.
    """
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.integration.api.recovery_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        payload = {
            "boundary": boundary,
            "user_id": fixture.user_id,
            "session_id": fixture.session_id,
            "workbook_id": fixture.active_id,
            "text": fixture.fixture.case.turns[0].user,
            "request_key": "killed-append",
        }
        process.stdin.write(json.dumps(payload).encode() + b"\n")
        await process.stdin.drain()
        line = await asyncio.wait_for(process.stdout.readline(), timeout=30)
        if not line:
            _, diagnostics = await asyncio.wait_for(process.communicate(), timeout=10)
            raise AssertionError(diagnostics.decode())
        observed = json.loads(line)
        assert observed["boundary"] == boundary
        process.kill()
        await asyncio.wait_for(process.communicate(), timeout=10)
        assert process.returncode == -signal.SIGKILL
        return observed["task_id"]
    finally:
        if process.returncode is None:
            process.kill()
            await asyncio.wait_for(process.communicate(), timeout=10)


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
@pytest.mark.parametrize("repeat", range(3))
async def test_actual_process_kill_recovers_original_operation(
    durable_client,
    boundary,
    repeat,  # noqa: F811
):
    """Recover the saved reference once after SIGKILL, then replay without another write."""
    fixture = durable_client
    before = await fixture.fixture.observe_state()
    task_id = await kill_at_boundary(fixture, boundary)
    stored = await fixture.container.tasks.get(fixture.user_id, task_id)
    checkpoint = json.loads(stored["checkpoint"])
    assert stored["status"] == "running"
    assert checkpoint["pending_calls"][0]["name"] == "execute_operation"
    reference = checkpoint["pending_calls"][0]["args"]["operation_ref"]
    expected = fixture.fixture.case.turns[0].expect.ledger_state
    assert await fixture.fixture.observe_state() == (
        before if boundary == "before_commit" else expected
    )
    fixture.container.main_chat._model = ScriptedModel(
        [AIMessage(content="Original append recovered.")]
    )
    for _ in range(2):
        response = await fixture.client.post(
            f"/v1/tasks/{task_id}/resume", headers=fixture.headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["run_status"] == "answered"
        assert body["operation_references"] == [reference]
        assert len(body["operation_receipts"]) == 1
        assert body["operation_receipts"][0]["status"] == "committed"
        assert await fixture.fixture.observe_state() == expected


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
async def test_killed_task_cannot_resume_after_source_revocation(
    durable_client,
    boundary,  # noqa: F811
):
    """Recheck current source access even when a committed receipt survived the crash."""
    fixture = durable_client
    task_id = await kill_at_boundary(fixture, boundary)
    state = await fixture.fixture.observe_state()
    store = fixture.fixture.store
    try:
        await store.pool.execute(
            "UPDATE ledger_spreadsheet SET user_id = $1 WHERE spreadsheet_id = $2",
            fixture.user_id + 1000000,
            fixture.fixture.workbook_id,
        )
        response = await fixture.client.post(
            f"/v1/tasks/{task_id}/resume", headers=fixture.headers
        )
        assert response.status_code == 404
        assert await fixture.fixture.observe_state() == state
    finally:
        await store.pool.execute(
            "UPDATE ledger_spreadsheet SET user_id = $1 WHERE spreadsheet_id = $2",
            fixture.user_id,
            fixture.fixture.workbook_id,
        )
