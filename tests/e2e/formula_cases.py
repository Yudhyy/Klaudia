"""Fixed user requests and independent persisted-state checks for live trials."""

from decimal import Decimal
import json
from typing import Any


def formula_prompt(stage: str) -> str:
    """Describe one explicit user action without prescribing agent tools.

    Args:
        stage: Named fixture action.

    Returns:
        Fixed request with exact placement, types and rounding policy.
    """
    actions = {
        "inputs": 'Set Inputs!A2 to decimal "0.1" with unit USD.',
        "created": 'Set Totals!A2 to a persistent formula adding Inputs!A2 and decimal literal "0.2", with unit USD. Round to two decimal places using half-even rounding.',
        "edited": 'Change Inputs!A2 to decimal "0.3" with unit USD. Keep the existing formula and report its updated total.',
        "failed": 'Set Totals!A2 to a persistent formula dividing Inputs!A2 by decimal literal "0", with unit USD and two decimal places using half-even rounding. Report the calculation status honestly.',
        "repaired": 'Replace the formula in Totals!A2 with Inputs!A2 divided by decimal literal "2", with unit USD and two decimal places using half-even rounding.',
        "literals": 'Set Inputs!A2 to decimal "+001.2300", B2 to text "00123", C2 to boolean true, D2 to date "2026-09-21", E2 to decimal "9007199254740993", and F2 to decimal "0.123456789012345678901234567890". Preserve each literal and type exactly.',
    }
    return (
        'In the workbook named "Formula acceptance", '
        + actions[stage]
        + " Change only the requested cells. Leave all other workbooks unchanged."
    )


def check_formula_cells(
    cells: list[dict[str, Any]], sheets: tuple[int, int], stage: str
) -> None:
    """Compare persisted cells with independently declared fixture outcomes.

    Args:
        cells: Typed inspection evidence.
        sheets: Input and total sheet identities assigned by the fixture.
        stage: Completed user action.

    Raises:
        AssertionError: Placement, literals, graph or calculation differs.
    """
    indexed = {
        (cell["sheet_id"], cell["row_number"], cell["column_number"]): cell
        for cell in cells
    }
    positions = {(sheets[0], 2, 1)}
    if stage != "inputs":
        positions.add((sheets[1], 2, 1))
    assert len(cells) == len(positions)
    assert set(indexed) == positions
    source = indexed[(sheets[0], 2, 1)]
    assert source["kind"] == "decimal"
    assert source["unit"] == "USD"
    assert source["raw_value"] == ("0.3" if stage == "edited" else "0.1")
    assert source["expression"] is None
    assert source["dependencies"] == []
    if stage == "inputs":
        return
    total = indexed[(sheets[1], 2, 1)]
    operation, literal, value = {
        "created": ("add", "0.2", "0.30"),
        "edited": ("add", "0.2", "0.50"),
        "failed": ("divide", "0", None),
        "repaired": ("divide", "2", "0.05"),
    }[stage]
    assert total["kind"] == "decimal"
    assert total["unit"] == "USD"
    assert total["dependencies"] == [source["cell_id"]]
    expression = total["expression"]
    operands = [{"cell_id": source["cell_id"]}, {"literal": literal}]
    if operation == "add":
        assert expression["operation"] in ("add", "sum")
        assert expression["operands"] in (operands, list(reversed(operands)))
    else:
        assert expression["operation"] == operation
        assert expression["operands"] == operands
    assert expression == {
        "operation": expression["operation"],
        "operands": expression["operands"],
        "rounding": {"places": 2, "mode": "ROUND_HALF_EVEN"},
    }
    assert total["calculated_value"] == value
    assert total["calculation_status"] == ("failed" if value is None else "current")
    assert total["calculation_error"] == (
        "invalid_or_inexact_arithmetic" if value is None else None
    )


def check_formula_grids(before: dict, after: dict, stage: str) -> None:
    """Check complete grids, including cells outside the requested edits.

    Args:
        before: Snapshot before the user turn.
        after: Snapshot after the committed operation.
        stage: Requested fixture action.

    Raises:
        AssertionError: Any sheet identity, title or grid differs from intent.
    """
    expected = {
        sheet["sheet_id"]: (
            sheet["title"],
            json.loads(sheet["grid"], parse_float=Decimal),
        )
        for sheet in before["sheets"]
    }
    for title, values in expected.values():
        if title == "Inputs" and stage in ("inputs", "edited", "literals"):
            if stage == "literals":
                values[1] = [
                    Decimal("1.2300"),
                    "00123",
                    True,
                    "2026-09-21",
                    9007199254740993,
                    Decimal("0.123456789012345678901234567890"),
                ]
            else:
                values[1][0] = Decimal("0.3" if stage == "edited" else "0.1")
        if title == "Totals" and stage in ("created", "edited", "failed", "repaired"):
            values[1][0] = {
                "created": Decimal("0.30"),
                "edited": Decimal("0.50"),
                "failed": None,
                "repaired": Decimal("0.05"),
            }[stage]
    actual = {
        sheet["sheet_id"]: (
            sheet["title"],
            json.loads(sheet["grid"], parse_float=Decimal),
        )
        for sheet in after["sheets"]
    }
    assert actual == expected
    if stage == "edited":
        original = {
            cell["cell_id"]: cell
            for cell in before["cells"]
            if cell["expression"] is not None
        }
        current = {
            cell["cell_id"]: cell
            for cell in after["cells"]
            if cell["expression"] is not None
        }
        assert set(original) == set(current)
        for identity, cell in original.items():
            assert current[identity]["expression"] == cell["expression"]


def formula_expectation(workbook_id, sheet_id, cell_id=None, value=None):
    """Describe independent exact outcomes for this fixture's single formula.

    Args:
        workbook_id: Expected operation destination.
        sheet_id: Expected first edited sheet.
        cell_id: Persisted formula identity, absent for input declaration.
        value: Expected exact result, or None for arithmetic failure.

    Returns:
        Complete calculation evidence expected by the shared acceptance grader.
    """
    status = (
        "not_required" if cell_id is None else "failed" if value is None else "current"
    )
    return {
        "workbook_id": workbook_id,
        "sheet_id": sheet_id,
        "calculation_status": status,
        "calculation": {
            "engine_version": "native-decimal-v1",
            "invalidated": int(cell_id is not None),
            "recalculated": int(cell_id is not None),
            "failed": int(status == "failed"),
            "results": []
            if cell_id is None
            else [
                {
                    "cell_id": cell_id,
                    "status": status,
                    "value": value,
                    "error": "invalid_or_inexact_arithmetic"
                    if status == "failed"
                    else None,
                }
            ],
        },
    }
