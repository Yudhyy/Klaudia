"""Financial evidence preserves exact values, matching rules and page boundaries."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from ledger.financial import FinancialRequest, execute_financial


def request(operation, **fields):
    """Build explicit financial intent for the fixture tables."""
    authority = {
        name: fields.pop(name)
        for name in ("user_id", "table_range", "expected_sheet_revision")
        if name in fields
    }
    return FinancialRequest.model_validate(
        {"table_id": "left", "query": {"operation": operation, **fields}, **authority}
    )


def test_record_sort_and_page_preserve_exact_typed_values():
    """Numeric sorting is stable and pages report the full match count."""
    query = request(
        "records",
        columns=["ID", "Amount"],
        sort=[
            {"column": "Amount", "kind": "number", "direction": "desc", "nulls": "last"}
        ],
        numeric_text="reject",
        offset=1,
        limit=1,
    )
    evidence = execute_financial(
        {
            "left": [
                ["ID", "Amount"],
                ["a", Decimal("0.1234567890123456789")],
                ["b", 2],
                ["c", 2],
            ]
        },
        query,
    )
    assert evidence["matched_rows"] == 3
    assert evidence["next_offset"] == 2
    assert evidence["records"][0]["row"] == 4
    assert evidence["records"][0]["values"]["ID"] == {"type": "str", "value": "c"}


def test_lookup_rejects_duplicate_matches():
    """Lookup never silently selects the first duplicate."""
    query = request(
        "lookup",
        columns=["ID"],
        filters=[{"column": "ID", "value": "a"}],
        missing="null",
    )
    with pytest.raises(ValueError, match="multiple"):
        execute_financial({"left": [["ID"], ["a"], ["a"]]}, query)


def test_left_join_preserves_unmatched_rows_and_rejects_duplicate_keys():
    """A lookup-style join cannot multiply financial records."""
    query = request(
        "join",
        right_table_id="right",
        left_keys=["ID"],
        right_keys=["ID"],
        columns=["ID"],
        right_columns=["Name"],
        join_kind="left",
        cardinality="many_to_one",
        null_keys="reject",
    )
    tables = {"left": [["ID"], ["a"], ["b"]], "right": [["ID", "Name"], ["a", "Alice"]]}
    evidence = execute_financial(tables, query)
    assert evidence["matched_rows"] == 2
    assert evidence["records"][1]["right"] is None
    tables["right"].append(["a", "Another"])
    with pytest.raises(ValueError, match="Duplicate"):
        execute_financial(tables, query)


def policies():
    """Return the fixture's declared numeric rules."""
    return {
        "numeric_text": "decimal",
        "null_amounts": "reject",
        "unit": "USD",
        "left_unit_column": "Currency",
        "right_unit_column": "Currency",
    }


def comparison(operation, **fields):
    """Build a keyed comparison with no inferred accounting conventions."""
    return request(
        operation,
        right_table_id="right",
        left_keys=["ID"],
        right_keys=["ID"],
        left_amount="Amount",
        right_amount="Amount",
        policies=policies(),
        null_keys="reject",
        **fields,
    )


def test_reconciliation_reports_missing_separately_from_zero():
    """Tolerance applies to matched amounts and never hides a missing key."""
    query = comparison("reconcile", tolerance="0.01")
    tables = {
        "left": [["ID", "Amount", "Currency"], ["a", "1.01", "USD"], ["b", 0, "USD"]],
        "right": [["ID", "Amount", "Currency"], ["a", 1, "USD"]],
    }
    evidence = execute_financial(tables, query)
    assert [item["status"] for item in evidence["records"]] == ["matched", "left_only"]
    assert evidence["records"][0]["difference"] == "0.01"
    assert evidence["records"][1]["right_amount"] is None


def test_variance_uses_explicit_direction_and_zero_baseline_policy():
    """Variance percent uses the baseline and the requested rounding mode."""
    query = comparison(
        "variance",
        direction="left_minus_right",
        zero_baseline="null",
        percentage_places=2,
        rounding="ROUND_HALF_EVEN",
    )
    tables = {
        "left": [["ID", "Amount", "Currency"], ["a", 4, "USD"], ["b", 1, "USD"]],
        "right": [["ID", "Amount", "Currency"], ["a", 3, "USD"], ["b", 0, "USD"]],
    }
    evidence = execute_financial(tables, query)
    assert evidence["records"][0]["percentage"] == "33.33"
    assert evidence["records"][1]["percentage"] is None
    assert evidence["records"][1]["percentage_status"] == "zero_baseline"


