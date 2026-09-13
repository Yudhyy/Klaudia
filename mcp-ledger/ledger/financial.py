"""Exact financial execution over bounded registered-table snapshots."""

from decimal import Context, Decimal, DecimalException, Inexact, localcontext
from fractions import Fraction
from typing import Any

from ledger.financial_contracts import (
    Aging,
    Comparison,
    FinancialRequest,
    Join,
    Lookup,
    NumericPolicy,
    Reconcile,
    Records,
    Variance,
)
from ledger.financial_records import (
    Table,
    iso_date,
    join_records,
    keyed_records,
    page,
    select_records,
    typed_value,
    unique_index,
)
from ledger.query import DECIMAL_PRECISION, _number

__all__ = ["FinancialRequest", "execute_financial"]


def amount_value(value: Any, policies: NumericPolicy) -> Decimal | None:
    """Read an amount with explicit null and numeric-text policies.

    Args:
        value: Raw amount cell.
        policies: Declared arithmetic conventions.

    Returns:
        Exact amount or None for an explicitly excluded blank.

    Raises:
        ValueError: An operand is blank or invalid under the policy.
    """
    if value is None or value == "":
        if policies.null_amounts == "exclude":
            return None
        raise ValueError("Null amount is forbidden")
    amount = _number(value, policies.numeric_text)
    if (
        len(amount.as_tuple().digits) > DECIMAL_PRECISION
        or abs(amount.as_tuple().exponent) > 128
        or abs(amount.adjusted()) > 128
    ):
        raise ValueError("Amount exceeds exact 64-digit precision or exponent budget")
    return amount


def amounts(
    table: Table, target: tuple[str, str | None], policies: NumericPolicy
) -> dict[int, Decimal | None]:
    """Validate all source amounts and units, including unmatched records.

    Args:
        table: Bounded source snapshot.
        target: Amount and optional unit columns.
        policies: Explicit arithmetic and unit rules.

    Returns:
        Amounts indexed by source row position.

    Raises:
        ValueError: Columns, operands or declared units are invalid.
    """
    column, unit_column = target
    table.require([column] + ([unit_column] if unit_column else []))
    parsed = {}
    for row_number, row in table.records:
        if unit_column and table.cell(row, unit_column) != policies.unit:
            raise ValueError("Amount unit does not match the declared unit")
        parsed[row_number] = amount_value(table.cell(row, column), policies)
    return parsed


def percentage(
    difference: Decimal, baseline: Decimal, request: Variance
) -> tuple[str | None, str]:
    """Round an exact rational percentage once at the requested decimal place.

    Args:
        difference: Exact signed variance.
        baseline: Signed subtrahend selected by the variance direction.
        request: Explicit zero and rounding policies.

    Returns:
        Decimal percentage text and calculation status.

    Raises:
        ValueError: Zero baseline is forbidden or the output exceeds precision.
    """
    if baseline == 0:
        if request.zero_baseline == "reject":
            raise ValueError("Variance baseline is zero")
        return None, "zero_baseline"
    scaled = (
        Fraction(difference) / Fraction(baseline) * 100 * 10**request.percentage_places
    )
    magnitude = abs(scaled)
    whole, remainder = divmod(magnitude.numerator, magnitude.denominator)
    twice = remainder * 2
    if request.rounding == "ROUND_HALF_UP" and twice >= magnitude.denominator:
        whole += 1
    elif request.rounding == "ROUND_HALF_EVEN" and (
        twice > magnitude.denominator or (twice == magnitude.denominator and whole % 2)
    ):
        whole += 1
    if len(str(whole)) > DECIMAL_PRECISION:
        raise ValueError("Variance percentage exceeds exact precision budget")
    signed = -whole if scaled < 0 else whole
    return format(
        Decimal(signed).scaleb(-request.percentage_places),
        f".{request.percentage_places}f",
    ), "calculated"


def paired_records(
    tables: tuple[Table, Table], request: Comparison
) -> list[tuple[Any, Any]]:
    """Full-outer-match unique keys in left order, then unmatched right order.

    Args:
        tables: Left and right source tables.
        request: Composite key and null policies.

    Returns:
        Source record pairs, retaining missing and null-key records.

    Raises:
        ValueError: Key columns, nulls or uniqueness are invalid.
    """
    left = keyed_records(tables[0], request.left_keys, request.null_keys)
    right = keyed_records(tables[1], request.right_keys, request.null_keys)
    left_index = unique_index(left)
    right_index = unique_index(right)
    return [(record, right_index.get(key)) for key, record in left] + [
        (None, record) for key, record in right if key is None or key not in left_index
    ]


def comparison_record(
    pair: tuple[Any, Any],
    operands: tuple[dict[int, Decimal | None], dict[int, Decimal | None]],
    request: Reconcile | Variance,
) -> dict[str, Any]:
    """Calculate one labelled pair without treating absent records as zero.

    Args:
        pair: Left and right source records, either possibly absent.
        operands: Parsed amounts keyed by source row.
        request: Financial comparison policies.

    Returns:
        Row positions, operands, difference and operation-specific status.

    Raises:
        ValueError: Percentage calculation violates the declared policy.
        DecimalException: Subtraction exceeds exact precision.
    """
    left, right = pair
    left_amount = operands[0][left[0]] if left else None
    right_amount = operands[1][right[0]] if right else None
    evidence = {
        "left_row": left[0] if left else None,
        "right_row": right[0] if right else None,
        "left_amount": str(left_amount) if left_amount is not None else None,
        "right_amount": str(right_amount) if right_amount is not None else None,
        "difference": None,
    }
    if left is None or right is None:
        evidence["status"] = "right_only" if left is None else "left_only"
    elif left_amount is None or right_amount is None:
        evidence["status"] = "excluded_null_amount"
    else:
        difference = left_amount - right_amount
        if isinstance(request, Variance) and request.direction == "right_minus_left":
            difference = right_amount - left_amount
        evidence["difference"] = format(difference, "f")
        if isinstance(request, Reconcile):
            evidence["status"] = (
                "matched" if abs(difference) <= request.tolerance else "different"
            )
        else:
            evidence["status"] = "compared"
            baseline = (
                right_amount if request.direction == "left_minus_right" else left_amount
            )
            evidence["percentage"], evidence["percentage_status"] = percentage(
                difference, baseline, request
            )
    if isinstance(request, Variance) and "percentage" not in evidence:
        evidence.update(percentage=None, percentage_status="not_comparable")
    return evidence


