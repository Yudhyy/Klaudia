"""Grade capability attempts separately from calculation and mutation evidence."""

import json
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictStr, ValidationError

from tests.e2e.schema import Expect, FormulaReceiptExpectation, MetricExpectation

if TYPE_CHECKING:
    from tests.e2e.checks import ResponseView


class _Metric(BaseModel):
    """Validated numeric observation from the tool boundary."""

    model_config = ConfigDict(allow_inf_nan=False)
    column: StrictStr
    operation: Literal["sum", "count"]
    value: Decimal | None


class _Group(BaseModel):
    """Observed grouping keys and their labelled values."""

    key: dict[str, JsonValue]
    metrics: list[_Metric]


class _Source(BaseModel):
    """Required stable table identity for calculation evidence."""

    table_id: StrictStr


class _Calculation(BaseModel):
    """Minimum typed calculation evidence accepted by the grader."""

    source: _Source
    groups: list[_Group]


class _CommittedReceipt(BaseModel):
    """Minimum receipt fields needed to count distinct observed commits."""

    status: Literal["committed"]
    operation_id: StrictStr = Field(min_length=1)


def _metric_matches(expected: MetricExpectation, calculation: dict) -> bool:
    """Match a labelled value from the expected source and grouping keys.

    Args:
        expected: Fixture-generated exact metric expectation.
        calculation: Observed calculate tool output.

    Returns:
        Whether one metric matches all declared evidence fields.
    """
    try:
        observed = _Calculation.model_validate(calculation)
    except ValidationError:
        return False
    if observed.source.table_id != expected.table_id:
        return False
    for group in observed.groups:
        if group.key != expected.group:
            continue
        for metric in group.metrics:
            if (
                metric.column != expected.column
                or metric.operation != expected.operation
            ):
                continue
            if metric.value == expected.value:
                return True
    return False


def check_capabilities(expect: Expect, view: "ResponseView") -> dict[str, bool]:
    """Require requested evidence even when an adapter cannot observe it.

    Args:
        expect: Declared capability and financial evidence checks.
        view: Adapter observations, never model-invented proof.

    Returns:
        Results for requested checks; missing observations fail.
    """
    checks = {}
    if expect.formula_receipts is not None:
        checks["formula_receipts"] = _formula_receipts_match(
            expect.formula_receipts, view.operation_receipts
        )
    if expect.capabilities_all:
        checks["capabilities_all"] = set(expect.capabilities_all).issubset(
            view.capabilities_attempted
        )
    if expect.metric_evidence:
        checks["metric_evidence"] = all(
            any(
                _metric_matches(metric, calculation)
                for calculation in view.calculations
            )
            for metric in expect.metric_evidence
        )
    if expect.committed_operations_min is not None:
        committed = set()
        for receipt in view.operation_receipts:
            try:
                committed.add(_CommittedReceipt.model_validate(receipt).operation_id)
            except ValidationError:
                continue
        checks["committed_operations_min"] = (
            len(committed) >= expect.committed_operations_min
        )
    if expect.ledger_state:
        checks["ledger_state"] = view.ledger_state is not None and json.dumps(
            view.ledger_state, sort_keys=True, allow_nan=False
        ) == json.dumps(expect.ledger_state, sort_keys=True, allow_nan=False)
    return checks


def _formula_receipts_match(
    expected: list[FormulaReceiptExpectation], receipts: list[dict]
) -> bool:
    """Compare the full ordered calculation evidence without numeric coercion.

    Args:
        expected: Fixture-authored outcomes for every operation in this turn.
        receipts: Receipts observed at the service boundary.

    Returns:
        Whether distinct commits match every target, status and calculation field.
    """
    if len(expected) != len(receipts):
        return False
    identities = set()
    for outcome, receipt in zip(expected, receipts, strict=True):
        try:
            identity = _CommittedReceipt.model_validate(receipt).operation_id
        except ValidationError:
            return False
        if identity in identities:
            return False
        identities.add(identity)
        required = {
            "operation_type": "edit_typed_cells",
            "target": {
                "spreadsheet_id": outcome.workbook_id,
                "sheet_id": outcome.sheet_id,
            },
            "accounting_validation": "not_run",
            "calculation_status": outcome.calculation_status,
            "calculation": outcome.calculation,
        }
        observed = {field: receipt.get(field) for field in required}
        try:
            if json.dumps(observed, sort_keys=True, allow_nan=False) != json.dumps(
                required, sort_keys=True, allow_nan=False
            ):
                return False
        except (TypeError, ValueError):
            return False
    return True
