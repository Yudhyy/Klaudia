"""Formula acceptance requires exact receipts, not plausible final answers."""

from copy import deepcopy

import pytest

from tests.e2e.checks import ResponseView, evaluate
from tests.e2e.schema import Expect
from tests.e2e.sut import attempted_capabilities


def formula_receipt():
    """Return independent evidence for one successful native calculation."""
    return {
        "operation_id": "operation-one",
        "status": "committed",
        "operation_type": "edit_typed_cells",
        "target": {"spreadsheet_id": "workbook", "sheet_id": 12},
        "calculation_status": "current",
        "accounting_validation": "not_run",
        "calculation": {
            "engine_version": "native-decimal-v1",
            "invalidated": 1,
            "recalculated": 1,
            "failed": 0,
            "results": [
                {
                    "cell_id": "total",
                    "status": "current",
                    "value": "0.50",
                    "error": None,
                }
            ],
        },
    }


def expected_receipt(receipt):
    """Select the stable contract independently of generated operation identity."""
    return {
        "workbook_id": "workbook",
        "sheet_id": 12,
        "calculation_status": receipt["calculation_status"],
        "calculation": deepcopy(receipt["calculation"]),
    }


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("status", "prepared"),
        ("operation_type", "append_rows"),
        ("operation_id", ""),
        ("operation_id", ["operation-one"]),
        ("target", {"spreadsheet_id": "other", "sheet_id": 12}),
        ("target", {"spreadsheet_id": "workbook", "sheet_id": 13}),
        ("target", {"spreadsheet_id": "workbook", "sheet_id": 12.0}),
        ("calculation_status", "failed"),
        ("accounting_validation", "passed"),
        ("calculation", {}),
    ],
)
def test_formula_receipt_rejects_wrong_identity_or_status(field, replacement):
    """A correct-looking amount cannot mask the wrong operation or destination."""
    receipt = formula_receipt()
    expected = Expect(formula_receipts=[expected_receipt(receipt)])
    view = ResponseView("Total: 0.50", [], 1, operation_receipts=[receipt])
    assert evaluate(expected, view).passed
    receipt[field] = replacement
    assert not evaluate(expected, view).passed


@pytest.mark.parametrize("value", [0.5, "0.5", "0.30", None])
def test_formula_receipt_preserves_exact_result_text(value):
    """Float conversion, lost scale, stale caches and null results fail grading."""
    receipt = formula_receipt()
    expected = Expect(formula_receipts=[expected_receipt(receipt)])
    receipt["calculation"]["results"][0]["value"] = value
    view = ResponseView("Total: 0.50", [], 1, operation_receipts=[receipt])
    assert not evaluate(expected, view).passed


@pytest.mark.parametrize("field", ["invalidated", "recalculated", "failed"])
def test_formula_receipt_requires_actual_calculation_counts(field):
    """Wrong recalculation counts fail even when the result text is correct."""
    receipt = formula_receipt()
    expected = Expect(formula_receipts=[expected_receipt(receipt)])
    receipt["calculation"][field] += 1
    assert not evaluate(
        expected, ResponseView("", [], 1, operation_receipts=[receipt])
    ).passed


def test_formula_receipts_require_complete_distinct_operation_sequence():
    """Missing, extra and replayed receipts cannot substitute for distinct edits."""
    first = formula_receipt()
    second = {**deepcopy(first), "operation_id": "operation-two"}
    second["calculation"]["results"][0]["value"] = "0.70"
    expected = Expect(
        formula_receipts=[expected_receipt(first), expected_receipt(second)]
    )
    for receipts in (
        [],
        [first],
        [first, first],
        [first, second, first],
        [second, first],
    ):
        assert not evaluate(
            expected, ResponseView("", [], 1, operation_receipts=receipts)
        ).passed
    assert evaluate(
        expected, ResponseView("", [], 1, operation_receipts=[first, second])
    ).passed


def test_formula_receipt_rejects_boolean_sheet_identity():
    """JSON true cannot satisfy an integer sheet identity of one."""
    receipt = formula_receipt()
    expected = expected_receipt(receipt)
    expected["sheet_id"] = 1
    receipt["target"]["sheet_id"] = True
    assert not evaluate(
        Expect(formula_receipts=[expected]),
        ResponseView("", [], 1, operation_receipts=[receipt]),
    ).passed


def test_empty_formula_receipt_expectation_forbids_mutation():
    """An explicitly empty sequence requires no observed commits."""
    expected = Expect(formula_receipts=[])
    assert evaluate(expected, ResponseView("", [], 1)).passed
    assert not evaluate(
        expected, ResponseView("", [], 1, operation_receipts=[formula_receipt()])
    ).passed


def test_formula_failure_requires_null_result_and_error_evidence():
    """A committed calculation failure is valid only when explicitly expected."""
    receipt = formula_receipt()
    receipt["calculation_status"] = "failed"
    receipt["calculation"]["failed"] = 1
    receipt["calculation"]["results"] = [
        {
            "cell_id": "total",
            "status": "failed",
            "value": None,
            "error": "invalid_or_inexact_arithmetic",
        }
    ]
    expected = Expect(formula_receipts=[expected_receipt(receipt)])
    view = ResponseView("Calculation failed", [], 1, operation_receipts=[receipt])
    assert evaluate(expected, view).passed
    receipt["calculation"]["results"][0]["value"] = "0.50"
    assert not evaluate(expected, view).passed


def test_shared_execute_tool_does_not_claim_append_capability():
    """Typed edits and generic execution must not inflate append capability scores."""
    assert attempted_capabilities(["execute_operation"]) == []
    assert attempted_capabilities(["prepare_typed_edit", "inspect_typed_workbook"]) == [
        "edit_typed_cells",
        "inspect_typed_workbook",
    ]
    assert attempted_capabilities(["prepare_table_append", "execute_operation"]) == [
        "append_records"
    ]
