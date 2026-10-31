"""Replay a fixed version-1 continuation with its original operation identity."""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from klaudia.core.agent.agent import MainAgent
from klaudia.core.agent.context import TaskContext
from ledger_core.errors import ApprovalRequiredError
from tests.agent_compatibility import read_fixture
from tests.unit.test_agent_checkpoints import MemoryCheckpoint
from tests.unit.test_main_agent import ScriptedModel


@pytest.fixture
def old_continuation() -> tuple[dict, MemoryCheckpoint]:
    """Load the saved release checkpoint without constructing an old agent.

    Returns:
        Original recovery records and an independent checkpoint store.
    """
    saved = read_fixture("continuation.json")
    checkpoint = MemoryCheckpoint()
    task = saved["snapshots"]["pending_approval"]["workflow_task"][0]
    checkpoint.state = json.loads(task["checkpoint"])
    return saved, checkpoint


async def test_old_pending_execution_recovers_original_receipt(
    old_continuation,
) -> None:
    """Resume the stored call before a model decision and replay the final state."""
    saved, checkpoint = old_continuation
    operations = AsyncMock()
    operations.execute.return_value = saved["receipt"]
    model = ScriptedModel([AIMessage(content="Original append confirmed.")])
    context = TaskContext.model_validate(saved["context"])
    outcome = await MainAgent(model, AsyncMock(), operations=operations).run(
        saved["message"], context, checkpoint=checkpoint
    )

    operations.execute.assert_awaited_once_with(context.user_id, saved["operation_ref"])
    operations.prepare.assert_not_awaited()
    assert outcome.operation_references == (saved["operation_ref"],)
    assert outcome.operation_receipts == (saved["receipt"],)
    assert outcome.loaded_skills == {"table-append": "2"}
    assert outcome.working_set == ()
    observed = model.inputs[0][-1]
    assert isinstance(observed, ToolMessage)
    assert observed.tool_call_id == "execute-original"
    assert json.loads(observed.content) == saved["receipt"]
    assert checkpoint.state["pending_calls"] == []

    replay_model = ScriptedModel([])
    replay = await MainAgent(replay_model, AsyncMock(), operations=operations).run(
        saved["message"], context, checkpoint=checkpoint
    )
    assert replay == outcome
    assert replay_model.inputs == []
    assert operations.execute.await_count == 1


async def test_old_pending_approval_stays_outside_checkpoint(old_continuation) -> None:
    """Retain pending execution while ledger approval blocks the run."""
    saved, checkpoint = old_continuation
    original = json.loads(json.dumps(checkpoint.state))
    operations = AsyncMock()
    operations.execute.side_effect = ApprovalRequiredError(saved["approval"])
    model = ScriptedModel([])
    outcome = await MainAgent(model, AsyncMock(), operations=operations).run(
        saved["message"],
        TaskContext.model_validate(saved["context"]),
        checkpoint=checkpoint,
    )
    assert outcome.status == "awaiting_approval"
    assert outcome.pending_approvals == (saved["approval"],)
    assert outcome.operation_receipts == ()
    assert checkpoint.state == original
    assert model.inputs == []
    operations.prepare.assert_not_awaited()


@pytest.mark.parametrize("changed", ["owner", "workbook", "message", "tools", "limits"])
async def test_old_checkpoint_rejects_changed_contract(
    old_continuation, changed
) -> None:
    """Reject changed identity inputs before calling the model or executor."""
    from klaudia.core.agent.agent import RunLimits

    saved, checkpoint = old_continuation
    context_fields = dict(saved["context"])
    message = saved["message"]
    operations = AsyncMock()
    options = {"operations": operations}
    if changed == "owner":
        context_fields["user_id"] += 1
    elif changed == "workbook":
        context_fields["active_workbook_id"] = "another-workbook"
    elif changed == "message":
        message = "Another request"
    elif changed == "tools":
        options["operations"] = None
    else:
        options["limits"] = RunLimits(max_steps=11)
    model = ScriptedModel([])
    with pytest.raises(ValueError, match="Incompatible checkpoint"):
        await MainAgent(model, AsyncMock(), **options).run(
            message, TaskContext(**context_fields), checkpoint=checkpoint
        )
    assert model.inputs == []
    operations.execute.assert_not_awaited()
