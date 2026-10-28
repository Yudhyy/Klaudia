"""Bounded table aggregation with exact decimal sums and labelled evidence."""

import math
import re
from dataclasses import dataclass, field
from decimal import Context, Decimal, DecimalException, Inexact, localcontext
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ledger import grid
from ledger.append_contracts import CellValue

MAX_QUERY_CELLS = 1_000_000
DECIMAL_PRECISION = 64
_DECIMAL_TEXT = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?\Z")
ColumnName = Annotated[str, Field(min_length=1, max_length=256)]
QueryValue = CellValue | Decimal
ScalarIdentity = tuple[str, QueryValue]
GroupIdentity = tuple[ScalarIdentity, ...]


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


class AggregateQuery(AggregationSpec):
    """A selected table rectangle and its validated aggregation policies."""

    table_range: Annotated[str, Field(min_length=1, max_length=64)]


def _number(value: Any, numeric_text: Literal["reject", "decimal"]) -> Decimal:
    """Parse raw numbers and plain decimal text without guessing display formats.

    Args:
        value: A cell from a legacy untyped grid.
        numeric_text: Whether text has been explicitly declared decimal input.

    Returns:
        A finite decimal value.

    Raises:
        ValueError: The cell is not a supported numeric value.
    """
    if type(value) in (int, float, Decimal):
        text = str(value)
    elif (
        numeric_text == "decimal"
        and type(value) is str
        and len(value) <= 128
        and _DECIMAL_TEXT.fullmatch(value)
    ):
        text = value
    else:
        raise ValueError(
            "Sum requires raw numbers; decimal text must be explicitly enabled"
        )
    number = Decimal(text)
    if not number.is_finite():
        raise ValueError("Sum requires finite numbers")
    return number


def _cell(row: list[Any], index: int) -> QueryValue:
    """Read a scalar cell, treating missing trailing cells as blank.

    Args:
        row: A row from a legacy untyped grid.
        index: Zero-based column index within the selected region.

    Returns:
        A scalar cell or None for a missing value.

    Raises:
        ValueError: The cell contains a container or a non-finite number.
    """
    value = row[index] if index < len(row) else None
    if value is not None and type(value) not in (str, int, float, bool, Decimal):
        raise ValueError("Query columns must contain scalar cells")
    if type(value) is float and not math.isfinite(value):
        raise ValueError("Query columns must contain finite values")
    if type(value) is Decimal and not value.is_finite():
        raise ValueError("Query columns must contain finite values")
    return value


def _identity(value: QueryValue) -> ScalarIdentity:
    """Use the same exact scalar identity for grouping and filtering.

    Args:
        value: Actual cell value.

    Returns:
        A hashable identity keeping text and booleans distinct from numbers.
    """
    if type(value) in (int, float, Decimal):
        return "number", Decimal(str(value))
    return type(value).__name__, value


@dataclass
class _MetricTotal:
    """Accumulate one labelled metric and its blank-value evidence."""

    metric: Metric
    numeric_text: Literal["reject", "decimal"]
    total: Decimal = Decimal(0)
    non_null_count: int = 0
    blank_count: int = 0

    def add(self, value: QueryValue) -> None:
        """Add a scalar value under the metric's sum or count contract.

        Args:
            value: A scalar cell from a matched record.

        Raises:
            ValueError: A sum operand is invalid.
            DecimalException: Summing would exceed the exact precision budget.
        """
        if value is None or value == "":
            self.blank_count += 1
            return
        self.non_null_count += 1
        if self.metric.operation == "sum":
            self.total += _number(value, self.numeric_text)

    def evidence(self) -> dict[str, Any]:
        """Return a value bound to its source column and aggregate operation.

        Returns:
            JSON-compatible metric evidence with decimal text values.
        """
        return {
            "column": self.metric.column,
            "operation": self.metric.operation,
            "value": format(self.total, "f")
            if self.metric.operation == "sum"
            else str(self.non_null_count),
            "non_null_count": self.non_null_count,
            "blank_count": self.blank_count,
        }


@dataclass
class _Group:
    """Metrics belonging to one exact grouping key and optional unit."""

    key: dict[str, QueryValue]
    totals: list[_MetricTotal]
    units: set[str] = field(default_factory=set)

    def add(
        self, row: list[Any], columns: dict[str, int], unit_column: str | None
    ) -> None:
        """Validate units and accumulate a matched record's metrics.

        Args:
            row: One matched table record.
            columns: Header-to-index mapping for this region.
            unit_column: Declared unit header, or None for unspecified units.

        Raises:
            ValueError: Units or metric operands are invalid.
            DecimalException: Summing would lose precision.
        """
        if unit_column:
            unit = _cell(row, columns[unit_column])
            if type(unit) is not str or not unit.strip():
                raise ValueError("Declared unit column must contain a non-empty unit")
            self.units.add(unit)
            if len(self.units) > 1:
                raise ValueError(
                    "Mixed units in aggregate group; group or filter by unit"
                )
        for total in self.totals:
            total.add(_cell(row, columns[total.metric.column]))

    def evidence(self) -> dict[str, Any]:
        """Return this group's key, unit and labelled metrics.

        Returns:
            JSON-compatible group evidence.
        """
        return {
            "key": {name: _json_group_value(value) for name, value in self.key.items()},
            "unit": next(iter(self.units), None),
            "metrics": [total.evidence() for total in self.totals],
        }


