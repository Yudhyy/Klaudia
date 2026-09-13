"""Durable checkpoints replay pending calls without repeating committed appends."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from klaudia.core.agent.agent import MainAgent
from klaudia.core.agent.context import TaskContext
from tests.unit.test_main_agent import ScriptedModel, call


class MemoryCheckpoint:
    """Simulate a durable store that survives construction of another agent."""

    def __init__(self):
        """Start without persisted state."""
        self.state = None
        self.fail_after_execute = False

    async def load(self):
        """Return an isolated copy of the last committed checkpoint."""
        return deepcopy(self.state)

    async def save(self, state):
        """Simulate a storage loss after a tool returned its committed receipt."""
        if self.fail_after_execute and state["receipts"]:
            raise ConnectionError("Checkpoint write lost")
        self.state = deepcopy(state)


async def test_restart_replays_pending_reference_and_continues_without_replanning():
    """A lost post-commit checkpoint leaves the original execute call pending."""
    checkpoint = MemoryCheckpoint()
    checkpoint.fail_after_execute = True
    operations = AsyncMock()
    operations.execute.return_value = {
        "operation_id": "op_one",
        "status": "committed",
        "target": {"sheet_id": 8},
    }
    first = MainAgent(
        ScriptedModel([call("execute_operation", {"operation_ref": "original"})]),
        AsyncMock(),
        operations=operations,
    )
    with pytest.raises(Exception):
        await first.run("Append", TaskContext(user_id=42), checkpoint=checkpoint)
    assert checkpoint.state["pending_calls"][0]["args"] == {"operation_ref": "original"}
    checkpoint.fail_after_execute = False
    second_model = ScriptedModel([AIMessage(content="Original append confirmed.")])
    second = MainAgent(second_model, AsyncMock(), operations=operations)
    outcome = await second.run("Append", TaskContext(user_id=42), checkpoint=checkpoint)
    assert outcome.status == "answered"
    assert (
        operations.execute.await_args_list[0].args
        == operations.execute.await_args_list[1].args
    )
    operations.prepare.assert_not_awaited()
    assert len(outcome.operation_receipts) == 1
    assert second_model.inputs[0][-1].type == "tool"
    third = MainAgent(ScriptedModel([]), AsyncMock(), operations=operations)
    replay = await third.run("Append", TaskContext(user_id=42), checkpoint=checkpoint)
    assert replay.content == outcome.content
    assert operations.execute.await_count == 2


async def test_checkpoint_rejects_different_identity_or_request():
    """A stored continuation cannot be adopted by another user or request."""
    checkpoint = MemoryCheckpoint()
    agent = MainAgent(ScriptedModel([AIMessage(content="Ready")]), AsyncMock())
    await agent.run("Hello", TaskContext(user_id=42), checkpoint=checkpoint)
    with pytest.raises(ValueError, match="checkpoint"):
        await agent.run("Hello", TaskContext(user_id=43), checkpoint=checkpoint)
    with pytest.raises(ValueError, match="checkpoint"):
        await agent.run(
            "Different intent", TaskContext(user_id=42), checkpoint=checkpoint
        )


async def test_overlap_is_repairable_and_does_not_trap_checkpoint_resume():
    """A raced table registration becomes tool evidence rather than a stuck task."""
    from ledger.resources import ResourceExistsError

    checkpoint = MemoryCheckpoint()
    operations = AsyncMock()
    operations.execute.side_effect = ResourceExistsError("Region already registered")
    model = ScriptedModel(
        [
            call("execute_operation", {"operation_ref": "authoring:overlap"}),
            AIMessage(
                content="That region already contains a table. Please clarify the intended destination."
            ),
        ]
    )
    agent = MainAgent(model, AsyncMock(), operations=operations)
    outcome = await agent.run(
        "Create a table", TaskContext(user_id=42), checkpoint=checkpoint
    )
    assert outcome.status == "answered"
    assert outcome.operation_receipts == ()
    assert checkpoint.state["pending_calls"] == []
    replay = await agent.run(
        "Create a table", TaskContext(user_id=42), checkpoint=checkpoint
    )
    assert replay.content == outcome.content
    operations.execute.assert_awaited_once()
