"""A1-notation grid engine for the ledger.

Pure functions over a 2D list-of-lists grid (row major, '' = empty cell).
All functions return new grids; inputs are never mutated. Bounds follow
Google Sheets semantics: reads trim trailing empties, writes pad the grid.
"""

from __future__ import annotations

import re
from typing import Any, Optional

_CELL_RE = re.compile(r"^([A-Za-z]+)(\d+)$")
_COL_RE = re.compile(r"^([A-Za-z]+)$")
_ROW_RE = re.compile(r"^(\d+)$")

Range = tuple[int, Optional[int], Optional[int], Optional[int]]


def col_to_index(letters: str) -> int:
    """Convert a column letter ('A', 'AA') to a 0-based index."""
    index = 0
    for ch in letters.upper():
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def index_to_col(index: int) -> str:
    """Convert a 0-based column index to letters ('A', 'AA')."""
    letters = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _parse_corner(token: str) -> tuple[Optional[int], Optional[int]]:
    """Parse one range corner into (row, col); None = unbounded axis."""
    cell = _CELL_RE.match(token)
    if cell:
        return int(cell.group(2)) - 1, col_to_index(cell.group(1))
    col = _COL_RE.match(token)
    if col:
        return None, col_to_index(col.group(1))
    row = _ROW_RE.match(token)
    if row:
        return int(row.group(1)) - 1, None
    raise ValueError(f"Invalid A1 token: {token!r}")


def parse_range(notation: Optional[str]) -> tuple:
    """Parse A1 notation into (row0, col0, row1, col1), 0-based inclusive.

    None components mean unbounded on that axis. A None notation means the
    whole sheet: (0, 0, None, None).

    Raises:
        ValueError: On malformed notation.
    """
    if notation is None or notation == "":
        return (0, 0, None, None)
    parts = notation.split(":")
    if len(parts) == 1:
        row, col = _parse_corner(parts[0])
        if row is None or col is None:
            raise ValueError(f"Single-token range must be a cell: {notation!r}")
        return (row, col, row, col)
    if len(parts) != 2:
        raise ValueError(f"Invalid A1 range: {notation!r}")
    row0, col0 = _parse_corner(parts[0])
    row1, col1 = _parse_corner(parts[1])
    return (row0 if row0 is not None else 0, col0, row1, col1)


def _trim_row(row: list[Any]) -> list[Any]:
    end = len(row)
    while end > 0 and (row[end - 1] is None or row[end - 1] == ""):
        end -= 1
    return row[:end]


def validate_bounded_range(notation: str, cell_limit: int) -> None:
    """Require explicit cell corners and a finite rectangle within a cell budget.

    Args:
        notation: A cell or rectangle in A1 notation.
        cell_limit: Maximum number of cells in the rectangle.

    Raises:
        ValueError: The range is unbounded, reversed, invalid or too large.
    """
    if not all(_CELL_RE.fullmatch(corner) for corner in notation.split(":")):
        raise ValueError("A finite rectangle with explicit A1 cell corners is required")
    first_row, first_column, last_row, last_column = parse_range(notation)
    if min(first_row, first_column, last_row, last_column) < 0:
        raise ValueError("Cell coordinates must be positive")
    if last_row < first_row or last_column < first_column:
        raise ValueError("Range must not be reversed")
    if (last_row - first_row + 1) * (last_column - first_column + 1) > cell_limit:
        raise ValueError(f"Range exceeds the {cell_limit} cell budget")


def slice_range(grid: list[list[Any]], notation: Optional[str]) -> list[list[Any]]:
    """Read a range from the grid, Sheets-style: trailing empty cells and
    rows are trimmed from the result."""
    row0, col0, row1, col1 = parse_range(notation)
    col0 = col0 if col0 is not None else 0
    last_row = row1 if row1 is not None else len(grid) - 1
    last_row = min(last_row, len(grid) - 1)

    out: list[list[Any]] = []
    for r in range(row0, last_row + 1):
        row = grid[r] if r < len(grid) else []
        upper = col1 + 1 if col1 is not None else len(row)
        out.append(_trim_row(row[col0:upper]))
    while out and not out[-1]:
        out.pop()
    return out


def _pad_copy(
    grid: list[list[Any]], min_rows: int, min_cols_at: dict[int, int]
) -> list[list[Any]]:
    """Deep-copy the grid, growing to min_rows and per-row minimum widths."""
    rows = max(len(grid), min_rows)
    out: list[list[Any]] = []
    for r in range(rows):
        row = list(grid[r]) if r < len(grid) else []
        need = min_cols_at.get(r, 0)
        if len(row) < need:
            row.extend([""] * (need - len(row)))
        out.append(row)
    return out


def write_range(
    grid: list[list[Any]], start_cell: str, data: list[list[Any]]
) -> list[list[Any]]:
    """Write a 2D block with its top-left corner at start_cell.

    Accepts a single cell ('B2') or a range ('B2:D4' — only the top-left
    corner is used, matching Sheets update semantics with a values block).
    """
    row0, col0, _r1, _c1 = parse_range(start_cell.split(":")[0])
    widths = {row0 + i: col0 + len(block_row) for i, block_row in enumerate(data)}
    out = _pad_copy(grid, row0 + len(data), widths)
    for i, block_row in enumerate(data):
        target = out[row0 + i]
        for j, value in enumerate(block_row):
            target[col0 + j] = value
    return out


def clear_range(grid: list[list[Any]], notation: str) -> list[list[Any]]:
    """Blank out values in a range; grid dimensions are preserved."""
    row0, col0, row1, col1 = parse_range(notation)
    col0 = col0 if col0 is not None else 0
    out = [list(row) for row in grid]
    last_row = min(row1 if row1 is not None else len(out) - 1, len(out) - 1)
    for r in range(row0, last_row + 1):
        row = out[r]
        upper = min(col1 + 1 if col1 is not None else len(row), len(row))
        for c in range(col0, upper):
            row[c] = ""
    return out


def insert_rows(grid: list[list[Any]], start: int, count: int) -> list[list[Any]]:
    """Insert empty rows at 0-based index start."""
    out = [list(row) for row in grid]
    for _ in range(count):
        out.insert(start, [])
    return out


def insert_cols(grid: list[list[Any]], start: int, count: int) -> list[list[Any]]:
    """Insert empty columns at 0-based index start in every row."""
    out = []
    for row in grid:
        new_row = list(row)
        if len(new_row) >= start:
            for _ in range(count):
                new_row.insert(start, "")
        out.append(new_row)
    return out


def last_data_row(grid: list[list[Any]]) -> int:
    """Return the count of leading rows up to and including the last row
    with any value (0 for an empty grid). Append lands at this index."""
    for r in range(len(grid) - 1, -1, -1):
        if _trim_row(grid[r]):
            return r + 1
    return 0
