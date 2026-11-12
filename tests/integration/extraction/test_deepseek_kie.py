"""Opt-in live check for DeepSeek receipt extraction.

Run with ``RUN_LIVE_DEEPSEEK_KIE=1 uv run pytest
tests/integration/extraction/test_deepseek_kie.py -v -s``. The test calls the
provider client directly, so Redis and PostgreSQL cannot return a cached result.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from dotenv import load_dotenv

from klaudia_api.adapters.documents.extraction.schema import validate_and_merge
from klaudia_api.adapters.documents.kie.client import KIEClient
from klaudia_api.adapters.documents.media.normalizer import to_canonical_jpg
from klaudia_api.composition.settings import Settings

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_IMAGE = PROJECT_ROOT / "sample-data" / "receipt" / "004-receipt.png"
GROUND_TRUTH = PROJECT_ROOT / "sample-data" / "labels" / "images" / "004-receipt.json"
MODEL = "deepseek-flash"


@pytest.mark.asyncio
async def test_deepseek_extracts_receipt_004() -> None:
    """Match receipt 004 except for the accepted long-ID OCR miss."""
    if os.getenv("RUN_LIVE_DEEPSEEK_KIE") != "1":
        pytest.skip("set RUN_LIVE_DEEPSEEK_KIE=1 to spend one live API request")

    settings = Settings(MOCK_KIE=False, KIE_MODEL=MODEL)
    if not settings.deepseek_api_key:
        pytest.skip("DEEPSEEK_API_KEY is not set")

    client = KIEClient(settings)
    try:
        jpg_bytes = to_canonical_jpg(SAMPLE_IMAGE.read_bytes())
        extracted = validate_and_merge(await client.extract_from_image(jpg_bytes))
    finally:
        await client.shutdown()

    expected = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    print(json.dumps(extracted, ensure_ascii=False, indent=2))
    extracted_without_id = deepcopy(extracted)
    expected_without_id = deepcopy(expected)
    extracted_without_id["info"].pop("receipt_id")
    expected_without_id["info"].pop("receipt_id")
    assert extracted_without_id == expected_without_id