def _json_group_value(value: QueryValue) -> CellValue:
    """Keep numeric group labels exact within the existing JSON scalar contract.

    Args:
        value: Observed grouping value, possibly a PostgreSQL decimal.

    Returns:
        A JSON scalar preserving the grouping value.

    Raises:
        ValueError: A fractional label cannot round-trip through a JSON float.
    """
    if type(value) is not Decimal:
        return value
    if value == value.to_integral_value():
        return int(value)
    number = float(value)
    if Decimal(str(number)) != value:
        raise ValueError("Grouping value exceeds lossless JSON numeric precision")
    return number


def _table_columns(rows: list[list[Any]], request: AggregateQuery) -> dict[str, int]:
    """Resolve requested names to unique headers in the table's first row.

    Args:
        rows: Selected table rows, beginning with headers.
        request: Validated aggregate query.

    Returns:
        Exact header names mapped to zero-based region columns.

    Raises:
        ValueError: Headers are absent, duplicated or missing requested columns.
    """
    if not rows:
        raise ValueError("Selected table has no header row")
    headers = [name for name in rows[0] if type(name) is str and name]
    if len(set(headers)) != len(headers):
        raise ValueError("Table headers must be unique")
    columns = {
        name: index for index, name in enumerate(rows[0]) if type(name) is str and name
    }
    required = {metric.column for metric in request.metrics} | set(request.group_by)
    required.update(predicate.column for predicate in request.filters)
    if request.unit_column:
        required.add(request.unit_column)
    missing = required - columns.keys()
    if missing:
        raise ValueError(
            f"Columns absent from selected table: {', '.join(sorted(missing))}"
        )
    return columns


def _accumulate(
    rows: list[list[Any]], columns: dict[str, int], request: AggregateQuery
) -> tuple[list[_Group], int]:
    """Filter table records and accumulate bounded groups.

    Args:
        rows: Selected table including its header row.
        columns: Exact header-to-index mapping.
        request: Validated query and its output group budget.

    Returns:
        Groups and the number of matched records.

    Raises:
        ValueError: Operands or units are invalid, or the group budget is exceeded.
        DecimalException: Accumulation would lose precision.
    """
    groups: dict[GroupIdentity, _Group] = {}
    if not request.group_by:
        groups[()] = _Group(
            {},
            [_MetricTotal(metric, request.numeric_text) for metric in request.metrics],
        )
    matched_rows = 0
    for row in rows[1:]:
        if not row or all(value is None or value == "" for value in row):
            continue
        if not all(
            _identity(_cell(row, columns[predicate.column]))
            == _identity(predicate.value)
            for predicate in request.filters
        ):
            continue
        key = {column: _cell(row, columns[column]) for column in request.group_by}
        identity = tuple(_identity(value) for value in key.values())
        if identity not in groups:
            if len(groups) >= request.max_groups:
                raise ValueError("Query exceeds group limit; narrow the filters")
            groups[identity] = _Group(
                key,
                [
                    _MetricTotal(metric, request.numeric_text)
                    for metric in request.metrics
                ],
            )
        groups[identity].add(row, columns, request.unit_column)
        matched_rows += 1
    return list(groups.values()), matched_rows


def aggregate_grid(values: list[list[Any]], request: AggregateQuery) -> dict[str, Any]:
    """Compute labelled metrics over a finite table region without returning rows.

    Args:
        values: Grid from one ledger snapshot.
        request: Table range, exact column metrics, filters and grouping.

    Returns:
        Matched record count, grouped metrics and the applied unit/blank policies.

    Raises:
        ValueError: The range, schema, operands, units or precision are invalid.
    """
    grid.validate_bounded_range(request.table_range, MAX_QUERY_CELLS)
    rows = grid.slice_range(values, request.table_range)
    columns = _table_columns(rows, request)
    try:
        with localcontext(Context(prec=DECIMAL_PRECISION)) as context:
            context.traps[Inexact] = True
            groups, matched_rows = _accumulate(rows, columns, request)
    except DecimalException as error:
        raise ValueError(
            f"Sum exceeds exact decimal precision of {DECIMAL_PRECISION} digits"
        ) from error
    return {
        "matched_rows": matched_rows,
        "unit_column": request.unit_column,
        "blank_policy": "exclude_and_count",
        "groups": [group.evidence() for group in groups],
    }
