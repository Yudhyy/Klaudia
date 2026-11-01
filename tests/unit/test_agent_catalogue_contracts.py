"""Check consumer read contracts and translation into canonical ledger requests."""

import importlib
from unittest.mock import AsyncMock

import pytest

from klaudia.core.agent.context import TaskContext
from klaudia.core.agent.tools import DiscoveryTools
from tests.agent_compatibility import read_fixture, tool_contracts


def test_read_tools_use_consumer_contracts() -> None:
    """Keep the complete release schemas while removing ledger model ownership."""
    tools = DiscoveryTools(AsyncMock(), TaskContext(user_id=42)).tools
    saved = read_fixture("runtime.json")["tools"]
    assert tool_contracts(list(tools)) == {
        tool.name: saved[tool.name] for tool in tools
    }
    assert all(tool.args_schema.__module__.startswith("klaudia.") for tool in tools)


@pytest.mark.parametrize(
    "case",
    [
        ("search", "catalogue", "resources", "ResourceSearch", "search_resources"),
        ("inspect", "catalogue", "resources", "ResourceInspection", "inspect_resource"),
        (
            "calculate",
            "calculations",
            "calculations",
            "CheckedCalculation",
            "calculate",
        ),
        (
            "financial_query",
            "financial",
            "financial_contracts",
            "CheckedFinancialRequest",
            "financial_query",
        ),
    ],
)
async def test_adapter_translates_read_requests(
    case: tuple[str, str, str, str, str],
) -> None:
    """Supply canonical ledger models with the same JSON and authenticated owner.

    Args:
        case: Capability method, schema modules, request class and saved tool name.
    """
    from app.adapters.agent.catalogue import CatalogueAdapter

    method, consumer_module, ledger_module, contract, tool_name = case
    consumer_type = getattr(
        importlib.import_module(f"klaudia.core.agent.ports.{consumer_module}"), contract
    )
    ledger_type = getattr(
        importlib.import_module(f"ledger_core.{ledger_module}"), contract
    )
    fields = next(
        saved["input"]
        for saved in read_fixture("requests.json")
        if saved["tool"] == tool_name
    ).copy()
    if method == "calculate":
        fields.update(expected_sheet_revision=7, expected_catalogue_revision=3)
    if method == "financial_query":
        fields["sources"] = [
            {
                "table_id": fields["table_id"],
                "sheet_revision": 7,
                "catalogue_revision": 3,
            }
        ]
    request = consumer_type.model_validate(fields)
    service = AsyncMock()
    evidence = {"exact_value": "0.123456789012345678901"}
    getattr(service, method).return_value = evidence
    observed = await getattr(CatalogueAdapter(service), method)(42, request)
    owner, checked = getattr(service, method).await_args.args
    assert owner == 42
    assert type(checked) is ledger_type
    assert checked.model_dump_json() == request.model_dump_json()
    assert observed is evidence


@pytest.mark.parametrize(
    "method", ["search", "inspect", "calculate", "financial_query"]
)
@pytest.mark.parametrize("failure", ["ResourceNotFoundError", "RevisionConflictError"])
async def test_adapter_maps_only_expected_read_failures(
    method: str, failure: str
) -> None:
    """Keep failure text and chaining without exposing a provider exception class.

    Args:
        method: Capability method under test.
        failure: Expected ledger failure name.
    """
    from ledger_core.errors import RevisionConflictError
    from ledger_core.resources import ResourceNotFoundError

    from app.adapters.agent.catalogue import CatalogueAdapter
    from klaudia.core.agent.ports import catalogue, errors
    from klaudia.core.agent.ports.calculations import CheckedCalculation
    from klaudia.core.agent.ports.financial import CheckedFinancialRequest

    requests = {
        "search": catalogue.ResourceSearch(intent="claims"),
        "inspect": catalogue.ResourceInspection(table_id="claims"),
        "calculate": CheckedCalculation(
            table_id="claims",
            metrics=[{"column": "Amount", "operation": "sum"}],
            expected_sheet_revision=7,
            expected_catalogue_revision=3,
        ),
        "financial_query": CheckedFinancialRequest(
            table_id="claims",
            query={"operation": "records", "columns": ["Amount"]},
            sources=[
                {"table_id": "claims", "sheet_revision": 7, "catalogue_revision": 3}
            ],
        ),
    }
    original = {
        "ResourceNotFoundError": ResourceNotFoundError,
        "RevisionConflictError": RevisionConflictError,
    }[failure]("Read rejected")
    service = AsyncMock()
    getattr(service, method).side_effect = original
    with pytest.raises(getattr(errors, failure), match="Read rejected") as caught:
        await getattr(CatalogueAdapter(service), method)(42, requests[method])
    assert caught.value.__cause__ is original


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionError("Database unavailable"),
        RuntimeError("Unknown failure"),
        ValueError("Narrow the request"),
    ],
)
async def test_adapter_preserves_unmapped_failures(failure: Exception) -> None:
    """Let storage, unknown and existing argument failures keep their identity.

    Args:
        failure: Failure that the adapter must not replace.
    """
    from app.adapters.agent.catalogue import CatalogueAdapter
    from klaudia.core.agent.ports.catalogue import ResourceInspection

    service = AsyncMock()
    service.inspect.side_effect = failure
    with pytest.raises(type(failure)) as caught:
        await CatalogueAdapter(service).inspect(
            42, ResourceInspection(table_id="claims")
        )
    assert caught.value is failure
