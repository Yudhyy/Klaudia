"""Exact labelled totals over selected table regions."""

import pytest
from decimal import localcontext

from ledger_core.query import AggregateQuery, aggregate_grid
from tests.e2e.synthetic import build_bulk_ledger
from tests.e2e.synthetic_finance import build_journal_ledger


def query(**changes) -> AggregateQuery:
    """Build a sum query with optional grouping, filters or range changes."""
    fields = {
        "table_range": "A1:C1001",
        "metrics": [{"column": "Amount", "operation": "sum"}],
        "numeric_text": "decimal",
    }
    return AggregateQuery(**(fields | changes))


def test_thousand_rows_return_exact_decimal_total():
    """Large inputs produce an exact sum without returning individual records."""
    rows = [["Amount"], *[["0.1"] for _ in range(1000)]]
    report = aggregate_grid(rows, query())
    assert report["matched_rows"] == 1000
    assert report["groups"][0]["metrics"][0]["value"] == "100.0"
    assert "rows" not in report


def test_debit_credit_keep_distinct_labels():
    """Equal-looking totals cannot silently lose their originating column."""
    rows = [["Debit", "Credit"], [42500000, 41050000]]
    request = query(
        metrics=[{"column": name, "operation": "sum"} for name in ("Debit", "Credit")]
    )
    metrics = aggregate_grid(rows, request)["groups"][0]["metrics"]
    assert [(metric["column"], metric["value"]) for metric in metrics] == [
        ("Debit", "42500000"),
        ("Credit", "41050000"),
    ]


def test_grouped_filtered_totals_keep_currency_separate():
    """Filtering and grouping operate on rows before any amounts are added."""
    rows = [
        ["Category", "Amount", "Currency"],
        ["Travel", 10, "USD"],
        ["Travel", 185000, "IDR"],
        ["Meals", 9000, "IDR"],
    ]
    request = query(
        group_by=["Currency"],
        filters=[{"column": "Category", "value": "Travel"}],
        unit_column="Currency",
    )
    report = aggregate_grid(rows, request)
    assert report["matched_rows"] == 2
    assert [
        (group["key"], group["unit"], group["metrics"][0]["value"])
        for group in report["groups"]
    ] == [({"Currency": "USD"}, "USD", "10"), ({"Currency": "IDR"}, "IDR", "185000")]


def test_mixed_currency_sum_is_rejected():
    """Declared units must agree within each aggregate group."""
    with pytest.raises(ValueError, match="Mixed units"):
        aggregate_grid(
            [["Amount", "Currency"], [10, "USD"], [1000, "IDR"]],
            query(unit_column="Currency"),
        )


def test_region_excludes_other_tables_and_footer():
    """A table away from A1 does not absorb nearby summaries or footers."""
    rows = [[999999], [], [None, "Amount"], [None, 10], [None, 20], [None, 30]]
    report = aggregate_grid(rows, query(table_range="B3:B5"))
    assert report["groups"][0]["metrics"][0]["value"] == "30"
    assert report["matched_rows"] == 2


def test_blank_policy_and_count_are_explicit():
    """Empty amounts are excluded and reported; zero remains a numeric value."""
    rows = [["Amount", "Invoice"], [None, "one"], ["", "two"], [0, "three"]]
    request = query(
        metrics=[
            {"column": "Amount", "operation": "sum"},
            {"column": "Invoice", "operation": "count"},
        ]
    )
    metrics = aggregate_grid(rows, request)["groups"][0]["metrics"]
    assert metrics[0] == {
        "column": "Amount",
        "operation": "sum",
        "value": "0",
        "non_null_count": 1,
        "blank_count": 2,
    }
    assert metrics[1]["value"] == "3"


@pytest.mark.parametrize(
    "amount",
    [True, "Rp 15.000", "1,000", "=SUM(A1:A2)", "NaN", float("inf"), {"amount": 10}],
)
def test_invalid_amount_never_becomes_a_partial_total(amount):
    """Malformed financial inputs fail instead of producing plausible totals."""
    with pytest.raises(ValueError):
        aggregate_grid([["Amount"], [10], [amount]], query())


@pytest.mark.parametrize("header", [["Amount", "Amount"], ["Missing"], [None]])
def test_missing_or_duplicate_columns_are_rejected(header):
    """Column targeting must resolve to one actual header."""
    with pytest.raises(ValueError):
        aggregate_grid([header, [10]], query())


