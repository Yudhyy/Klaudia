"""Robust JSON parsing for vLLM/Qwen3.5-4B responses.

3-layer fallback:
    1. json.loads on the cleaned content (after stripping markdown fences and
       any leaked preamble before the first '{')
    2. json_repair (handles unclosed braces, trailing commas, mixed quotes,
       truncated output) — common with LLM outputs that hit max_tokens
    3. raise OCRJsonParseError with diagnostics

This is the last line of defense. Even with vLLM `response_format=json_object`
and `guided_json`, a finetuned model can occasionally drop the closing brace
when running near the context limit. We log + repair rather than fail the
extraction so partial data is still usable.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from json_repair import repair_json

logger = logging.getLogger(__name__)


class OCRJsonParseError(ValueError):
    """All parse layers exhausted. `raw` holds the head of the offending
    string for diagnostics (capped to keep logs sane)."""

    def __init__(self, message: str, *, raw: str) -> None:
        super().__init__(message)
        self.raw = raw[:400]


def _strip_fences_and_preamble(text: str) -> str:
    """Strip markdown code fences and any preamble before the first '{'."""
    content = text.strip()

    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    brace_idx = content.find("{")
    if brace_idx > 0:
        logger.debug("Stripped %d preamble chars before JSON object", brace_idx)
        content = content[brace_idx:]

    return content


def parse_extraction_json(raw: str) -> dict[str, Any]:
    """Parse Qwen3.5-4B response into a dict. Never returns None.

    Raises:
        OCRJsonParseError if all 3 layers fail.
    """
    content = _strip_fences_and_preamble(raw)
    if not content:
        raise OCRJsonParseError("empty content", raw=raw)

    # Layer 1: standard parse, tolerating trailing junk via raw_decode.
    try:
        decoder = json.JSONDecoder()
        obj, _end = decoder.raw_decode(content)
        if isinstance(obj, dict):
            return obj
        raise OCRJsonParseError(
            f"top-level is {type(obj).__name__}, expected object", raw=raw
        )
    except json.JSONDecodeError as e1:
        logger.warning("json.loads failed (%s); trying json_repair", e1)

    # Layer 2: repair-and-parse.
    try:
        repaired = repair_json(content, return_objects=True)
        if isinstance(repaired, dict):
            logger.info("json_repair recovered structured object")
            return repaired
        if isinstance(repaired, list) and repaired and isinstance(repaired[0], dict):
            # Some models occasionally wrap the object in a single-element list.
            logger.info("json_repair recovered list-wrapped object")
            return repaired[0]
        logger.warning(
            "json_repair returned %s; falling through", type(repaired).__name__
        )
    except Exception as e2:  # pragma: no cover - defensive
        logger.warning("json_repair raised: %s", e2)

    # Layer 3: give up loudly.
    raise OCRJsonParseError(
        "all 3 parse layers failed; first 400 chars stored in .raw", raw=raw
    )
