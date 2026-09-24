"""Exact labels must retain their meaning, not merely contain expected numbers."""

import pytest

from tests.e2e.rollout_cases import check_labels


def test_markdown_labels_preserve_exact_decimal_values():
    """Allow ordinary presentation around correctly labelled values."""
    check_labels(
        "- **Before total:** 200.00 USD\nAppended amount: 25 USD.",
        {"Before total": "200", "Appended amount": "25"},
    )


@pytest.mark.parametrize(
    "suffix", [" \u2014 exact sum of both rows.", " (sum over both rows)"]
)
def test_label_with_explanation_preserves_exact_amount(suffix):
    """Accept a labelled amount followed by an explanatory dash clause."""
    check_labels(
        "**Before total: 200 USD**" + suffix,
        {"Before total": "200"},
    )


@pytest.mark.parametrize(
    "answer",
    [
        "Before total: 25 USD\nAppended amount: 200 USD",
        "Before total: 200 USD\nBefore total: 200 USD\nAppended amount: 25 USD",
        "Before total: 200 EUR\nAppended amount: 25 USD",
        "The numbers are 200 and 25 USD.",
    ],
)
def test_swapped_duplicate_wrong_unit_and_unlabelled_answers_fail(answer):
    """Numeric membership does not prove correct final-answer semantics."""
    with pytest.raises(AssertionError):
        check_labels(answer, {"Before total": "200", "Appended amount": "25"})
