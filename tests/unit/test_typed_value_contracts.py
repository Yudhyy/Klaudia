"""Typed cell contracts preserve raw kinds and require financial scale choices."""

import pytest
from pydantic import ValidationError

from ledger.typed_contracts import FormulaEdit, TypedValue


@pytest.mark.parametrize(
    "kind,value",
    [
        ("decimal", 0.1),
        ("decimal", "1,000"),
        ("decimal", True),
        ("boolean", 1),
        ("boolean", "true"),
        ("date", "20260914"),
        ("date", "2026-02-30"),
        ("text", 42),
    ],
)
def test_typed_values_reject_implicit_conversion(kind, value):
    """Input types cannot silently change raw values or infer locale."""
    with pytest.raises(ValidationError):
        TypedValue(kind=kind, value=value)


@pytest.mark.parametrize(
    "kind,value",
    [
        ("decimal", "+001.2300"),
        ("text", "00123"),
        ("boolean", False),
        ("date", "2026-09-14"),
    ],
)
def test_typed_values_preserve_declared_raw_payload(kind, value):
    """Typed storage keeps the supplied raw representation distinct from display."""
    assert TypedValue(kind=kind, value=value).value == value


def test_persisted_formula_requires_explicit_financial_rounding():
    """An engine's exact mode cannot omit a ledger result's scale policy."""
    with pytest.raises(ValidationError, match="explicit"):
        FormulaEdit(
            action="set_formula",
            sheet_id=1,
            row=2,
            column=1,
            formula={"operation": "sum", "operands": [{"literal": "1"}]},
        )
