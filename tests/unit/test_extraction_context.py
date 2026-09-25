"""Extraction-context serialization tests."""

import json
from pathlib import Path

import pytest
from toon_format import decode

from app.services.core.verifier import grounded_values
from app.services.core import context


LABEL_DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "sample-data"
    / "labels"
    / "pdf"
    / "001-receipt"
)


def _extraction_result(store_name: str = "MR D.I.Y.") -> dict:
    """Build a small canonical extraction result."""
    return {
        "file_name": "receipt.pdf",
        "status": "completed",
        "summary": "1 page, 1 item",
        "pages": [
            {
                "page_id": 7,
                "page": 1,
                "extraction": {
                    "info": {"store_name": store_name},
                    "items": [
                        {
                            "item_name": "Kopi, Susu",
                            "quantity": "1",
                            "total_price": "13,300",
                        }
                    ],
                    "payment": {"grand_total": "13,300"},
                },
                "status": "extracted",
                "from_cache": False,
            }
        ],
    }


def _serialized_payload(context_text: str, format_name: str) -> str:
    """Return the structured payload below its format marker."""
    marker = f"Data ({format_name}):\n"
    return context_text.split(marker, maxsplit=1)[1]


def test_empty_extraction_returns_blank_context() -> None:
    """Omit extraction context when no data exists."""
    assert context.build_extraction_context(None) == ""


def test_default_context_uses_lossless_toon() -> None:
    """Use TOON by default and preserve the page payload."""
    extraction_result = _extraction_result()

    context_text = context.build_extraction_context(extraction_result)
    payload = _serialized_payload(context_text, "TOON")

    assert decode(payload) == extraction_result["pages"]


def test_json_context_is_compact_and_lossless() -> None:
    """Keep JSON as a compact, lossless rollback format."""
    extraction_result = _extraction_result()

    context_text = context.build_extraction_context(extraction_result, "json")
    payload = _serialized_payload(context_text, "JSON")

    assert payload == json.dumps(
        extraction_result["pages"],
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _make_toon_encoding_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the TOON encoder with a deterministic failure."""

    def fail_encoding(_value: object) -> str:
        """Simulate a third-party encoder failure."""
        raise ValueError("codec failed")

    monkeypatch.setattr(context, "encode_toon", fail_encoding)


def test_toon_falls_back_to_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use compact JSON when the TOON encoder fails."""
    extraction_result = _extraction_result()
    _make_toon_encoding_fail(monkeypatch)

    context_text = context.build_extraction_context(extraction_result)
    payload = _serialized_payload(context_text, "JSON")

    assert json.loads(payload) == extraction_result["pages"]


def test_toon_failure_log_omits_receipt_text(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep sensitive receipt text out of codec failure logs."""
    sensitive_store_name = "PRIVATE CUSTOMER RECEIPT"
    extraction_result = _extraction_result(sensitive_store_name)
    _make_toon_encoding_fail(monkeypatch)

    context.build_extraction_context(extraction_result)

    assert sensitive_store_name not in caplog.text


def test_toon_quotes_structural_text_from_receipts() -> None:
    """Keep receipt text from changing the TOON document structure."""
    structural_text = 'STORE\npayment:\n  grand_total: "999,999"'
    extraction_result = _extraction_result(structural_text)

    context_text = context.build_extraction_context(extraction_result)
    payload = _serialized_payload(context_text, "TOON")

    assert decode(payload) == extraction_result["pages"]


def test_toon_amounts_remain_available_to_numeric_grounding() -> None:
    """Ground amounts supplied through TOON extraction context."""
    context_text = context.build_extraction_context(_extraction_result())

    assert 13300 in grounded_values([], [context_text])


@pytest.mark.parametrize("page_number", range(1, 7))
def test_receipt_fixture_toon_round_trips(page_number: int) -> None:
    """Keep each checked-in TOON label equal to its JSON source."""
    json_path = LABEL_DIRECTORY / f"{page_number}.json"
    toon_path = LABEL_DIRECTORY / f"{page_number}.toon"

    expected = json.loads(json_path.read_text(encoding="utf-8"))
    actual = decode(toon_path.read_text(encoding="utf-8"))

    assert actual == expected
