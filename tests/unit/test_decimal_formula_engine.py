"""Exact native formulas retain literals and explicit dependency behaviour."""

import pytest

from ledger.formula_engine import Formula, evaluate_graph


def formula(operation, *operands, **policy):
    """Build a native formula with explicit literal or cell operands."""
    return Formula.model_validate(
        {
            "operation": operation,
            "operands": [
                {"cell_id": value[1:]} if value.startswith("@") else {"literal": value}
                for value in operands
            ],
            **policy,
        }
    )


def test_decimal_engine_preserves_values_beyond_binary_precision():
    """Decimal literals and large integer results remain exact."""
    expressions = {
        "sum": formula("sum", "0.1", "0.2"),
        "large": formula("add", "9007199254740992", "1"),
        "literal": formula("sum", "1.234567890123456789"),
    }
    calculated = evaluate_graph({}, expressions)
    assert calculated["sum"]["value"] == "0.3"
    assert calculated["large"]["value"] == "9007199254740993"
    assert calculated["literal"]["value"] == "1.234567890123456789"
    assert expressions["literal"].model_dump()["operands"] == [
        {"literal": "1.234567890123456789"}
    ]


def test_dependencies_recalculate_in_order_and_propagate_errors():
    """Derived cells use upstream results rather than cached prior amounts."""
    expressions = {
        "total": formula("multiply", "@subtotal", "2"),
        "subtotal": formula("add", "@input", "0.2"),
    }
    assert evaluate_graph({"input": "0.1"}, expressions)["total"]["value"] == "0.6"
    assert evaluate_graph({"input": "0.3"}, expressions)["total"]["value"] == "1"
    expressions["subtotal"] = formula("divide", "@input", "0")
    calculated = evaluate_graph({"input": "1"}, expressions)
    assert calculated["subtotal"]["status"] == "failed"
    assert calculated["total"] == {
        "status": "failed",
        "value": None,
        "error": "dependency_failed",
    }


def test_cycles_and_unknown_dependencies_reject():
    """Dependency validation runs before returning any calculated values."""
    with pytest.raises(ValueError, match="cycle"):
        evaluate_graph({}, {"a": formula("sum", "@b"), "b": formula("sum", "@a")})
    with pytest.raises(ValueError, match="Unknown"):
        evaluate_graph({}, {"a": formula("sum", "@missing")})


def test_inexact_division_requires_explicit_rounding():
    """A repeating fraction cannot silently inherit an ambient rounding mode."""
    expression = formula("divide", "1", "3")
    assert evaluate_graph({}, {"a": expression})["a"]["status"] == "failed"
    rounded = formula(
        "divide", "1", "3", rounding={"places": 2, "mode": "ROUND_HALF_EVEN"}
    )
    assert evaluate_graph({}, {"a": rounded})["a"]["value"] == "0.33"


def test_downstream_formulas_consume_rounded_cell_values():
    """Each computed cell rounds before its value enters a dependent formula."""
    policy = {"places": 2, "mode": "ROUND_HALF_EVEN"}
    expressions = {
        "third": formula("divide", "1", "3", rounding=policy),
        "total": formula("multiply", "@third", "3", rounding=policy),
    }
    calculated = evaluate_graph({}, expressions)
    assert calculated["third"] == {"status": "current", "value": "0.33", "error": None}
    assert calculated["total"] == {"status": "current", "value": "0.99", "error": None}


@pytest.mark.parametrize(
    "mode,expected",
    [("ROUND_HALF_EVEN", "0.12"), ("ROUND_HALF_UP", "0.13"), ("ROUND_DOWN", "0.12")],
)
def test_explicit_rounding_uses_exact_ties(mode, expected):
    """One rational rounding step retains the requested tie convention."""
    expression = formula(
        "rounddown" if mode == "ROUND_DOWN" else "round",
        "0.125",
        rounding={"places": 2, "mode": mode},
    )
    assert evaluate_graph({}, {"a": expression})["a"]["value"] == expected


def test_invalid_literals_and_unsupported_syntax_reject():
    """No binary float, Excel expression or executable language enters the engine."""
    for value in (0.1, "=A1+1", "NaN", "1e10000", "1,000"):
        with pytest.raises(ValueError):
            Formula.model_validate(
                {"operation": "sum", "operands": [{"literal": value}]}
            )


@pytest.mark.parametrize(
    "operation,mode,expected",
    [
        ("roundup", "ROUND_UP", "-1.24"),
        ("rounddown", "ROUND_DOWN", "-1.23"),
        ("round", "ROUND_HALF_EVEN", "-1.24"),
    ],
)
def test_negative_explicit_rounding_functions(operation, mode, expected):
    """Up and down refer to magnitude, not the number line."""
    expression = formula(operation, "-1.235", rounding={"places": 2, "mode": mode})
    assert evaluate_graph({}, {"a": expression})["a"]["value"] == expected


def test_four_place_scale_and_rounded_precision_failure():
    """Financial scales are explicit and overflow cannot report a current value."""
    expression = formula(
        "divide", "1", "3", rounding={"places": 4, "mode": "ROUND_HALF_UP"}
    )
    assert evaluate_graph({}, {"a": expression})["a"]["value"] == "0.3333"
    oversized = formula(
        "multiply",
        "9" * 64,
        "9" * 64,
        rounding={"places": 2, "mode": "ROUND_HALF_EVEN"},
    )
    assert evaluate_graph({}, {"a": oversized})["a"]["status"] == "failed"
