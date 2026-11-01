"""Agent-selected metrics and observed revisions for exact backend reads."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    model_validator,
)

from agent_core.agent.ports.append import CellValue

ColumnName = Annotated[str, Field(min_length=1, max_length=256)]


class Metric(BaseModel):
    """An aggregate tied to an exact source column name."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    column: ColumnName
    operation: Literal["sum", "count"]


class EqualityFilter(BaseModel):
    """An exact-value predicate; display text is not parsed as money."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    column: ColumnName
    value: CellValue


class AggregationSpec(BaseModel):
    """Metrics and grouping policies independent of a physical table location."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    metrics: Annotated[list[Metric], Field(min_length=1, max_length=8)]
    filters: Annotated[list[EqualityFilter], Field(max_length=8)] = Field(
        default_factory=list
    )
    group_by: Annotated[list[ColumnName], Field(max_length=3)] = Field(
        default_factory=list
    )
    unit_column: ColumnName | None = None
    numeric_text: Literal["reject", "decimal"] = "reject"
    max_groups: Annotated[int, Field(strict=True, ge=1, le=100)] = 20

    @model_validator(mode="after")
    def unique_targets(self) -> "AggregationSpec":
        """Reject duplicate metrics or group keys.

        Returns:
            The validated query.

        Raises:
            ValueError: A metric or grouping column appears twice.
        """
        if len(set(self.group_by)) != len(self.group_by):
            raise ValueError("Grouping columns must be unique")
        targets = [(metric.column, metric.operation) for metric in self.metrics]
        if len(set(targets)) != len(targets):
            raise ValueError("Metrics must be unique")
        return self


class CalculationRequest(AggregationSpec):
    """Model-selected table and metrics without authority or physical coordinates."""

    table_id: Annotated[str, Field(min_length=1, max_length=256)]


class CheckedCalculation(CalculationRequest):
    """Calculation bound to revisions from an inspected resource reference."""

    expected_sheet_revision: Annotated[StrictInt, Field(ge=0)]
    expected_catalogue_revision: Annotated[StrictInt, Field(gt=0)]
