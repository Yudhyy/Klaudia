"""Typed bounded records and exact key matching for financial execution."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ledger.financial_contracts import Join, Lookup, Matching, Records
from ledger.query import _cell, _identity, _number

MAX_FINANCIAL_ROWS = 10_000


def iso_date(value: Any) -> date:
    """Parse only a full ISO calendar date, without timezone or locale guesses.

    Args:
        value: Raw cell value.

    Returns:
        Calendar date from YYYY-MM-DD text.

    Raises:
        ValueError: The cell is not a canonical ISO date.
    """
    if type(value) is not str or len(value) != 10:
        raise ValueError("Date cells require YYYY-MM-DD text")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Date cells require YYYY-MM-DD text")
    return parsed


def typed_value(value: Any) -> dict[str, Any]:
    """Encode exact numeric cells distinctly from strings.

    Args:
        value: Validated finite scalar.

    Returns:
        Explicit type and lossless JSON value.
    """
    if type(value) in (int, float, Decimal):
        return {"type": "number", "value": str(value)}
    return {"type": type(value).__name__, "value": value}


@dataclass(frozen=True)
class Table:
    """One finite table with physical row offsets retained for evidence."""

    columns: dict[str, int]
    records: list[tuple[int, list[Any]]]

    @classmethod
    def read(cls, rows: list[list[Any]]) -> "Table":
        """Read unique headers and bounded nonblank records.

        Args:
            rows: Registered region beginning with its header row.

        Returns:
            Column mapping and one-based region row positions.

        Raises:
            ValueError: Headers are invalid or the row budget is exceeded.
        """
        if not rows or len(rows) - 1 > MAX_FINANCIAL_ROWS:
            raise ValueError("Financial table requires headers and at most 10000 rows")
        headers = rows[0]
        if any(type(name) is not str or not name for name in headers):
            raise ValueError("Financial table headers must be nonblank text")
        if len(set(headers)) != len(headers):
            raise ValueError("Financial table headers must be unique")
        return cls(
            {name: index for index, name in enumerate(headers)},
            [
                (index, row)
                for index, row in enumerate(rows[1:], 2)
                if any(value is not None and value != "" for value in row)
            ],
        )

    def require(self, names: list[str]) -> None:
        """Validate referenced columns even when the table has no records.

        Args:
            names: Exact source column names.

        Raises:
            ValueError: Any requested column is absent.
        """
        missing = set(names) - self.columns.keys()
        if missing:
            raise ValueError(
                f"Columns absent from selected table: {', '.join(sorted(missing))}"
            )

    def cell(self, row: list[Any], name: str) -> Any:
        """Read a scalar from a prevalidated source column.

        Args:
            row: Source record.
            name: Previously checked column.

        Returns:
            Finite scalar or None for a missing trailing cell.

        Raises:
            ValueError: The cell is not a finite scalar.
        """
        return _cell(row, self.columns[name])

    def evidence(
        self, record: tuple[int, list[Any]], columns: list[str]
    ) -> dict[str, Any]:
        """Return projected cells with their source row position.

        Args:
            record: Source row position and cells.
            columns: Validated projection names.

        Returns:
            Typed values labelled by column and region row.
        """
        return {
            "row": record[0],
            "values": {
                name: typed_value(self.cell(record[1], name)) for name in columns
            },
        }


def page(records: list[dict[str, Any]], request: Records | Matching) -> dict[str, Any]:
    """Page completed evidence without hiding the full population size.

    Args:
        records: Full bounded result population.
        request: Requested page bounds.

    Returns:
        Result page with explicit continuation and match count.
    """
    end = request.offset + request.limit
    return {
        "matched_rows": len(records),
        "records": records[request.offset : end],
        "next_offset": end if end < len(records) else None,
    }


def select_records(table: Table, request: Records | Lookup) -> dict[str, Any]:
    """Filter records before deterministic sorting or unique lookup.

    Args:
        table: Bounded source table.
        request: Validated selection policies.

    Returns:
        A page or a single optional lookup record.

    Raises:
        ValueError: Columns, sort operands or lookup cardinality are invalid.
    """
    table.require(request.columns + [predicate.column for predicate in request.filters])
    selected = [
        record
        for record in table.records
        if all(
            _identity(table.cell(record[1], predicate.column))
            == _identity(predicate.value)
            for predicate in request.filters
        )
    ]
    if isinstance(request, Lookup):
        if len(selected) > 1:
            raise ValueError("Lookup found multiple records; refine the predicates")
        if not selected and request.missing == "reject":
            raise ValueError("Lookup found no record")
        return {
            "matched_rows": len(selected),
            "record": table.evidence(selected[0], request.columns)
            if selected
            else None,
        }
    table.require([order.column for order in request.sort])
    for order in reversed(request.sort):
        populated, blanks = [], []
        for record in selected:
            value = table.cell(record[1], order.column)
            if value is None or value == "":
                blanks.append(record)
                continue
            if order.kind == "number":
                value = _number(value, request.numeric_text)
            elif order.kind == "date":
                value = iso_date(value)
            elif type(value) is not str:
                raise ValueError("Text sorting requires text cells")
            populated.append((value, record))
        populated.sort(key=lambda item: item[0], reverse=order.direction == "desc")
        ordered = [record for _, record in populated]
        selected = blanks + ordered if order.nulls == "first" else ordered + blanks
    return page(
        [table.evidence(record, request.columns) for record in selected], request
    )


def keyed_records(
    table: Table, keys: list[str], nulls: str
) -> list[tuple[Any, tuple[int, list[Any]]]]:
    """Build type-aware composite keys while retaining null-key records.

    Args:
        table: Source records.
        keys: Exact column names.
        nulls: Reject null keys or keep them unmatched.

    Returns:
        Keys and source records; None denotes a never-matching key.

    Raises:
        ValueError: A key column is absent or a forbidden null key occurs.
    """
    table.require(keys)
    keyed = []
    for record in table.records:
        values = [table.cell(record[1], name) for name in keys]
        blank = any(value is None or value == "" for value in values)
        if blank and nulls == "reject":
            raise ValueError("Null matching key is forbidden")
        keyed.append(
            (None if blank else tuple(_identity(value) for value in values), record)
        )
    return keyed


def unique_index(
    keyed: list[tuple[Any, tuple[int, list[Any]]]],
) -> dict[Any, tuple[int, list[Any]]]:
    """Reject duplicate non-null identities before matching any output rows.

    Args:
        keyed: Type-aware source keys and records.

    Returns:
        Unique non-null record index.

    Raises:
        ValueError: A key appears more than once.
    """
    index = {}
    for key, record in keyed:
        if key is None:
            continue
        if key in index:
            raise ValueError(
                "Duplicate matching key; aggregate or resolve duplicates first"
            )
        index[key] = record
    return index


def join_records(tables: tuple[Table, Table], request: Join) -> dict[str, Any]:
    """Execute bounded lookup-style joins without multiplying source records.

    Args:
        tables: Left and right source snapshots.
        request: Join projections, cardinality and null policies.

    Returns:
        Paged pairs with explicit missing right records.

    Raises:
        ValueError: Columns, keys or declared cardinality are invalid.
    """
    left, right = tables
    left.require(request.columns)
    right.require(request.right_columns)
    left_keys = keyed_records(left, request.left_keys, request.null_keys)
    right_index = unique_index(
        keyed_records(right, request.right_keys, request.null_keys)
    )
    if request.cardinality == "one_to_one":
        unique_index(left_keys)
    joined = []
    for key, record in left_keys:
        match = right_index.get(key)
        if match is not None or request.join_kind == "left":
            joined.append(
                {
                    "left": left.evidence(record, request.columns),
                    "right": right.evidence(match, request.right_columns)
                    if match
                    else None,
                }
            )
    return page(joined, request)
