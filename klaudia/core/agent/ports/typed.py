"""Agent typed-edit intent; ledger validates values, formulas and financial scale."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

DecimalText = Annotated[
    str,
    Field(
        strict=True, min_length=1, max_length=128, pattern=r"^[+-]?[0-9]+(?:\.[0-9]+)?$"
    ),
]
CellId = Annotated[str, Field(strict=True, min_length=1, max_length=80)]


class Contract(BaseModel):
    """Forbid undeclared fields and mutation of validated contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LiteralOperand(Contract):
    """A decimal literal retained verbatim without binary conversion."""

    literal: DecimalText


class CellOperand(Contract):
    """An explicit dependency on a stable managed cell identity."""

    cell_id: CellId


class Rounding(Contract):
    """Explicit financial scale and a single final rounding mode."""

    places: Literal[2, 4]
    mode: Literal["ROUND_HALF_EVEN", "ROUND_HALF_UP", "ROUND_UP", "ROUND_DOWN"]


class Formula(Contract):
    """One native operation; complex formulas compose through cell dependencies."""

    operation: Literal[
        "add", "subtract", "multiply", "divide", "sum", "round", "roundup", "rounddown"
    ]
    operands: Annotated[
        list[LiteralOperand | CellOperand], Field(min_length=1, max_length=64)
    ]
    rounding: Rounding | None = None


class TypedValue(Contract):
    """Keep semantic type, raw value, unit and display metadata separate."""

    kind: Literal["decimal", "text", "boolean", "date"]
    value: Annotated[str, Field(strict=True, max_length=512)] | StrictBool
    unit: Annotated[str, Field(max_length=64)] | None = None
    display_format: Annotated[str, Field(max_length=128)] | None = None


class CellPosition(Contract):
    """A stable sheet identity and bounded one-based cell coordinates."""

    sheet_id: Annotated[int, Field(strict=True, gt=0)]
    row: Annotated[int, Field(strict=True, ge=1, le=1000)]
    column: Annotated[int, Field(strict=True, ge=1, le=256)]


class InputEdit(CellPosition):
    """Declare or edit an input without replacing a computed cell."""

    action: Literal["set_input"]
    input: TypedValue


class FormulaEdit(CellPosition):
    """Create or replace a native computed cell with explicit rounding."""

    action: Literal["set_formula"]
    formula: Formula
    unit: Annotated[str, Field(max_length=64)] | None = None
    display_format: Annotated[str, Field(max_length=128)] | None = None


class WorkbookInspection(Contract):
    """Select an owned workbook for typed-cell and revision evidence."""

    workbook_id: Annotated[str, Field(min_length=1, max_length=256)]


class TypedEditProposal(WorkbookInspection):
    """An exact batch with an observed whole-workbook snapshot fingerprint."""

    action: Literal["edit_typed_cells"] = "edit_typed_cells"
    snapshot: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    edits: Annotated[
        list[Annotated[InputEdit | FormulaEdit, Field(discriminator="action")]],
        Field(min_length=1, max_length=32),
    ]