def test_group_budget_rejects_truncated_totals():
    """Too many groups require a narrower query instead of hidden omissions."""
    with pytest.raises(ValueError, match="group limit"):
        aggregate_grid(
            [["Amount", "Category"], [1, "A"], [2, "B"]],
            query(group_by=["Category"], max_groups=1),
        )


@pytest.mark.parametrize("table_range", ["A:Z", "A0:C10", "C5:A1", "A1:ZZ100000"])
def test_unbounded_or_invalid_regions_are_rejected(table_range):
    """Calculations require a finite selected table region."""
    with pytest.raises(ValueError):
        aggregate_grid([["Amount"], [1]], query(table_range=table_range))


def test_no_matches_is_distinct_from_missing_data():
    """A valid query matching no records returns an explicit zero-row total."""
    report = aggregate_grid(
        [["Amount", "Category"], [1, "A"]],
        query(filters=[{"column": "Category", "value": "B"}]),
    )
    assert report["matched_rows"] == 0
    assert report["groups"][0]["metrics"][0]["value"] == "0"


def test_precision_overflow_fails_instead_of_rounding():
    """Amounts exceeding the arithmetic contract cannot be silently rounded."""
    with pytest.raises(ValueError, match="precision"):
        aggregate_grid([["Amount"], ["9" * 65], [1]], query())


def test_bulk_benchmark_ground_truth_matches_without_model_arithmetic():
    """The existing thousand-row benchmark is solved by the deterministic core."""
    ledger = build_bulk_ledger(seed=1013)
    rows = next(iter(ledger.grids.values()))
    amount_column = rows[0][-1]
    request = query(
        table_range="A1:D1001", metrics=[{"column": amount_column, "operation": "sum"}]
    )
    total = aggregate_grid(rows, request)["groups"][0]["metrics"][0]["value"]
    assert total == str(ledger.all_sheets_total)


def test_journal_benchmark_keeps_debit_and_credit_evidence():
    """The unbalanced journal retains both distinct code-computed totals."""
    ledger = build_journal_ledger(seed=1012)
    rows = next(iter(ledger.grids.values()))
    columns = rows[0][-2:]
    request = query(
        table_range="A1:E21",
        metrics=[{"column": column, "operation": "sum"} for column in columns],
    )
    metrics = aggregate_grid(rows, request)["groups"][0]["metrics"]
    assert [metric["value"] for metric in metrics] == [
        str(ledger.debit_total),
        str(ledger.credit_total),
    ]


def test_decimal_context_is_independent_of_callers_precision():
    """Caller decimal settings cannot change a query's exact arithmetic."""
    with localcontext() as context:
        context.prec = 2
        total = aggregate_grid([["Amount"], [12345], [1]], query())
    assert total["groups"][0]["metrics"][0]["value"] == "12346"


@pytest.mark.parametrize("keys", [(1, 1.0), (0, -0.0)])
def test_numeric_grouping_matches_numeric_filtering(keys):
    """Equivalent numeric keys form one group under the same equality filter."""
    rows = [["Account", "Amount"], [keys[0], 10], [keys[1], 20]]
    request = query(
        group_by=["Account"],
        filters=[{"column": "Account", "value": keys[0]}],
        max_groups=1,
    )
    groups = aggregate_grid(rows, request)["groups"]
    assert len(groups) == 1
    assert groups[0]["metrics"][0]["value"] == "30"


def test_boolean_text_and_numeric_group_keys_remain_distinct():
    """Account identifiers are not coerced across text, boolean and numeric types."""
    rows = [["Account", "Amount"], [1, 10], [True, 20], ["1", 30]]
    groups = aggregate_grid(rows, query(group_by=["Account"]))["groups"]
    assert len(groups) == 3
    assert [group["metrics"][0]["value"] for group in groups] == ["10", "20", "30"]


def test_default_query_does_not_guess_numeric_text_locale():
    """A dotted amount might be a decimal or grouped thousands, so reject by default."""
    request = AggregateQuery(
        table_range="A1:A2", metrics=[{"column": "Amount", "operation": "sum"}]
    )
    with pytest.raises(ValueError, match="explicitly enabled"):
        aggregate_grid([["Amount"], ["15.000"]], request)
