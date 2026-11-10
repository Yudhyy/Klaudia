"""Task coordination preserves stored input, status and interruption handling."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest

from workflow_core.ports import TaskBusyError
from workflow_core.service import TaskCompletion, TaskRun, WorkflowService


class StoredTasks:
    """Retain one session and record the identity used to open it."""

    def __init__(self, session):
        """Bind the stored session without creating infrastructure.

        Args:
            session: Existing task state used by each execution attempt.
        """
        self.session = session
        self.create = AsyncMock(return_value="task:original")
        self.opened = []

    @asynccontextmanager
    async def open(self, user_id, task_id):
        """Keep execution inside the store's exclusive task scope.

        Args:
            user_id: Authenticated task owner.
            task_id: Original stored task identity.

        Yields:
            The same session until execution and status persistence finish.
        """
        self.opened.append((user_id, task_id))
        yield self.session
        self.opened.append("released")


async def test_start_opens_the_original_task_before_binding_its_run():
    """A keyed creation result selects the stored session, not the new payload."""
    session = AsyncMock()
    tasks = StoredTasks(session)
    stored_outcome = {"operation_references": ["prepared:original"]}

    async def run():
        """Use the bound stored state while the task is still held.

        Returns:
            The observed status and unchanged application outcome.
        """
        assert tasks.opened == [(42, "task:original")]
        return TaskCompletion("answered", stored_outcome)

    bind = Mock(return_value=TaskRun(run, AsyncMock()))
    workflow = WorkflowService(tasks, bind)
    payload = {"turn": {"text": "Retry", "request_key": "same-key"}}
    outcome = await workflow.start((42, 7), payload)
    assert outcome is stored_outcome
    tasks.create.assert_awaited_once_with((42, 7), payload)
    bind.assert_called_once_with(session)
    session.set_status.assert_awaited_once_with("answered")
    assert tasks.opened[-1] == "released"


async def test_failed_run_records_failure_before_reading_recovery_evidence():
    """A failed run returns the adapter's original references and receipts."""
    session = AsyncMock()
    tasks = StoredTasks(session)
    saved_receipt = {"operation_id": "original", "status": "committed"}
    failed_outcome = {"receipts": [saved_receipt]}

    async def recover():
        """Read saved evidence after the failure status reaches storage.

        Returns:
            The application-owned failure response with committed evidence.
        """
        session.set_status.assert_awaited_once_with("failed")
        return failed_outcome

    prepared = TaskRun(AsyncMock(side_effect=ConnectionError("Lost reply")), recover)
    workflow = WorkflowService(tasks, Mock(return_value=prepared))
    assert await workflow.resume(42, "task:original") is failed_outcome
    tasks.create.assert_not_awaited()


@pytest.mark.parametrize("status", ["awaiting_approval", "failed", "interrupted"])
async def test_observed_stop_status_does_not_trigger_an_automatic_retry(status):
    """A normal stop records its exact status and returns its application value."""
    session = AsyncMock()
    run = AsyncMock(return_value=TaskCompletion(status, "original-outcome"))
    recover = AsyncMock()
    workflow = WorkflowService(StoredTasks(session), lambda _: TaskRun(run, recover))
    assert await workflow.resume(42, "task:original") == "original-outcome"
    session.set_status.assert_awaited_once_with(status)
    run.assert_awaited_once()
    recover.assert_not_awaited()


@pytest.mark.parametrize("status_write_fails", [False, True])
async def test_cancellation_propagates_after_interruption_is_recorded(
    status_write_fails,
):
    """A failed interruption write cannot replace the original cancellation."""
    session = AsyncMock()
    if status_write_fails:
        session.set_status.side_effect = ConnectionError("Store closed")
    cancellation = asyncio.CancelledError("Original cancellation")
    run = AsyncMock(side_effect=cancellation)
    recover = AsyncMock()
    workflow = WorkflowService(StoredTasks(session), lambda _: TaskRun(run, recover))
    with pytest.raises(asyncio.CancelledError) as caught:
        await workflow.resume(42, "task:original")
    assert caught.value is cancellation
    session.set_status.assert_awaited_once_with("interrupted")
    recover.assert_not_awaited()


async def test_invalid_stored_input_is_not_reported_as_an_agent_failure():
    """Restore errors retain the existing pre-run failure behavior."""
    session = AsyncMock()
    bind = Mock(side_effect=ValueError("Invalid stored input"))
    workflow = WorkflowService(StoredTasks(session), bind)
    with pytest.raises(ValueError, match="Invalid stored input"):
        await workflow.resume(42, "task:original")
    session.set_status.assert_not_awaited()


async def test_failed_status_write_does_not_claim_a_recovered_outcome():
    """Recovery cannot hide failure to persist the failed task status."""
    session = AsyncMock()
    session.set_status.side_effect = ConnectionError("Store closed")
    run = AsyncMock(side_effect=RuntimeError("Run failed"))
    recover = AsyncMock()
    workflow = WorkflowService(StoredTasks(session), lambda _: TaskRun(run, recover))
    with pytest.raises(ConnectionError, match="Store closed"):
        await workflow.resume(42, "task:original")
    recover.assert_not_awaited()


async def test_busy_task_never_binds_or_invokes_the_agent():
    """Store contention stops execution before the application run is built."""
    tasks = StoredTasks(AsyncMock())

    @asynccontextmanager
    async def busy(user_id, task_id):
        """Reject another owner of the same task execution slot.

        Args:
            user_id: Authenticated task owner.
            task_id: Task already held by another execution.

        Yields:
            No session when the task is busy.
        """
        raise TaskBusyError("Task is already running")
        yield

    tasks.open = busy
    bind = Mock()
    workflow = WorkflowService(tasks, bind)
    with pytest.raises(TaskBusyError):
        await workflow.resume(42, "task:original")
    bind.assert_not_called()