def compare_tables(
    tables: tuple[Table, Table], request: Reconcile | Variance
) -> dict[str, Any]:
    """Compare every unique key and page evidence after full validation.

    Args:
        tables: Authorised source snapshots.
        request: Reconciliation or variance intent.

    Returns:
        Paged labelled pairs and status counts for the full population.

    Raises:
        ValueError: Keys, units, operands or policies are invalid.
        DecimalException: Arithmetic exceeds exact precision.
    """
    operands = (
        amounts(
            tables[0],
            (request.left_amount, request.policies.left_unit_column),
            request.policies,
        ),
        amounts(
            tables[1],
            (request.right_amount, request.policies.right_unit_column),
            request.policies,
        ),
    )
    records = []
    counts: dict[str, int] = {}
    for pair in paired_records(tables, request):
        evidence = comparison_record(pair, operands, request)
        for side, table, record, keys in zip(
            ("left", "right"),
            tables,
            pair,
            (request.left_keys, request.right_keys),
            strict=True,
        ):
            evidence[f"{side}_key"] = (
                {name: typed_value(table.cell(record[1], name)) for name in keys}
                if record
                else None
            )
        counts[evidence["status"]] = counts.get(evidence["status"], 0) + 1
        records.append(evidence)
    return {
        **page(records, request),
        "status_counts": counts,
        "excluded_left_amounts": sum(value is None for value in operands[0].values()),
        "excluded_right_amounts": sum(value is None for value in operands[1].values()),
        "unit": request.policies.unit,
        "left_amount_column": request.left_amount,
        "right_amount_column": request.right_amount,
    }


def age_table(table: Table, request: Aging) -> dict[str, Any]:
    """Sum signed amounts in exhaustive calendar-day aging buckets.

    Args:
        table: Bounded source snapshot.
        request: Due-date column, as-of date and inclusive overdue bounds.

    Returns:
        Labelled bucket totals, record counts and explicit exclusions.

    Raises:
        ValueError: Amounts, dates, units or columns are invalid.
        DecimalException: A total exceeds exact precision.
    """
    table.require([request.due_date])
    parsed = amounts(
        table, (request.amount, request.policies.left_unit_column), request.policies
    )
    labels = (
        ["not_due", "due_today"]
        + [
            f"{lower + 1}_{upper}_days"
            for lower, upper in zip(
                [0] + request.bucket_days[:-1], request.bucket_days, strict=True
            )
        ]
        + [f"over_{request.bucket_days[-1]}_days"]
    )
    totals = [Decimal(0) for _ in labels]
    counts = [0 for _ in labels]
    excluded = 0
    for row_number, row in table.records:
        days = (request.as_of - iso_date(table.cell(row, request.due_date))).days
        amount = parsed[row_number]
        if amount is None:
            excluded += 1
            continue
        bucket = (
            0
            if days < 0
            else 1
            if days == 0
            else next(
                (
                    index + 2
                    for index, upper in enumerate(request.bucket_days)
                    if days <= upper
                ),
                len(labels) - 1,
            )
        )
        totals[bucket] += amount
        counts[bucket] += 1
    return {
        "matched_rows": len(table.records),
        "excluded_null_amounts": excluded,
        "unit": request.policies.unit,
        "amount_column": request.amount,
        "date_column": request.due_date,
        "buckets": [
            {"label": label, "amount": format(total, "f"), "record_count": count}
            for label, total, count in zip(labels, totals, counts, strict=True)
        ],
    }


def execute_financial(
    grids: dict[str, list[list[Any]]], request: FinancialRequest
) -> dict[str, Any]:
    """Execute validated intent without model arithmetic or expression evaluation.

    Args:
        grids: Finite source regions keyed by checked table identity.
        request: Financial operation and explicit policies.

    Returns:
        Exact labelled evidence, with full applied query and numeric contract.

    Raises:
        ValueError: Source values, cardinality or arithmetic exceed the contract.
    """
    query = request.query
    left = Table.read(grids[request.table_id])
    try:
        with localcontext(
            Context(prec=DECIMAL_PRECISION, Emax=128, Emin=-128)
        ) as context:
            context.traps[Inexact] = True
            if isinstance(query, (Records, Lookup)):
                evidence = select_records(left, query)
            elif isinstance(query, Aging):
                evidence = age_table(left, query)
            else:
                tables = (left, Table.read(grids[query.right_table_id]))
                evidence = (
                    join_records(tables, query)
                    if isinstance(query, Join)
                    else compare_tables(tables, query)
                )
    except DecimalException as error:
        raise ValueError(
            "Financial arithmetic exceeds exact 64-digit precision or exponent budget"
        ) from error
    return {
        "operation": query.operation,
        "query": request.model_dump(mode="json"),
        "numeric_contract": {
            "precision": DECIMAL_PRECISION,
            "invalid_values": "reject",
            "arithmetic": "exact_except_explicit_percentage_rounding",
            "row_coordinates": "one_based_within_registered_range",
            "empty_buckets": "zero_with_record_count",
        },
        **evidence,
    }
