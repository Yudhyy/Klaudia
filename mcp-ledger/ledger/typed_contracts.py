"""Typed inputs and native formula edits bound to one workbook snapshot."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from ledger.formula_engine import Contract, DecimalText, Formula, decimal_value


class TypedValue(Contract):
    """Keep semantic type, raw value, unit and display metadata separate."""

    kind: Literal["decimal", "text", "boolean", "date"]
    value: Annotated[str, Field(strict=True, max_length=512)] | StrictBool
    unit: Annotated[str, Field(max_length=64)] | None = None
    display_format: Annotated[str, Field(max_length=128)] | None = None

    @model_validator(mode="after")
    def validate_value(self) -> "TypedValue":
        """Reject implicit type conversion and invalid decimal/date literals.

        Returns:
            Validated typed value.

        Raises:
            ValueError: The raw value does not match its declared type.
        """
        from pydantic import TypeAdapter

        if self.kind == "boolean":
            if type(self.value) is not bool:
                raise ValueError("Boolean inputs require a boolean value")
        elif type(self.value) is not str:
            raise ValueError("This input type requires exact text")
        elif self.kind == "decimal":
            TypeAdapter(DecimalText).validate_python(self.value)
            decimal_value(self.value)
        elif self.kind == "date" and (
            len(self.value) != 10
            or date.fromisoformat(self.value).isoformat() != self.value
        ):
            raise ValueError("Date inputs require YYYY-MM-DD")
        return self


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

    @model_validator(mode="after")
    def financial_scale(self) -> "FormulaEdit":
        """Require a declared financial scale for every persisted result.

        Returns:
            Validated formula edit.

        Raises:
            ValueError: Result rounding was omitted.
        """
        if self.formula.rounding is None:
            raise ValueError(
                "Persistent formulas require explicit 2- or 4-place rounding"
            )
        return self


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

    @model_validator(mode="after")
    def unique_positions(self) -> "TypedEditProposal":
        """Reject ambiguous repeated edits of the same position.

        Returns:
            Validated batch.

        Raises:
            ValueError: A position occurs twice.
        """
        positions = {(edit.sheet_id, edit.row, edit.column) for edit in self.edits}
        if len(positions) != len(self.edits):
            raise ValueError("Each cell position may be edited once per proposal")
        return self
