"""Shared byte budgets for model-facing ledger evidence."""

import json
from typing import Any

from ledger_core.resources import ResourceInspection

MAX_EVIDENCE_BYTES = 65_536


def schema_page(
    descriptor: dict[str, Any], query: ResourceInspection
) -> dict[str, Any]:
    """Select a bounded column page without altering the source descriptor.

    Args:
        descriptor: Complete registered metadata from an authorised read.
        query: Validated schema pagination request.

    Returns:
        Metadata with a column page and explicit continuation evidence.
    """
    columns = descriptor["columns"]
    end = query.column_offset + query.column_limit
    return {
        **descriptor,
        "columns": columns[query.column_offset : end],
        "column_count": len(columns),
        "column_offset": query.column_offset,
        "has_more_columns": end < len(columns),
    }


def bounded_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Keep snapshot and aggregate responses within the context byte budget.

    Args:
        evidence: Complete evidence ready to return from a read operation.

    Returns:
        The unchanged evidence when it fits the budget.

    Raises:
        ValueError: The response is too large to return without truncation.
    """
    if (
        len(json.dumps(evidence, ensure_ascii=False).encode("utf-8"))
        > MAX_EVIDENCE_BYTES
    ):
        raise ValueError(
            "Evidence exceeds 65536 bytes; narrow the range, filters or metrics"
        )
    return evidence