def test_aging_uses_explicit_date_and_inclusive_upper_boundaries():
    """Future and due-today amounts stay separate from overdue buckets."""
    query = request(
        "aging",
        amount="Amount",
        due_date="Due",
        as_of="2026-09-13",
        bucket_days=[30, 60, 90],
        policies={**policies(), "right_unit_column": None},
    )
    tables = {
        "left": [
            ["Amount", "Due", "Currency"],
            [1, "2026-09-14", "USD"],
            [2, "2026-09-13", "USD"],
            [3, "2026-08-14", "USD"],
            [4, "2026-08-13", "USD"],
        ]
    }
    evidence = execute_financial(tables, query)
    assert [(item["label"], item["amount"]) for item in evidence["buckets"]][:4] == [
        ("not_due", "1"),
        ("due_today", "2"),
        ("1_30_days", "3"),
        ("31_60_days", "4"),
    ]


@pytest.mark.parametrize("field", ["user_id", "table_range", "expected_sheet_revision"])
def test_financial_intent_rejects_authority(field):
    """The model cannot choose authority or revisions."""
    with pytest.raises(ValidationError):
        request("records", columns=["ID"], **{field: 1})


@pytest.mark.parametrize(
    "value",
    [True, "1,000", "NaN", "=SUM(A1:A2)", Decimal("1e10000"), Decimal("1." + "1" * 64)],
)
def test_invalid_amounts_reject_even_on_unmatched_records(value):
    """Matching cannot hide malformed or over-budget financial operands."""
    query = comparison("reconcile", tolerance="0")
    with pytest.raises(ValueError):
        execute_financial(
            {
                "left": [["ID", "Amount", "Currency"], ["a", value, "USD"]],
                "right": [["ID", "Amount", "Currency"]],
            },
            query,
        )


@pytest.mark.parametrize("amount,unit", [(None, "USD"), (1, "EUR"), (1, None)])
def test_amounts_and_units_never_silently_default(amount, unit):
    """Blank amounts and wrong currencies fail under the explicit policy."""
    with pytest.raises(ValueError):
        execute_financial(
            {
                "left": [["ID", "Amount", "Currency"], ["a", amount, unit]],
                "right": [["ID", "Amount", "Currency"]],
            },
            comparison("reconcile", tolerance="0"),
        )


def test_excluded_null_amounts_are_counted_without_becoming_zero():
    """A null policy excludes a pair rather than declaring it reconciled."""
    query = comparison("reconcile", tolerance="0")
    query = query.model_copy(
        update={
            "query": query.query.model_copy(
                update={
                    "policies": query.query.policies.model_copy(
                        update={"null_amounts": "exclude"}
                    )
                }
            )
        }
    )
    evidence = execute_financial(
        {
            "left": [["ID", "Amount", "Currency"], ["a", None, "USD"]],
            "right": [["ID", "Amount", "Currency"], ["a", 0, "USD"]],
        },
        query,
    )
    assert evidence["records"][0]["status"] == "excluded_null_amount"
    assert evidence["records"][0]["difference"] is None
    assert evidence["excluded_left_amounts"] == 1


def test_full_comparison_validates_later_pages():
    """A small page cannot hide duplicate keys or invalid later records."""
    query = comparison("reconcile", tolerance="0", limit=1)
    with pytest.raises(ValueError, match="Duplicate"):
        execute_financial(
            {
                "left": [
                    ["ID", "Amount", "Currency"],
                    ["a", 1, "USD"],
                    ["b", 2, "USD"],
                    ["b", 2, "USD"],
                ],
                "right": [["ID", "Amount", "Currency"]],
            },
            query,
        )


