"""Keep agent evidence within its tool response byte budget."""

import json
from typing import Any

MAX_EVIDENCE_BYTES = 65_536


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
