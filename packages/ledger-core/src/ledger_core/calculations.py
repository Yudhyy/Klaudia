"""Calculation contracts separate model intent from observed source revisions."""

from typing import Annotated, Any
from decimal import Decimal
import json

from pydantic import Field, StrictInt

from ledger_core.query import AggregationSpec


class CalculationRequest(AggregationSpec):
    """Model-selected table and metrics without authority or physical coordinates."""

    table_id: Annotated[str, Field(min_length=1, max_length=256)]


class CheckedCalculation(CalculationRequest):
    """Calculation bound to revisions from an inspected resource reference."""

    expected_sheet_revision: Annotated[StrictInt, Field(ge=0)]
    expected_catalogue_revision: Annotated[StrictInt, Field(gt=0)]


def decode_calculation_grid(encoded: str) -> list[list[Any]]:
    """Preserve PostgreSQL fractional values as decimals for exact aggregation.

    Args:
        encoded: JSONB grid returned by PostgreSQL.

    Returns:
        Rows with exact decimal operands, without float conversion.

    Raises:
        ValueError: The stored grid is not valid JSON.
    """
    return json.loads(encoded, parse_float=Decimal)
