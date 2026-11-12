import json
import logging
from contextvars import ContextVar, Token
from typing import Any, Literal

from toon_format import encode as encode_toon

logger = logging.getLogger(__name__)

ExtractionContextFormat = Literal["toon", "json"]

# Request-local active-workbook hint; tools check ownership separately.
_ACTIVE_SPREADSHEET: ContextVar[str | None] = ContextVar(
    "active_spreadsheet", default=None
)


def set_active_spreadsheet(spreadsheet_id: str | None) -> Token:
    """Bind the active spreadsheet for the current request context."""
    return _ACTIVE_SPREADSHEET.set(spreadsheet_id)


def reset_active_spreadsheet(token: Token) -> None:
    """Restore the scope captured by the matching set_active_spreadsheet."""
    _ACTIVE_SPREADSHEET.reset(token)


def get_active_spreadsheet() -> str | None:
    """Return the active spreadsheet id, or None when unscoped."""
    return _ACTIVE_SPREADSHEET.get()


def _encode_compact_json(value: Any) -> str:
    """Encode a value as compact UTF-8 JSON."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _serialize_extraction_pages(
    pages: list[dict[str, Any]], output_format: ExtractionContextFormat
) -> tuple[str, str]:
    """Serialize pages and return the format label with the encoded text."""
    if output_format == "json":
        return "JSON", _encode_compact_json(pages)

    try:
        return "TOON", encode_toon(pages)
    except Exception as exc:
        logger.warning(
            "TOON extraction-context encoding failed; using JSON (%s)",
            type(exc).__name__,
        )
        return "JSON", _encode_compact_json(pages)


def build_extraction_context(
    extraction_data: dict[str, Any] | None,
    output_format: ExtractionContextFormat = "toon",
) -> str:
    """Format extracted document facts for the main agent.

    Args:
        extraction_data: File metadata and validated page extractions.
        output_format: Prompt-facing format for the page payload.

    Returns:
        A prompt-ready extraction block, or an empty string without data.
    """
    if not extraction_data:
        return ""
    format_label, serialized_pages = _serialize_extraction_pages(
        extraction_data.get("pages", []), output_format
    )
    return (
        f"[Extraction Result]\n"
        f"File: {extraction_data.get('file_name', 'unknown')}\n"
        f"Status: {extraction_data.get('status', 'unknown')}\n"
        f"Summary: {extraction_data.get('summary', 'N/A')}\n"
        f"Data ({format_label}):\n{serialized_pages}"
    )
