"""Calculation intent cannot control location or silently round stored decimals."""

import pytest
from pydantic import ValidationError

from ledger_core.calculations import CalculationRequest, decode_calculation_grid


@pytest.mark.parametrize(
    "field", ["table_range", "spreadsheet_id", "user_id", "expected_sheet_revision"]
)
def test_model_calculation_rejects_authority_and_location_fields(field):
    """Only table identity and aggregation choices are model-controlled."""
    with pytest.raises(ValidationError):
        CalculationRequest.model_validate(
            {
                "table_id": "tbl_one",
                "metrics": [{"column": "Amount", "operation": "sum"}],
                field: 1,
            }
        )


def test_grid_decoder_preserves_fractional_precision_for_sums():
    """Stored fractions retain their exact digits through labelled arithmetic."""
    from ledger_core.query import AggregateQuery, aggregate_grid

    values = decode_calculation_grid('[["Amount"], [0.123456789012345678901], [0.1]]')
    evidence = aggregate_grid(
        values,
        AggregateQuery(
            table_range="A1:A3", metrics=[{"column": "Amount", "operation": "sum"}]
        ),
    )
    assert evidence["groups"][0]["metrics"][0]["value"] == "0.223456789012345678901"


def test_unused_columns_and_excluded_rows_do_not_block_calculation():
    """Precision limits apply to selected operands, not unrelated values."""
    from ledger_core.query import AggregateQuery, aggregate_grid

    values = decode_calculation_grid(
        '[["Amount", "Unused", "Status"], [10, 0.123456789012345678901, "keep"], [0.123456789012345678901234567890123456789012345678901234567890123456789, 0, "skip"]]'
    )
    evidence = aggregate_grid(
        values,
        AggregateQuery(
            table_range="A1:C3",
            metrics=[{"column": "Amount", "operation": "sum"}],
            filters=[{"column": "Status", "value": "keep"}],
        ),
    )
    assert evidence["groups"][0]["metrics"][0]["value"] == "10"


def test_count_does_not_require_rounding_fractional_operands():
    """Nonblank counts do not coerce or sum their numeric values."""
    from ledger_core.query import AggregateQuery, aggregate_grid

    values = decode_calculation_grid('[["Amount"], [0.123456789012345678901]]')
    evidence = aggregate_grid(
        values,
        AggregateQuery(
            table_range="A1:A2", metrics=[{"column": "Amount", "operation": "count"}]
        ),
    )
    assert evidence["groups"][0]["metrics"][0]["value"] == "1"


def test_fractional_group_labels_never_silently_round():
    """The legacy JSON key format rejects labels it cannot encode exactly."""
    from ledger_core.query import AggregateQuery, aggregate_grid

    query = AggregateQuery(
        table_range="A1:A2",
        metrics=[{"column": "Amount", "operation": "count"}],
        group_by=["Amount"],
    )
    with pytest.raises(ValueError, match="Grouping value"):
        aggregate_grid(
            decode_calculation_grid('[["Amount"], [0.123456789012345678901]]'), query
        )
    assert aggregate_grid(decode_calculation_grid('[["Amount"], [0.1]]'), query)[
        "groups"
    ][0]["key"] == {"Amount": 0.1}
