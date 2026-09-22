"""Live formula grading rejects plausible but incorrect persisted state."""

from copy import deepcopy

import pytest

from tests.e2e.formula_cases import (
    check_formula_cells,
    check_formula_grids,
    formula_prompt,
)


def formula_cells():
    """Return independent persisted input and formula evidence."""
    return [
        {
            "cell_id": "input",
            "sheet_id": 10,
            "row_number": 2,
            "column_number": 1,
            "kind": "decimal",
            "unit": "USD",
            "raw_value": "0.3",
            "expression": None,
            "dependencies": [],
        },
        {
            "cell_id": "total",
            "sheet_id": 11,
            "row_number": 2,
            "column_number": 1,
            "kind": "decimal",
            "unit": "USD",
            "raw_value": None,
            "expression": {
                "operation": "add",
                "operands": [{"cell_id": "input"}, {"literal": "0.2"}],
                "rounding": {"places": 2, "mode": "ROUND_HALF_EVEN"},
            },
            "dependencies": ["input"],
            "calculated_value": "0.50",
            "calculation_status": "current",
            "calculation_error": None,
        },
    ]


def test_live_formula_state_accepts_exact_requested_graph():
    """The expected graph passes independently of generated cell identities."""
    check_formula_cells(formula_cells(), (10, 11), "edited")


@pytest.mark.parametrize("operation", ["add", "sum"])
@pytest.mark.parametrize("reverse", [False, True])
def test_live_addition_accepts_equivalent_binary_expression(operation, reverse):
    """A request to add two values permits either native addition form.

    Args:
        operation: Native binary addition or two-operand sum.
        reverse: Whether commutative operands appear in reverse order.
    """
    cells = formula_cells()
    cells[1]["expression"]["operation"] = operation
    if reverse:
        cells[1]["expression"]["operands"].reverse()
    check_formula_cells(cells, (10, 11), "edited")


@pytest.mark.parametrize(
    "field,value",
    [
        ("dependencies", []),
        ("calculated_value", "0.5"),
        ("sheet_id", 12),
        ("row_number", 3),
        ("calculation_status", "failed"),
        ("unit", "EUR"),
    ],
)
def test_live_formula_state_rejects_wrong_graph(field, value):
    """Exact values alone cannot hide wrong placement or dependencies."""
    cells = deepcopy(formula_cells())
    cells[1][field] = value
    with pytest.raises(AssertionError):
        check_formula_cells(cells, (10, 11), "edited")


def test_live_formula_state_rejects_extra_managed_cell():
    """Unexpected edits fail even when requested cells are correct."""
    cells = formula_cells()
    cells.append({**cells[0], "cell_id": "extra", "row_number": 3})
    with pytest.raises(AssertionError):
        check_formula_cells(cells, (10, 11), "edited")


def test_live_formula_state_rejects_missing_input_unit():
    """Requested monetary metadata must survive on inputs as well as formulas."""
    cells = formula_cells()
    cells[0]["unit"] = None
    with pytest.raises(AssertionError):
        check_formula_cells(cells, (10, 11), "edited")


@pytest.mark.parametrize("change", ["operation", "identity"])
def test_input_edit_must_preserve_existing_formula(change):
    """Equivalent creation choices do not permit changing an existing formula.

    Args:
        change: Unrequested replacement of the formula definition or identity.
    """
    before = {"sheets": [], "cells": formula_cells()}
    after = deepcopy(before)
    if change == "operation":
        after["cells"][1]["expression"]["operation"] = "sum"
    else:
        after["cells"][1]["cell_id"] = "replacement"
    with pytest.raises(AssertionError):
        check_formula_grids(before, after, "edited")


def test_discovery_prompt_does_not_supply_internal_tool_names():
    """The model must select its own tools from a business request."""
    prompt = formula_prompt("inputs")
    assert "Formula acceptance" in prompt
    assert "prepare_typed_edit" not in prompt
    assert "load_skill" not in prompt
