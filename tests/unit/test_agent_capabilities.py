"""Exercise agent-owned tool contracts with isolated capability implementations."""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from klaudia.core.agent.agent import MainAgent
from klaudia.core.agent.context import TaskContext
from tests.agent_compatibility import read_fixture, tool_contracts
from tests.unit.test_main_agent import ScriptedModel, call


def capability_tools():
    """Bind the consumer interfaces without application stores or policy code.

    Returns:
        Fake implementations and the four agent-owned tools.
    """
    from klaudia.core.agent.documents import DocumentTools
    from klaudia.core.agent.memory import MemoryTools
    from klaudia.core.agent.reconciliation import PolicyReconciliationTools
    from klaudia.core.agent.ports.documents import DocumentReader
    from klaudia.core.agent.ports.memory import MemoryReader
    from klaudia.core.agent.ports.reconciliation import PolicyReconciler

    documents = AsyncMock(spec=DocumentReader)
    memory = AsyncMock(spec=MemoryReader)
    reconciliation = AsyncMock(spec=PolicyReconciler)
    return (documents, memory, reconciliation), (
        *DocumentTools(documents).tools,
        MemoryTools(memory).read_tool,
        PolicyReconciliationTools(reconciliation).tool,
    )


def test_consumer_tools_keep_the_complete_release_contract() -> None:
    """Keep titles, nested definitions, defaults and tool descriptions unchanged."""
    _, tools = capability_tools()
    saved = read_fixture("runtime.json")["tools"]
    assert tool_contracts(list(tools)) == {
        tool.name: saved[tool.name] for tool in tools
    }


async def test_scripted_agent_uses_only_supplied_capabilities() -> None:
    """Return backend evidence without adding authority or changing exact values."""
    implementations, tools = capability_tools()
    documents, memory, reconciliation = implementations
    saved = read_fixture("evidence.json")
    documents.search.return_value = saved["document_search"]["response"]
    documents.read.return_value = saved["document_page"]["response"]
    memory.read.return_value = saved["memory"]["response"]
    reconciliation.reconcile.return_value = saved["policy"]["response"]
    requests = {
        request["tool"]: json.loads(request["json"])
        for request in read_fixture("requests.json")
    }
    arguments = [requests[tool.name] for tool in tools]
    model = ScriptedModel(
        [
            *[
                call(tool.name, fields, tool.name)
                for tool, fields in zip(tools, arguments)
            ],
            AIMessage(content="Evidence read."),
        ]
    )
    outcome = await MainAgent(
        model,
        AsyncMock(),
        archive_tools=tools[:2],
        memory_tool=tools[2],
        reconciliation_tool=tools[3],
    ).run("Read evidence", TaskContext(user_id=42))
    documents.search.assert_awaited_once_with(**arguments[0])
    documents.read.assert_awaited_once_with(**arguments[1])
    memory.read.assert_awaited_once_with(**arguments[2])
    checked = reconciliation.reconcile.await_args.args[0]
    assert checked.model_dump(mode="json") == arguments[3]
    assert [json.loads(entry[2]) for entry in outcome.tool_evidence] == [
        saved["document_search"]["response"],
        saved["document_page"]["response"],
        saved["memory"]["response"],
        saved["policy"]["response"],
    ]


@pytest.mark.parametrize("index", range(4))
async def test_capability_infrastructure_failures_propagate(index: int) -> None:
    """Do not turn storage or transport failures into empty evidence.

    Args:
        index: Tool whose bound implementation must fail.
    """
    implementations, tools = capability_tools()
    documents, memory, reconciliation = implementations
    methods = (documents.search, documents.read, memory.read, reconciliation.reconcile)
    methods[index].side_effect = ConnectionError("Capability unavailable")
    arguments = next(
        request["input"]
        for request in read_fixture("requests.json")
        if request["tool"] == tools[index].name
    )
    with pytest.raises(ConnectionError, match="Capability unavailable"):
        await tools[index].ainvoke(arguments)
