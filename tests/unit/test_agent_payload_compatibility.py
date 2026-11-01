"""Preserve saved tool requests, expected failures and capability evidence."""

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.adapters.agent.catalogue import CatalogueAdapter
from app.services.memory.contracts import MemoryDocument
from app.services.memory.tools import MemoryReader
from app.services.core.archive_tools import ArchiveReader
from klaudia.core.agent.memory import MemoryTools
from klaudia.core.agent.agent import MainAgent
from klaudia.core.agent.context import TaskContext
from ledger_core.errors import RevisionConflictError
from ledger_core.resources import ResourceExistsError, ResourceNotFoundError
from tests.agent_compatibility import PROFILES, agent_options, read_fixture
from tests.unit.test_main_agent import ScriptedModel, call
from tests.unit.test_policy_reconciliation import arguments, setup_tools

ERRORS = {
    "ResourceExistsError": ResourceExistsError,
    "ResourceNotFoundError": ResourceNotFoundError,
    "RevisionConflictError": RevisionConflictError,
    "ValueError": ValueError,
}


async def test_saved_request_json_and_defaults_match_current_tool_contracts() -> None:
    """Load original requests and preserve defaults, unions and serialized values."""
    context = TaskContext(user_id=42)
    model = ScriptedModel([AIMessage(content="Ready.")])
    await MainAgent(
        model, AsyncMock(), **agent_options(list(PROFILES.values())[-1], context)
    ).run("Inspect requests", context)
    schemas = {tool.name: tool.args_schema for tool in model.tools}
    saved = read_fixture("requests.json")
    assert {request["tool"] for request in saved} == set(schemas)
    for request in saved:
        schema = schemas[request["tool"]]
        assert (
            schema.model_validate(request["input"]).model_dump_json() == request["json"]
        )
        assert (
            schema.model_validate_json(request["json"]).model_dump_json()
            == request["json"]
        )


@pytest.mark.parametrize(
    "case",
    [
        "overlap",
        "missing",
        "revision",
        "policy_revision",
        "value",
        "long_value",
        "invalid_arguments",
        "unknown_tool",
    ],
)
async def test_saved_domain_error_is_still_model_visible(case: str) -> None:
    """Keep expected failure text and the error ToolMessage contract unchanged."""
    saved = read_fixture("errors.json")[case]
    catalogue = AsyncMock()
    options = {}
    if case == "policy_revision":
        _, catalogue, tool = setup_tools()
        options["reconciliation_tool"] = tool
        catalogue.financial_query.side_effect = RevisionConflictError("Source changed")
    elif "failure" in saved:
        failure = saved["failure"]
        catalogue.inspect.side_effect = ERRORS[failure["type"]](failure["message"])
    model = ScriptedModel(
        [call(saved["tool"], saved["arguments"]), AIMessage(content="Observed error.")]
    )
    await MainAgent(model, CatalogueAdapter(catalogue), **options).run(
        "Inspect", TaskContext(user_id=42)
    )
    observed = model.inputs[1][-1]
    assert {
        "content": observed.content,
        "status": observed.status,
        "name": observed.name,
    } == saved["response"]


async def test_saved_document_and_memory_evidence_payloads() -> None:
    """Preserve pagination and trust fields produced by application tools."""
    saved = read_fixture("evidence.json")
    database = AsyncMock()
    database.fetchall.return_value = saved["document_search"]["rows"]
    database.fetchone.return_value = dict(saved["document_page"]["row"])
    tools = ArchiveReader(database, 42)
    assert (
        await tools.search(**saved["document_search"]["arguments"])
        == saved["document_search"]["response"]
    )
    assert (
        await tools.read(**saved["document_page"]["arguments"])
        == saved["document_page"]["response"]
    )
    memory = AsyncMock()
    memory.read.return_value = MemoryDocument.model_validate(
        saved["memory"]["document"]
    )
    assert (
        await MemoryTools(MemoryReader(memory, 42)).read_tool.ainvoke(
            saved["memory"]["arguments"]
        )
        == saved["memory"]["response"]
    )


async def test_saved_policy_evidence_and_checked_request_json() -> None:
    """Keep exact policy scope and revision-bound financial request fields."""
    saved = read_fixture("evidence.json")["policy"]
    _, catalogue, tool = setup_tools()
    catalogue.financial_query.return_value = saved["financial_response"]
    assert await tool.ainvoke(arguments()) == saved["response"]
    owner, request = catalogue.financial_query.await_args.args
    assert owner == 42
    assert request.model_dump_json() == saved["checked_request_json"]
