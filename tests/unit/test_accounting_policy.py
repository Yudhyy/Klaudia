"""Validate explicit policy metadata without interpreting policy prose."""

import pytest
from pydantic import ValidationError

from app.services.memory.contracts import DocumentEdit


def policy_fields(**changes):
    """Build explicit reconciliation policy fields for contract tests."""
    return {
        "entity": "Example Ltd",
        "jurisdiction": "declared-test-jurisdiction",
        "effective_from": "2026-01-01",
        "effective_until": "2026-12-31",
        "unit": "USD",
        "numeric_text": "decimal",
        "null_amounts": "reject",
        "null_keys": "reject",
        "tolerance": "0.01",
        **changes,
    }


def test_policy_preserves_exact_parameters():
    """Accept structured policy alongside prose without float conversion."""
    edit = DocumentEdit(
        expected_revision=0, content="Approved policy", policy=policy_fields()
    )
    assert edit.policy.model_dump(mode="json") == policy_fields()


@pytest.mark.parametrize(
    "changes",
    [
        {"effective_until": "2025-12-31"},
        {"effective_from": "2026-01-01T00:00:00"},
        {"effective_from": 1767225600},
        {"entity": " "},
        {"jurisdiction": ""},
        {"unit": "\x00"},
        {"tolerance": "-0.01"},
        {"tolerance": "NaN"},
        {"tolerance": 0.01},
        {"null_amounts": "zero"},
        {"rounding": "inferred"},
    ],
)
def test_policy_rejects_ambiguous_or_invalid_parameters(changes):
    """Reject malformed applicability and arithmetic settings at the boundary."""
    with pytest.raises(ValidationError):
        DocumentEdit(
            expected_revision=0, content="Policy", policy=policy_fields(**changes)
        )
