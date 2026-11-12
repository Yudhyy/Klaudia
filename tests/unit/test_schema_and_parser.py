"""Schema validation + 3-layer JSON parser tests.

These exercise the defensive layer between vLLM and DB persistence: even
when Qwen3.5-4B outputs malformed JSON or partial data, downstream code must
see a stable schema.
"""

import pytest

from klaudia_api.adapters.documents.extraction.parser import (
    OCRJsonParseError,
    parse_extraction_json,
)
from klaudia_api.adapters.documents.extraction.schema import validate_and_merge


# ── Schema merge ───────────────────────────────────────────────────────────


def test_merge_returns_full_schema_for_empty_input():
    out = validate_and_merge({})
    assert set(out.keys()) == {"info", "items", "payment"}
    assert out["items"] == []


def test_merge_drops_unknown_top_level_keys():
    out = validate_and_merge({"unexpected": "value", "info": {"store_name": "X"}})
    assert "unexpected" not in out
    assert out["info"]["store_name"] == "X"


def test_merge_preserves_items_list():
    out = validate_and_merge({"items": [{"item_name": "Indomie", "quantity": "2"}]})
    assert len(out["items"]) == 1
    assert out["items"][0]["item_name"] == "Indomie"


def test_merge_strips_empty_placeholder_items():
    """Items with empty item_name are placeholder leftovers from the schema
    template; they must not survive into DB."""
    out = validate_and_merge(
        {"items": [{"item_name": "Real", "quantity": "1"}, {"item_name": "  "}]}
    )
    assert len(out["items"]) == 1
    assert out["items"][0]["item_name"] == "Real"


def test_merge_coerces_non_dict_input_to_empty():
    out = validate_and_merge("not a dict")  # type: ignore[arg-type]
    assert out["items"] == []
    assert "info" in out


def test_merge_ensures_arrays_when_model_returns_string():
    out = validate_and_merge({"items": "not a list"})
    assert out["items"] == []


def test_merge_ensures_payment_taxes_array():
    out = validate_and_merge({"payment": {"taxes": None}})
    assert out["payment"]["taxes"] == []


# ── JSON parser ────────────────────────────────────────────────────────────


def test_parse_clean_json():
    assert parse_extraction_json('{"a": 1}') == {"a": 1}


def test_parse_strips_markdown_fences():
    raw = '```json\n{"a": 1}\n```'
    assert parse_extraction_json(raw) == {"a": 1}


def test_parse_strips_generic_fences():
    raw = '```\n{"a": 1}\n```'
    assert parse_extraction_json(raw) == {"a": 1}


def test_parse_strips_leading_preamble():
    raw = 'Here is the result: {"a": 1}'
    assert parse_extraction_json(raw) == {"a": 1}


def test_parse_repairs_truncated_json():
    """Common failure mode: model hits max_tokens mid-array."""
    raw = '{"a": 1, "b": [1, 2, 3'
    out = parse_extraction_json(raw)
    assert out["a"] == 1


def test_parse_repairs_trailing_comma():
    raw = '{"a": 1,}'
    out = parse_extraction_json(raw)
    assert out == {"a": 1}


def test_parse_unwraps_list_of_one_object():
    """Some prompts cause the model to wrap output in a single-element list."""
    raw = '[{"a": 1}]'
    out = parse_extraction_json(raw)
    assert out == {"a": 1}


def test_parse_raises_on_pure_garbage():
    with pytest.raises(OCRJsonParseError):
        parse_extraction_json("not even close to json")


def test_parse_error_truncates_diagnostic_raw():
    long_garbage = "x" * 1000
    with pytest.raises(OCRJsonParseError) as exc:
        parse_extraction_json(long_garbage)
    assert len(exc.value.raw) <= 400
