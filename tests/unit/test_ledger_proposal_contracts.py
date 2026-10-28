"""Preserve proposal validation, schemas and stored JSON across contract moves."""

from importlib import import_module
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from klaudia.core.agent.writes import ExecuteOperation, PrepareTableAppend
from ledger.authoring import AuthoringProposal
from ledger.operations import AppendRows
from ledger.table_operations import TableAppend, TableAppendProposal, _request_payload

CONTRACTS = (
    AppendRows,
    TableAppendProposal,
    TableAppend,
    AuthoringProposal,
    PrepareTableAppend,
    ExecuteOperation,
)


@pytest.fixture(scope="module")
def original_contracts():
    fixture = Path(__file__).with_name("fixtures") / "ledger_proposal_contracts.json"
    return json.loads(fixture.read_text())


@pytest.mark.parametrize("contract", CONTRACTS, ids=lambda model: model.__name__)
def test_original_json_schema_is_preserved(contract, original_contracts):
    assert (
        contract.model_json_schema() == original_contracts["schemas"][contract.__name__]
    )


@pytest.mark.parametrize("contract", CONTRACTS, ids=lambda model: model.__name__)
def test_original_payload_serialization_is_preserved(contract, original_contracts):
    saved = original_contracts["payloads"][contract.__name__]
    assert contract.model_validate(saved["input"]).model_dump_json() == saved["json"]


@pytest.mark.parametrize("contract", CONTRACTS, ids=lambda model: model.__name__)
def test_original_json_can_be_reloaded(contract, original_contracts):
    saved = original_contracts["payloads"][contract.__name__]["json"]
    assert contract.model_validate_json(saved).model_dump_json() == saved


def test_original_append_storage_payload_is_preserved(original_contracts):
    request = TableAppend.model_validate(
        original_contracts["payloads"]["TableAppend"]["input"]
    )
    assert _request_payload(request) == original_contracts["append_storage_payload"]


@pytest.mark.parametrize(
    "changes",
    [
        {"table_id": ""},
        {"expected_sheet_revision": True},
        {"expected_sheet_revision": -1},
        {"expected_catalogue_revision": "3"},
        {"expected_catalogue_revision": 0},
        {"records": []},
        {"records": [{}]},
        {"records": [{"": 1}]},
        {"records": [{"Amount": {"value": 1}}]},
        {"records": [{"Amount": float("nan")}]},
        {"records": [{"Amount": float("inf")}]},
        {"records": [{"Amount": 1}] * 101},
        {"user_id": 42},
        {"idempotency_key": "model-selected"},
    ],
)
def test_append_proposal_rejects_invalid_or_authority_fields(
    changes, original_contracts
):
    original = original_contracts["payloads"]["TableAppendProposal"]["input"]
    with pytest.raises(ValidationError):
        TableAppendProposal.model_validate(original | changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"definition": None},
        {"headers": None},
        {"action": "register_table"},
        {"action": "refresh_table"},
        {"column_ids": ["col_claim", "col_amount"]},
        {"table_id": "tbl_claims"},
        {"expected_sheet_revision": 2},
        {"user_id": 42},
    ],
)
def test_authoring_rejects_ambiguous_action_fields(changes, original_contracts):
    original = original_contracts["payloads"]["AuthoringProposal"]["input"]
    with pytest.raises(ValidationError):
        AuthoringProposal.model_validate(original | changes)


@pytest.mark.parametrize(
    ("legacy_module", "contract_module", "name"),
    [
        ("operations", "append_contracts", "CellValue"),
        ("operations", "append_contracts", "AppendRow"),
        ("operations", "append_contracts", "AppendRows"),
        ("table_operations", "append_contracts", "RecordField"),
        ("table_operations", "append_contracts", "TableRecord"),
        ("table_operations", "append_contracts", "TableAppendProposal"),
        ("table_operations", "append_contracts", "TableAppend"),
        ("authoring", "authoring_contracts", "AuthoringProposal"),
    ],
)
def test_legacy_imports_retain_contract_identity(legacy_module, contract_module, name):
    original = getattr(import_module(f"ledger.{legacy_module}"), name)
    canonical = getattr(import_module(f"ledger.{contract_module}"), name)
    assert original is canonical
