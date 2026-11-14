"""Input contracts for checked literal-value appends."""

import pytest
from pydantic import ValidationError

from ledger_postgres.operations import AppendRows


@pytest.mark.parametrize(
    "changes",
    [
        {"sheet_id": True},
        {"sheet_id": 0},
        {"expected_revision": -1},
        {"expected_revision": "1"},
        {"idempotency_key": " "},
        {"idempotency_key": "x" * 129},
        {"rows": []},
        {"rows": [[]]},
        {"rows": [[{"amount": 10}]]},
        {"rows": [[[10]]]},
        {"rows": [[float("nan")]]},
        {"rows": [[float("inf")]]},
        {"rows": [[1]] * 1001},
        {"rows": [[1] * 257]},
        {"spreadsheet_id": "foreign"},
    ],
)
def test_invalid_append_is_rejected(changes):
    """Reject invalid values, oversized requests and caller-supplied scope."""
    fields = dict(
        sheet_id=1, expected_revision=0, idempotency_key="request", rows=[[10]]
    )
    with pytest.raises(ValidationError):
        AppendRows(**(fields | changes))


def test_raw_cell_types_are_preserved():
    """Literal text, booleans and numeric values retain their original types."""
    rows = [["00123", 185000, 0.25, True, None]]
    request = AppendRows(
        sheet_id=1, expected_revision=0, idempotency_key="request", rows=rows
    )
    assert request.rows == rows
    assert [type(cell) for cell in request.rows[0]] == [
        str,
        int,
        float,
        bool,
        type(None),
    ]
