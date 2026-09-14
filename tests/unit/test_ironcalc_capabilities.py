"""Pin observed engine capabilities before selecting a financial formula adapter."""

from decimal import Decimal
from importlib.metadata import version
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

ironcalc = pytest.importorskip(
    "ironcalc", reason="Run the isolated IronCalc capability suite with ironcalc==0.8.2"
)
CELL_NAMESPACE = {"sheet": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@pytest.fixture
def formula_model():
    """Create a fresh model under the exact version being evaluated.

    Returns:
        Empty IronCalc workbook with fixed locale and timezone.
    """
    assert version("ironcalc") == "0.8.2"
    return ironcalc.create("capability-probe", "en", "UTC", "en")


def cached_values(model, destination):
    """Read the engine's exported numeric cache without trusting display text.

    Args:
        model: Evaluated synthetic workbook.
        destination: Temporary XLSX destination managed by pytest.

    Returns:
        First-sheet cell addresses mapped to cached value text.
    """
    model.save_to_xlsx(str(destination))
    with ZipFile(destination) as archive:
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return {
        cell.attrib["r"]: cell.find("sheet:v", CELL_NAMESPACE).text
        for cell in root.findall(".//sheet:c", CELL_NAMESPACE)
    }


def test_decimal_display_hides_binary_result(formula_model, tmp_path):
    """A displayed decimal sum is not an exact financial value."""
    formula_model.set_user_input(0, 1, 1, "=0.1+0.2")
    formula_model.evaluate()
    assert formula_model.get_formatted_cell_value(0, 1, 1) == "0.3"
    cached = cached_values(formula_model, tmp_path / "fraction.xlsx")
    assert cached["A1"] == "0.30000000000000004"
    assert Decimal(cached["A1"]) != Decimal("0.1") + Decimal("0.2")


def test_formula_literal_roundtrip_and_large_integer_precision(formula_model, tmp_path):
    """Numeric formula text and calculated values exceed the exactness gate."""
    expression = "=1.234567890123456789"
    formula_model.set_user_input(0, 1, 1, expression)
    formula_model.set_user_input(0, 2, 1, "=9007199254740992+1")
    formula_model.evaluate()
    assert formula_model.get_cell_content(0, 1, 1) != expression
    cached = cached_values(formula_model, tmp_path / "precision.xlsx")
    assert Decimal(cached["A1"]) != Decimal(expression[1:])
    assert Decimal(cached["A2"]) != Decimal(9007199254740993)


def test_formula_errors_are_visible(formula_model):
    """Division errors, cycles and unknown functions do not return valid totals."""
    for row, expression in enumerate(("=1/0", "=A2+1", "=UNSUPPORTED_FUNCTION(1)"), 1):
        formula_model.set_user_input(0, row, 1, expression)
    formula_model.evaluate()
    assert formula_model.get_formatted_cell_value(0, 1, 1) == "#DIV/0!"
    assert formula_model.get_formatted_cell_value(0, 2, 1) == "#CIRC!"
    assert formula_model.get_formatted_cell_value(0, 3, 1) == "#NAME?"


def test_cross_sheet_sum_tracks_input_rename_and_insertion(formula_model):
    """Supported references recalculate and rewrite after structural edits."""
    formula_model.add_sheet("Inputs")
    formula_model.set_user_input(1, 1, 1, "20")
    formula_model.set_user_input(1, 2, 1, "22")
    formula_model.set_user_input(0, 1, 1, "=SUM(Inputs!A1:A2)")
    formula_model.evaluate()
    assert formula_model.get_formatted_cell_value(0, 1, 1) == "42"
    formula_model.set_user_input(1, 1, 1, "21")
    formula_model.evaluate()
    assert formula_model.get_formatted_cell_value(0, 1, 1) == "43"
    formula_model.rename_sheet(1, "Budget")
    formula_model.insert_rows(1, 1, 1)
    formula_model.evaluate()
    assert formula_model.get_cell_content(0, 1, 1) == "=SUM(Budget!A2:A3)"
    assert formula_model.get_formatted_cell_value(0, 1, 1) == "43"
    formula_model.delete_sheet(1)
    formula_model.evaluate()
    assert formula_model.get_formatted_cell_value(0, 1, 1) == "#REF!"


def test_binding_has_no_public_dependency_or_raw_value_reader(formula_model):
    """The adapter cannot promise APIs absent from the pinned Python binding."""
    names = {name for name in dir(formula_model) if not name.startswith("_")}
    assert "get_cell_content" in names
    assert "get_formatted_cell_value" in names
    assert "get_cell_value" not in names
    assert not any("depend" in name or "parse" in name for name in names)