@pytest.mark.parametrize(
    "rounding,expected",
    [("ROUND_HALF_EVEN", "0.12"), ("ROUND_HALF_UP", "0.13"), ("ROUND_DOWN", "0.12")],
)
def test_percentage_rounding_happens_once_at_exact_ties(rounding, expected):
    """Exact rational division avoids double rounding at a half-way value."""
    query = comparison(
        "variance",
        direction="left_minus_right",
        zero_baseline="reject",
        percentage_places=2,
        rounding=rounding,
    )
    evidence = execute_financial(
        {
            "left": [["ID", "Amount", "Currency"], ["a", 801, "USD"]],
            "right": [["ID", "Amount", "Currency"], ["a", 800, "USD"]],
        },
        query,
    )
    assert evidence["records"][0]["percentage"] == expected


def test_reverse_variance_uses_left_as_signed_baseline():
    """Direction changes both the difference and the percentage denominator."""
    query = comparison(
        "variance",
        direction="right_minus_left",
        zero_baseline="reject",
        percentage_places=2,
        rounding="ROUND_HALF_EVEN",
    )
    evidence = execute_financial(
        {
            "left": [["ID", "Amount", "Currency"], ["a", -2, "USD"]],
            "right": [["ID", "Amount", "Currency"], ["a", -3, "USD"]],
        },
        query,
    )
    assert evidence["records"][0]["difference"] == "-1"
    assert evidence["records"][0]["percentage"] == "50.00"


def test_null_keys_never_match_each_other():
    """Explicit never-match keeps two blank keys as separate missing records."""
    query = comparison("reconcile", tolerance="0")
    query = query.model_copy(
        update={"query": query.query.model_copy(update={"null_keys": "never_match"})}
    )
    evidence = execute_financial(
        {
            "left": [["ID", "Amount", "Currency"], [None, 0, "USD"]],
            "right": [["ID", "Amount", "Currency"], [None, 0, "USD"]],
        },
        query,
    )
    assert evidence["status_counts"] == {"left_only": 1, "right_only": 1}


def test_numeric_key_identity_preserves_boolean_and_text_distinctions():
    """Numeric representations match, while text codes and booleans stay distinct."""
    query = comparison("reconcile", tolerance="0")
    evidence = execute_financial(
        {
            "left": [
                ["ID", "Amount", "Currency"],
                [1, 0, "USD"],
                [True, 0, "USD"],
                ["1", 0, "USD"],
            ],
            "right": [["ID", "Amount", "Currency"], [Decimal("1.0"), 0, "USD"]],
        },
        query,
    )
    assert evidence["status_counts"] == {"matched": 1, "left_only": 2}


@pytest.mark.parametrize("as_of", [0, "20260913", "2026-09-13T00:00:00Z"])
def test_aging_rejects_implicit_date_conversions(as_of):
    """As-of dates cannot be interpreted as timestamps or compact date codes."""
    with pytest.raises(ValidationError):
        request(
            "aging",
            amount="Amount",
            due_date="Due",
            as_of=as_of,
            bucket_days=[30],
            policies={**policies(), "right_unit_column": None},
        )


def test_aging_exact_thousand_rows_and_precision_overflow():
    """Bucket arithmetic scales to 1000 rows and rejects an inexact total."""
    query = request(
        "aging",
        amount="Amount",
        due_date="Due",
        as_of="2026-09-13",
        bucket_days=[30],
        policies={**policies(), "right_unit_column": None},
    )
    rows = [["Amount", "Due", "Currency"]] + [
        [Decimal("0.001"), "2026-09-12", "USD"] for _ in range(1000)
    ]
    evidence = execute_financial({"left": rows}, query)
    assert evidence["buckets"][2]["amount"] == "1.000"
    rows[1][0] = Decimal("9" * 64)
    with pytest.raises(ValueError, match="precision"):
        execute_financial({"left": rows}, query)


def test_empty_sources_still_validate_columns_and_row_budget():
    """Empty tables and small pages do not bypass structural limits."""
    query = request("records", columns=["Missing"], limit=1)
    with pytest.raises(ValueError, match="absent"):
        execute_financial({"left": [["ID"]]}, query)
    with pytest.raises(ValueError, match="10000"):
        execute_financial(
            {"left": [["ID"]] + [[index] for index in range(10001)]}, query
        )


def test_aging_rejects_a_policy_for_an_absent_right_source():
    """Returned policies must describe checks that the operation can perform."""
    with pytest.raises(ValidationError, match="no right source"):
        request(
            "aging",
            amount="Amount",
            due_date="Due",
            as_of="2026-09-13",
            bucket_days=[30],
            policies=policies(),
        )
