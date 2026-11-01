"""Check execution-scope translation and canonical backend validation."""

import importlib
from unittest.mock import AsyncMock

import pytest
from ledger_core.errors import ApprovalRequiredError, IdempotencyConflictError
from ledger_core.resources import ResourceExistsError
from pydantic import ValidationError

from app.adapters.agent.authoring import AuthoringAdapter
from app.adapters.agent.errors import ledger_failures
from app.adapters.agent.operations import OperationAdapter
from klaudia.core.agent.ports import errors
from klaudia.core.agent.ports.authoring import AuthoringProposal
from klaudia.core.agent.ports.typed import TypedEditProposal
from tests.agent_compatibility import read_fixture


@pytest.mark.parametrize(
    "case",
    [
        (
            "prepare",
            "append",
            "append_contracts",
            "TableAppendProposal",
            "prepare_table_append",
        ),
        (
            "prepare_authoring",
            "authoring",
            "authoring_contracts",
            "AuthoringProposal",
            "prepare_table_authoring",
        ),
        (
            "prepare_typed",
            "typed",
            "typed_contracts",
            "TypedEditProposal",
            "prepare_typed_edit",
        ),
    ],
)
async def test_operation_adapter_retains_request_json_and_executor(
    case: tuple[str, str, str, str, str],
) -> None:
    """Send canonical requests to the supplied executor and preserve proposal evidence.

    Args:
        case: Operation method, schema modules, request class and saved tool name.
    """
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
    if method == "prepare":
        fields.update(expected_sheet_revision=7, expected_catalogue_revision=3)
    request = consumer_type.model_validate(fields)
    executor = AsyncMock()
    prepared = {"operation_ref": "prepared:original", "approval_id": "checked:original"}
    getattr(executor, method).return_value = prepared
    assert await getattr(OperationAdapter(executor), method)(42, request) is prepared
    owner, canonical = getattr(executor, method).await_args.args
    assert owner == 42
    assert type(canonical) is ledger_type
    assert canonical.model_dump_json() == request.model_dump_json()


@pytest.mark.parametrize(
    "saved",
    [
        request
        for request in read_fixture("requests.json")
        if request["tool"] == "prepare_table_authoring"
    ],
)
async def test_each_authoring_action_keeps_its_canonical_payload(saved: dict) -> None:
    """Preserve all five lifecycle actions, optional fields and observed revisions.

    Args:
        saved: Original release authoring request.
    """
    executor = AsyncMock()
    await OperationAdapter(executor).prepare_authoring(
        42, AuthoringProposal.model_validate(saved["input"])
    )
    canonical = executor.prepare_authoring.await_args.args[1]
    assert canonical.model_dump_json() == saved["json"]


@pytest.mark.parametrize(
    "method, identity",
    [("inspect_typed", "workbook:original"), ("execute", "prepared:original")],
)
async def test_operation_adapter_reuses_selected_executor(
    method: str, identity: str
) -> None:
    """Keep ownership, original identities and returned evidence on the supplied scope.

    Args:
        method: Capability method that accepts an opaque identity.
        identity: Original workbook or operation reference.
    """
    executor = AsyncMock()
    evidence = {
        "operation_id": "committed:original",
        "exact_value": "0.123456789012345678901",
    }
    getattr(executor, method).return_value = evidence
    assert await getattr(OperationAdapter(executor), method)(42, identity) is evidence
    getattr(executor, method).assert_awaited_once_with(42, identity)


async def test_approval_pause_keeps_the_original_payload() -> None:
    """Translate approval control flow without replacing its durable identity."""
    approval = read_fixture("continuation.json")["approval"]
    original = ApprovalRequiredError(approval)
    executor = AsyncMock()
    executor.execute.side_effect = original
    with pytest.raises(errors.ApprovalRequiredError) as caught:
        await OperationAdapter(executor).execute(42, approval["operation_ref"])
    assert caught.value.approval is approval
    assert caught.value.__cause__ is original
    assert str(caught.value) == str(original)


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionError("Connection lost"),
        IdempotencyConflictError("Different request"),
        RuntimeError("Unknown failure"),
    ],
)
async def test_operation_failures_outside_the_contract_propagate(
    failure: Exception,
) -> None:
    """Do not turn an unknown write outcome or retry conflict into repairable evidence.

    Args:
        failure: Unmapped backend failure.
    """
    executor = AsyncMock()
    executor.execute.side_effect = failure
    with pytest.raises(type(failure)) as caught:
        await OperationAdapter(executor).execute(42, "prepared:original")
    assert caught.value is failure


async def test_overlap_failure_keeps_consumer_type_and_cause() -> None:
    """Translate the final expected failure without changing its text."""
    original = ResourceExistsError("Region overlaps")
    with pytest.raises(errors.ResourceExistsError, match="Region overlaps") as caught:
        with ledger_failures():
            raise original
    assert caught.value.__cause__ is original


@pytest.mark.parametrize(
    "change", ["decimal", "rounding", "duplicate", "arity", "date"]
)
async def test_typed_financial_validation_stays_in_canonical_ledger(
    change: str,
) -> None:
    """Reject invalid financial intent before the selected executor can persist it.

    Args:
        change: Ledger rule that the schema-only consumer request does not own.
    """
    fields = next(
        saved["input"]
        for saved in read_fixture("requests.json")
        if saved["tool"] == "prepare_typed_edit"
    )
    if change == "decimal":
        fields["edits"][0]["input"]["value"] = "9" * 65
    elif change == "rounding":
        fields["edits"][1]["formula"]["rounding"] = None
    elif change == "duplicate":
        fields["edits"].append(fields["edits"][0])
    elif change == "arity":
        fields["edits"][1]["formula"]["operation"] = "add"
    else:
        fields["edits"][0]["input"].update(kind="date", value="2026-02-30")
    request = TypedEditProposal.model_validate(fields)
    executor = AsyncMock()
    with pytest.raises(ValidationError):
        await OperationAdapter(executor).prepare_typed(42, request)
    executor.prepare_typed.assert_not_awaited()


async def test_authoring_action_validation_stays_in_canonical_ledger() -> None:
    """Keep invalid action combinations out of operation storage."""
    executor = AsyncMock()
    request = AuthoringProposal(action="unregister_table", table_id="claims")
    with pytest.raises(ValidationError):
        await OperationAdapter(executor).prepare_authoring(42, request)
    executor.prepare_authoring.assert_not_awaited()


async def test_authoring_reads_retain_identity_filters_and_evidence() -> None:
    """Keep sheet pagination and placement reads on the existing owned service."""
    service = AsyncMock()
    adapter = AuthoringAdapter(service)
    await adapter.sheets(42, workbook_id="workbook:original", offset=20)
    service.sheets.assert_awaited_once_with(
        42, workbook_id="workbook:original", offset=20
    )
    assert await adapter.inspect(42, 8, "A1:B2") is service.inspect.return_value
    service.inspect.assert_awaited_once_with(42, 8, "A1:B2")
