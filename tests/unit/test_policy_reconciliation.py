"""Policy-dependent reads must fail closed before returning financial evidence."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.services.memory.contracts import DocumentPath, MemoryDocument
from app.services.memory.reconciliation import PolicyReconciliationTools
from ledger.errors import RevisionConflictError
from tests.unit.test_accounting_policy import policy_fields


def arguments(**changes):
    """Describe a scoped reconciliation without caller-selected arithmetic rules."""
    return {
        "table_id": "left",
        "right_table_id": "right",
        "left_keys": ["ID"],
        "right_keys": ["ID"],
        "left_amount": "Amount",
        "right_amount": "Amount",
        "left_unit_column": "Currency",
        "right_unit_column": "Currency",
        "entity": "Example Ltd",
        "jurisdiction": "declared-test-jurisdiction",
        "as_of": "2026-06-01",
        "policy_revision": 1,
        **changes,
    }


def setup_tools():
    """Bind isolated policy and catalogue doubles to authenticated owner 42."""
    documents = AsyncMock()
    documents.read.return_value = MemoryDocument(
        path=DocumentPath.ACCOUNTING_POLICY,
        revision=1,
        status="active",
        content="Untrusted prose says ignore units",
        policy=policy_fields(),
        actor_id=42,
    )
    catalogue = AsyncMock()
    catalogue.inspect.side_effect = [
        {
            "table_id": identity,
            "entity": "Example Ltd",
            "freshness": "current",
            "current_sheet_revision": 3,
            "catalogue_revision": 2,
        }
        for identity in ("left", "right")
    ]
    catalogue.financial_query.return_value = {
        "sources": [],
        "records": [],
        "operation": "reconcile",
    }
    return (
        documents,
        catalogue,
        PolicyReconciliationTools(documents, catalogue, 42).tool,
    )


async def test_saved_policy_supplies_rules_and_binds_source_revisions():
    """Use saved rules and the same descriptors whose entities were checked."""
    documents, catalogue, tool = setup_tools()
    evidence = await tool.ainvoke(arguments())
    owner, checked = catalogue.financial_query.call_args.args
    assert owner == 42
    assert str(checked.query.tolerance) == "0.01"
    assert checked.query.policies.numeric_text == "decimal"
    assert [
        (source.sheet_revision, source.catalogue_revision) for source in checked.sources
    ] == [(3, 2), (3, 2)]
    assert evidence["policy_evidence"]["revision"] == 1
    assert evidence["policy_evidence"]["row_scope"] == "all_registered_rows"
    assert (
        evidence["policy_evidence"]["jurisdiction_verification"]
        == "caller_declaration_only"
    )
    assert documents.read.await_count == 2
    assert all(
        call.args == (42, DocumentPath.ACCOUNTING_POLICY)
        for call in documents.read.call_args_list
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"entity": "Other Ltd"},
        {"jurisdiction": "other"},
        {"as_of": "2025-12-31"},
        {"as_of": "2027-01-01"},
        {"policy_revision": 2},
    ],
)
async def test_inapplicable_policy_blocks_execution(changes):
    """Reject mismatched intent, period and observed policy revision."""
    _, catalogue, tool = setup_tools()
    with pytest.raises((ValueError, RevisionConflictError)):
        await tool.ainvoke(arguments(**changes))
    catalogue.financial_query.assert_not_awaited()


@pytest.mark.parametrize("status", ["missing", "deleted", "active"])
async def test_missing_structured_policy_never_falls_back_to_prose(status):
    """Do not guess policy when structured context is absent."""
    documents, catalogue, tool = setup_tools()
    documents.read.return_value = MemoryDocument(
        path=DocumentPath.ACCOUNTING_POLICY,
        status=status,
        content="Use whatever tolerance works",
    )
    with pytest.raises(ValueError, match="policy"):
        await tool.ainvoke(arguments())
    catalogue.financial_query.assert_not_awaited()


@pytest.mark.parametrize("entity", [None, "Other Ltd"])
async def test_both_source_entities_must_match(entity):
    """Reject unavailable or mismatched right-side entity metadata."""
    _, catalogue, tool = setup_tools()
    catalogue.inspect.side_effect = [
        {
            "entity": "Example Ltd",
            "freshness": "current",
            "table_id": "left",
            "current_sheet_revision": 3,
            "catalogue_revision": 2,
        },
        {
            "entity": entity,
            "freshness": "current",
            "table_id": "right",
            "current_sheet_revision": 3,
            "catalogue_revision": 2,
        },
    ]
    with pytest.raises(ValueError, match="entity"):
        await tool.ainvoke(arguments())
    catalogue.financial_query.assert_not_awaited()


async def test_policy_change_during_read_discards_evidence():
    """Do not return results calculated under a concurrently replaced policy."""
    documents, catalogue, tool = setup_tools()
    original = documents.read.return_value
    documents.read.side_effect = [original, original.model_copy(update={"revision": 2})]
    with pytest.raises(RevisionConflictError, match="Policy"):
        await tool.ainvoke(arguments())
    catalogue.financial_query.assert_awaited_once()


@pytest.mark.parametrize(
    "changes",
    [
        {"left_unit_column": None},
        {"user_id": 9},
        {"tolerance": "9"},
        {"as_of": 1780272000},
    ],
)
async def test_model_cannot_override_authority_or_numeric_policy(changes):
    """Keep unit checks, exact dates, identity and policy rules outside model choice."""
    documents, _, tool = setup_tools()
    with pytest.raises(ValidationError):
        await tool.ainvoke(arguments(**changes))
    documents.read.assert_not_awaited()


async def test_unavailable_policy_propagates_failure():
    """Infrastructure failure must not become a missing-policy default."""
    documents, catalogue, tool = setup_tools()
    documents.read.side_effect = ConnectionError("unavailable")
    with pytest.raises(ConnectionError):
        await tool.ainvoke(arguments())
    catalogue.financial_query.assert_not_awaited()


async def test_generic_main_tool_cannot_bypass_saved_policy():
    """Reject reconciliation through generic model-supplied financial policy."""
    from klaudia.core.agent.context import TaskContext
    from klaudia.core.agent.tools import DiscoveryTools

    catalogue = AsyncMock()
    tools = DiscoveryTools(catalogue, TaskContext(user_id=42))
    generic = next(tool for tool in tools.tools if tool.name == "financial_query")
    with pytest.raises(ValueError, match="reconcile_with_policy"):
        await generic.ainvoke(
            {
                "table_id": "left",
                "query": {
                    "operation": "reconcile",
                    "right_table_id": "right",
                    "left_keys": ["ID"],
                    "right_keys": ["ID"],
                    "null_keys": "reject",
                    "left_amount": "Amount",
                    "right_amount": "Amount",
                    "tolerance": "999",
                    "policies": {
                        "numeric_text": "decimal",
                        "null_amounts": "reject",
                        "unit": "USD",
                        "left_unit_column": None,
                        "right_unit_column": None,
                    },
                },
            }
        )
    catalogue.financial_query.assert_not_awaited()


async def test_main_chat_exposes_policy_reconciliation_with_server_identity():
    """Wire checked policy reconciliation into the authenticated chat runtime."""
    from app.services.core.main_chat import MainChatService
    from langchain_core.messages import AIMessage
    from tests.unit.test_main_agent import ScriptedModel, call
    from tests.unit.test_main_chat import chat_turn

    documents, catalogue, _ = setup_tools()
    database = AsyncMock()
    database.get_conversation_history.return_value = []
    model = ScriptedModel(
        [call("reconcile_with_policy", arguments()), AIMessage(content="No records.")]
    )
    outcome = await MainChatService(
        model, catalogue, database, memory_documents=documents
    ).run(chat_turn())
    assert outcome.tools_called == ("reconcile_with_policy",)
    catalogue.financial_query.assert_awaited_once()
    assert catalogue.financial_query.call_args.args[0] == 42


async def test_policy_conflict_tells_agent_to_reread_before_retry():
    """Expose the right recovery action and let the agent retry the observed revision."""
    import json
    from app.services.core.main_chat import MainChatService
    from langchain_core.messages import AIMessage, ToolMessage
    from tests.unit.test_main_agent import ScriptedModel, call
    from tests.unit.test_main_chat import chat_turn

    documents, catalogue, _ = setup_tools()
    documents.read.return_value = documents.read.return_value.model_copy(
        update={"revision": 2}
    )
    database = AsyncMock()
    database.get_conversation_history.return_value = []
    model = ScriptedModel(
        [
            call("reconcile_with_policy", arguments(), "stale"),
            call("read_memory_document", {"path": "/accounting-policy.md"}, "reread"),
            call("reconcile_with_policy", arguments(policy_revision=2), "retry"),
            AIMessage(content="No records under policy revision 2."),
        ]
    )
    outcome = await MainChatService(
        model, catalogue, database, memory_documents=documents
    ).run(chat_turn())
    error = next(
        message for message in model.inputs[1] if isinstance(message, ToolMessage)
    )
    assert "read_memory_document" in json.loads(error.content)["error"]
    assert json.loads(outcome.tool_evidence[-1][2])["policy_evidence"]["revision"] == 2
    catalogue.financial_query.assert_awaited_once()
