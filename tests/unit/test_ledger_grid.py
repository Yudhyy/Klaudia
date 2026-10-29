"""Unit tests for the mcp-ledger A1-notation grid engine."""

from ledger_core.grid import (
    clear_range,
    col_to_index,
    index_to_col,
    insert_cols,
    insert_rows,
    parse_range,
    slice_range,
    write_range,
)


def test_col_letter_conversions():
    assert col_to_index("A") == 0
    assert col_to_index("Z") == 25
    assert col_to_index("AA") == 26
    assert col_to_index("AZ") == 51
    assert index_to_col(0) == "A"
    assert index_to_col(25) == "Z"
    assert index_to_col(26) == "AA"
    assert index_to_col(51) == "AZ"


def test_parse_full_range():
    assert parse_range("A1:C10") == (0, 0, 9, 2)


def test_parse_single_cell():
    assert parse_range("B2") == (1, 1, 1, 1)


def test_parse_column_only_range():
    # "A:Z" means all rows, columns A through Z
    assert parse_range("A:Z") == (0, 0, None, 25)


def test_parse_row_only_range():
    # "1:3" means rows 1-3, all columns
    assert parse_range("1:3") == (0, None, 2, None)


def test_parse_none_means_whole_sheet():
    assert parse_range(None) == (0, 0, None, None)


def test_slice_range_trims_like_sheets():
    grid = [["a", "b", ""], ["c", "", ""], ["", "", ""]]
    # Whole sheet: trailing empty rows dropped, rows trimmed to last value
    assert slice_range(grid, None) == [["a", "b"], ["c"]]


def test_slice_range_explicit_window():
    grid = [["a", "b", "c"], ["d", "e", "f"], ["g", "h", "i"]]
    assert slice_range(grid, "B2:C3") == [["e", "f"], ["h", "i"]]


def test_slice_range_beyond_data_returns_empty():
    grid = [["a"]]
    assert slice_range(grid, "B2:C3") == []


def test_write_range_pads_grid():
    """Rows the write touches are padded to reach the block; untouched rows
    stay ragged (reads trim trailing empties, so both are equivalent)."""
    grid: list[list] = []
    out = write_range(grid, "B2", [["x", "y"], ["z", "w"]])
    assert out == [[], ["", "x", "y"], ["", "z", "w"]]


def test_write_range_overwrites_in_place_values():
    grid = [["a", "b"], ["c", "d"]]
    out = write_range(grid, "A1", [["X"]])
    assert out == [["X", "b"], ["c", "d"]]
    assert grid == [["a", "b"], ["c", "d"]]  # input untouched (immutability)


def test_append_semantics_via_write_after_last_row():
    grid = [["h1", "h2"], ["v1", "v2"]]
    out = write_range(grid, "A3", [["v3", "v4"]])
    assert out == [["h1", "h2"], ["v1", "v2"], ["v3", "v4"]]


def test_clear_range_keeps_dimensions():
    grid = [["a", "b"], ["c", "d"]]
    out = clear_range(grid, "A1:A2")
    assert out == [["", "b"], ["", "d"]]


def test_insert_rows_shifts_down():
    grid = [["a"], ["b"]]
    out = insert_rows(grid, start=1, count=2)
    assert out == [["a"], [], [], ["b"]]


def test_insert_cols_shifts_right():
    grid = [["a", "b"], ["c", "d"]]
    out = insert_cols(grid, start=1, count=1)
    assert out == [["a", "", "b"], ["c", "", "d"]]
