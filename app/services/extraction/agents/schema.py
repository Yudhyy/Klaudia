"""Schema utilities for Qwen3.5-4B JSON output.

Qwen3.5-4B is fine-tuned on EXTRACTION_SCHEMA but real-world output drifts:
extra keys, missing nested keys, arrays that should be lists end up as
strings or None, all-empty placeholder entries leftover from the schema
template, etc. These helpers normalize back to the canonical shape so
downstream code (DB persistence, sheet formatter, main agent reasoning)
sees a stable contract.

Strategy:
  1. validate_and_merge — deep-merge model output into a fresh schema copy.
     Unknown keys are dropped (not silently passed to DB).
  2. _ensure_arrays — guarantee array fields are always lists.
  3. _clean_empty_arrays — drop placeholder entries that have no real value
     in the field that matters most (item_name, value, amount).
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from app.services.extraction.agents.config import EXTRACTION_SCHEMA

logger = logging.getLogger(__name__)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    """Merge override into base in place. Only keys present in base are kept;
    extras from the model are dropped (defensive against schema drift).
    """
    for key, val in override.items():
        if key not in base:
            continue
        if isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        elif isinstance(val, list) and val:
            base[key] = val
        elif isinstance(val, str) and val:
            base[key] = val
        elif isinstance(val, (int, float)) and val != 0:
            # Numeric fields in the schema are TEXT; coerce so DB serialization
            # is uniform.
            base[key] = str(val)
        # None / "" / 0 / [] → keep base default


def _ensure_arrays(merged: dict[str, Any]) -> None:
    """Guarantee array fields are lists, never None or string."""
    root_arrays = ("items",)
    info_arrays = ("store_contacts",)
    pay_arrays = ("discounts", "taxes", "additional_charges")

    for field in root_arrays:
        if not isinstance(merged.get(field), list):
            merged[field] = []
    info = merged.setdefault("info", {})
    for field in info_arrays:
        if not isinstance(info.get(field), list):
            info[field] = []
    payment = merged.setdefault("payment", {})
    for field in pay_arrays:
        if not isinstance(payment.get(field), list):
            payment[field] = []


def _clean_empty_arrays(merged: dict[str, Any]) -> None:
    """Drop placeholder entries (all-empty primary field) from array fields."""
    info = merged.get("info", {})
    payment = merged.get("payment", {})

    checks: list[tuple[dict[str, Any], str, str]] = [
        (info, "store_contacts", "value"),
        (merged, "items", "item_name"),
        (payment, "discounts", "amount"),
        (payment, "taxes", "amount"),
        (payment, "additional_charges", "amount"),
    ]

    for container, key, primary in checks:
        if not isinstance(container, dict):
            continue
        arr = container.get(key)
        if not isinstance(arr, list):
            continue
        container[key] = [
            item
            for item in arr
            if isinstance(item, dict) and str(item.get(primary, "")).strip()
        ]


def validate_and_merge(ai_result: dict[str, Any]) -> dict[str, Any]:
    """Merge model output into a clean schema copy.

    Guarantees on return value:
      - Top-level keys: info, items, payment
      - All array fields are lists
      - No placeholder empty entries leaked from the schema template
      - Extra keys from the model are dropped
    """
    merged = copy.deepcopy(EXTRACTION_SCHEMA)
    if not isinstance(ai_result, dict):
        logger.warning("validate_and_merge: input is not dict (%s)", type(ai_result))
        # Treat as empty merge so caller still gets valid schema shell
        ai_result = {}
    _deep_merge(merged, ai_result)
    _ensure_arrays(merged)
    _clean_empty_arrays(merged)
    return merged
