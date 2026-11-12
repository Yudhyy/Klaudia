"""Live Gemini KIE smoke test.

Skipped unless GCP creds (Vertex) or LLM_API_KEY are configured. Makes one
real Gemini-3-flash call against a sample receipt and checks the response
matches the expected store name. Exists to catch regressions in:
    - prompt drift
    - schema drift
    - google-genai SDK upgrades
"""

from __future__ import annotations

from pathlib import Path

import pytest

from klaudia_api.adapters.documents.kie.gemini import GeminiKIEClient
from klaudia_api.adapters.documents.media.normalizer import to_canonical_jpg
from klaudia_api.composition.settings import Settings


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SAMPLE_IMAGE = _PROJECT_ROOT / "sample-data" / "receipt" / "001-receipt.jpeg"


def _credentials_available(s: Settings) -> bool:
    if s.google_genai_use_vertexai:
        return bool(s.google_cloud_project)
    return bool(s.llm_api_key)


@pytest.fixture
def live_settings():
    s = Settings(MOCK_KIE=False, KIE_MODEL="gemini-3.1-flash-lite")
    if not _credentials_available(s):
        pytest.skip(
            "Gemini credentials not configured; set GOOGLE_CLOUD_PROJECT "
            "(Vertex) or LLM_API_KEY (Developer API)."
        )
    return s


@pytest.mark.asyncio
async def test_gemini_extracts_known_receipt(live_settings):
    client = GeminiKIEClient(live_settings)
    jpg = to_canonical_jpg(_SAMPLE_IMAGE.read_bytes())
    result = await client.extract_from_image(jpg)
    # Don't over-assert — Gemini's exact output may vary across model
    # versions. Anchor on what is unambiguous in the receipt.
    assert "ALFAMIDI" in (result.get("info", {}).get("store_name") or "").upper()
    assert isinstance(result.get("items"), list)
    assert isinstance(result.get("payment"), dict)
