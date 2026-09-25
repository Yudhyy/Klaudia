"""Chat integration preserves authenticated context and operation recovery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.models.chat import ChatMetadata
from app.services.core.main_chat import MainChatService, MainChatTurn
from tests.unit.test_agent_discovery_tools import descriptor
from tests.unit.test_main_agent import ScriptedModel, call


def chat_turn():
    """Build a server-authenticated chat turn with an active resource hint."""
    return MainChatTurn(
        user_id=42,
        session_id=7,
        active_workbook_id="active",
        text="Add the expense",
        extraction_contexts=("Extracted receipt facts",),
        metadata=ChatMetadata(),
    )


def chat_service(model, operations=None):
    """Use recorded persistence and the real agent with scripted decisions."""
    database = AsyncMock()
    database.get_conversation_history.return_value = [
        {"sender": "assistant", "message_text": "Previous answer"},
        {"sender": "user", "message_text": "Previous request"},
    ]
    catalogue = AsyncMock()
    catalogue.inspect.return_value = descriptor()
    service = MainChatService(model, catalogue, database, operations=operations)
    return service, database


async def test_chat_loads_history_once_without_resource_inventory():
    """History and extraction are data after the stable agent prefix."""
    model = ScriptedModel([AIMessage(content="Ready")])
    service, database = chat_service(model)
    outcome = await service.run(chat_turn())
    assert outcome.status == "answered"
    context = model.inputs[0]
    assert len([message for message in context if message.type == "system"]) == 1
    assert "Previous request" in context[-1].content
    assert "Extracted receipt facts" in context[-1].content
    assert context[-1].content.count("Add the expense") == 1
    assert "active" in context[1].content
    assert [args.args[3] for args in database.save_message.await_args_list][:2] == [
        "Extracted receipt facts",
        "Add the expense",
    ]


async def test_reference_is_saved_before_execution_and_survives_failure():
    """An uncertain write has a durable session reference before execution starts."""
    operations = AsyncMock()
    operations.prepare.return_value = {
        "operation_ref": "prepared:one",
        "status": "prepared",
    }
    model = ScriptedModel(
        [
            call("inspect_resource", {"table_id": "tbl_claims"}),
            call(
                "prepare_table_append",
                {"table_id": "tbl_claims", "records": [{"Amount": 20}]},
                "prepare",
            ),
            call("execute_operation", {"operation_ref": "prepared:one"}, "execute"),
        ]
    )
    service, database = chat_service(model, operations)

    async def uncertain_execute(user_id, reference):
        """Fail after checking that recovery evidence already reached storage."""
        assert user_id == 42
        assert any(
            reference in args.args[3] for args in database.save_message.await_args_list
        )
        raise ConnectionError("Response lost")

    operations.execute.side_effect = uncertain_execute
    outcome = await service.run(chat_turn())
    assert outcome.status == "failed"
    assert outcome.operation_references == ("prepared:one",)
    assert outcome.operation_receipts == ()
    assert operations.execute.await_count == 1


async def test_persistence_failure_prevents_execution():
    """A write cannot run when its recovery reference could not be saved."""
    operations = AsyncMock()
    operations.prepare.return_value = {
        "operation_ref": "prepared:one",
        "status": "prepared",
    }
    model = ScriptedModel(
        [
            call("inspect_resource", {"table_id": "tbl_claims"}),
            call(
                "prepare_table_append",
                {"table_id": "tbl_claims", "records": [{"Amount": 20}]},
                "prepare",
            ),
        ]
    )
    service, database = chat_service(model, operations)

    async def reject_recovery(*args):
        """Allow the input messages but reject the operation journal."""
        if "prepared:one" in args[3]:
            raise ConnectionError("Storage unavailable")

    database.save_message.side_effect = reject_recovery
    with pytest.raises(ConnectionError):
        await service.run(chat_turn())
    operations.execute.assert_not_awaited()


async def test_cancelled_execution_keeps_saved_reference():
    """Cancellation propagates while the session retains the retry identity."""
    import asyncio

    from klaudia.core.agent.agent import AgentRunCancelled

    operations = AsyncMock()
    operations.execute.side_effect = asyncio.CancelledError()
    model = ScriptedModel(
        [
            call("execute_operation", {"operation_ref": "prepared:old"}),
        ]
    )
    service, database = chat_service(model, operations)
    with pytest.raises(AgentRunCancelled) as caught:
        await service.run(chat_turn())
    assert caught.value.outcome.operation_references == ("prepared:old",)
    assert any(
        "prepared:old" in args.args[3] for args in database.save_message.await_args_list
    )


async def test_post_commit_failure_keeps_receipt_and_never_reexecutes():
    """A model failure after commit retains the actual receipt for the client."""
    operations = AsyncMock()
    receipt = {
        "operation_id": "one",
        "status": "committed",
        "target": {"sheet_id": 8},
        "calculation_status": "not_supported",
    }
    operations.execute.return_value = receipt
    model = ScriptedModel(
        [call("execute_operation", {"operation_ref": "prepared:old"})]
    )
    service, database = chat_service(model, operations)
    outcome = await service.run(chat_turn())
    assert outcome.status == "failed"
    assert outcome.operation_receipts == (receipt,)
    assert operations.execute.await_count == 1
    assert any(
        '"receipt"' in args.args[3] for args in database.save_message.await_args_list
    )


def test_single_agent_has_no_runtime_or_legacy_memory_settings():
    """The application cannot select a retired runtime or memory service."""
    from config.settings import Settings

    assert "chat_runtime" not in Settings.model_fields
    assert "memory_mode" not in Settings.model_fields
    assert "mcp_archive_url" not in Settings.model_fields


def orchestrator_container():
    """Build a container exposing only the main chat service."""
    from config.settings import Settings
    from klaudia.core.agent.agent import RunOutcome

    database = AsyncMock()
    database.create_session.return_value = 7
    database.get_conversation_history.return_value = []
    guardrails = AsyncMock()
    guardrails.validate_input.return_value = SimpleNamespace(passed=True)
    guardrails.validate_output.return_value = SimpleNamespace(passed=True)
    main_chat = AsyncMock()
    main_chat.run.return_value = RunOutcome(
        "answered",
        "Recorded the expense.",
        2,
        ("execute_operation",),
        {},
        (),
        ({"operation_id": "one", "status": "committed"},),
        ("prepared:one",),
    )
    spreadsheets = AsyncMock()
    spreadsheets.resolve_scope.return_value = "active"
    return SimpleNamespace(
        settings=Settings(_env_file=None, NUMERIC_VERIFY_MODE="off"),
        db_client=database,
        guardrails=guardrails,
        main_chat=main_chat,
        extraction_agent=AsyncMock(),
        langfuse=None,
        spreadsheets=spreadsheets,
    )


@pytest.mark.parametrize("streaming", [False, True])
async def test_both_chat_paths_preserve_identity_and_check_output_first(streaming):
    """Both transports retain checked main-agent output."""
    from app.models.chat import KlaudiaMessage
    from app.services.core.orchestrator import KlaudiaOrchestrator

    container = orchestrator_container()
    container.guardrails.validate_output.return_value = SimpleNamespace(
        passed=False, rejection_message="Reply blocked"
    )
    orchestrator = KlaudiaOrchestrator(container)
    arguments = dict(
        messages=[KlaudiaMessage(role="user", content="Add the expense")],
        session_id=None,
        user_id=42,
        spreadsheet_id="active",
    )
    if streaming:
        events = [event async for event in orchestrator.stream(**arguments)]
        assert [
            event["data"]["text"] for event in events if event["type"] == "token"
        ] == ["Reply blocked"]
        response = next(event["data"] for event in events if event["type"] == "done")
    else:
        reply = await orchestrator.process(**arguments)
        assert reply.message.content == "Reply blocked"
        response = reply.model_dump()
    assert response["runtime"] == "main"
    assert response["operation_references"] == ["prepared:one"]
    assert response["operation_receipts"][0]["status"] == "committed"
    turn = container.main_chat.run.await_args.args[0]
    assert (turn.user_id, turn.session_id, turn.active_workbook_id) == (42, 7, "active")
    container.guardrails.validate_input.assert_awaited_once()
    container.guardrails.validate_output.assert_awaited_once()


async def test_receipt_survives_post_commit_journal_failure():
    """An observed commit remains in the response when its journal write fails."""
    operations = AsyncMock()
    receipt = {"operation_id": "one", "status": "committed", "target": {"sheet_id": 8}}
    operations.execute.return_value = receipt
    service, database = chat_service(
        ScriptedModel([call("execute_operation", {"operation_ref": "prepared:old"})]),
        operations,
    )

    async def reject_receipt(*args):
        """Fail only after the ledger has returned committed evidence."""
        if '"receipt"' in args[3]:
            raise ConnectionError("Journal failed")

    database.save_message.side_effect = reject_receipt
    outcome = await service.run(chat_turn())
    assert outcome.status == "failed"
    assert outcome.operation_receipts == (receipt,)
    assert outcome.operation_references == ("prepared:old",)


async def test_large_history_does_not_block_small_followup():
    """Oversized archived extraction context leaves later requests usable."""
    model = ScriptedModel([AIMessage(content="Ready")])
    service, database = chat_service(model)
    database.get_conversation_history.return_value = [
        {"sender": "assistant", "message_text": "Recover prepared:old"},
        {"sender": "user", "message_text": "large extraction " * 100000},
    ]
    outcome = await service.run(chat_turn())
    assert outcome.status == "answered"
    assert "Recover prepared:old" in model.inputs[0][-1].content
    assert "Context omitted" in model.inputs[0][-1].content
    assert len(model.inputs[0][-1].content.encode()) < 32768
    assert {"search_documents", "read_document_page"} <= {
        tool.name for tool in model.tools
    }


@pytest.mark.parametrize("streaming", [False, True])
async def test_extraction_facts_and_document_ids_reach_main_chat(streaming):
    """Both transports hand extracted facts to the main runtime in upload order."""
    from app.models.attachment import FileAttachment
    from app.models.chat import KlaudiaMessage
    from app.services.core.orchestrator import KlaudiaOrchestrator

    container = orchestrator_container()
    container.settings.extraction_mode = "sync"
    extraction = {
        "file_id": 5,
        "file_name": "receipt.png",
        "pages": [{"extraction": {"merchant": "Taxi vendor"}}],
        "status": "completed",
        "summary": "Receipt extracted",
    }
    container.extraction_agent.process.return_value = SimpleNamespace(**extraction)
    orchestrator = KlaudiaOrchestrator(container)

    async def extracted_events(*args):
        """Supply the same completed extraction contract as the queue path."""
        yield {"__final__": True, "payload": [extraction]}

    orchestrator._run_extraction_stream = extracted_events
    arguments = dict(
        messages=[
            KlaudiaMessage(
                role="user",
                content="Read this receipt",
                attachments=[
                    FileAttachment(
                        filename="receipt.png",
                        content_type="image/png",
                        data=b"fixture",
                    )
                ],
            )
        ],
        session_id=7,
        user_id=42,
    )
    if streaming:
        events = [event async for event in orchestrator.stream(**arguments)]
        assert events[-1]["type"] == "done"
    else:
        await orchestrator.process(**arguments)
        container.extraction_agent.process.assert_awaited_once()
    turn = container.main_chat.run.await_args.args[0]
    assert turn.document_ids == (5,)
    assert "Taxi vendor" in turn.extraction_contexts[0]


async def test_enforced_numeric_failure_preserves_receipts():
    """Reject ungrounded prose without rewriting or discarding a known commit."""
    from dataclasses import replace

    from app.models.chat import KlaudiaMessage
    from app.services.core.orchestrator import KlaudiaOrchestrator

    container = orchestrator_container()
    container.settings.numeric_verify_mode = "enforce"
    container.main_chat.run.return_value = replace(
        container.main_chat.run.return_value, content="The total is 999999."
    )
    response = await KlaudiaOrchestrator(container).process(
        [KlaudiaMessage(role="user", content="Read the total")], 7, 42
    )
    assert "999999" not in response.message.content
    assert "could not verify" in response.message.content
    assert response.operation_receipts[0]["status"] == "committed"


async def test_input_rejection_reports_selected_runtime_without_running_agent():
    """Input guardrails stop the main route before it can invoke any tools."""
    from app.models.chat import KlaudiaMessage
    from app.services.core.orchestrator import KlaudiaOrchestrator

    container = orchestrator_container()
    container.guardrails.validate_input.return_value = SimpleNamespace(
        passed=False, rejection_message="Request blocked"
    )
    response = await KlaudiaOrchestrator(container).process(
        [KlaudiaMessage(role="user", content="Blocked request")], 7, 42
    )
    assert response.runtime == "main"
    assert response.run_status == "rejected"
    container.main_chat.run.assert_not_awaited()
