"""Direct integration test for GeminiKIEClient.

Bypasses IngestService / orchestrator and calls the KIE client directly
so we can inspect the raw Gemini response structure when debugging.

Run:
    uv run pytest tests/test_gemini_kie_direct.py -v -s

Requirements:
    Vertex AI mode:   GOOGLE_GENAI_USE_VERTEXAI=True + GOOGLE_CLOUD_PROJECT +
                      GOOGLE_APPLICATION_CREDENTIALS
    Developer API:    GOOGLE_GENAI_USE_VERTEXAI=False + LLM_API_KEY
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from klaudia_api.adapters.documents.kie.gemini import GeminiKIEClient
from klaudia_api.adapters.documents.media.normalizer import to_canonical_jpg
from klaudia_api.composition.settings import Settings

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[3]

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s - %(message)s")

# ── Sample image ─────────────────────────────────────────────────────────────
SAMPLE_DIR = PROJECT_ROOT / "sample-data" / "receipt"
SAMPLE_IMAGES = sorted(SAMPLE_DIR.glob("*.jpg"))


def _skip_if_no_creds() -> None:
    settings = Settings()
    if settings.google_genai_use_vertexai:
        if not settings.google_cloud_project:
            pytest.skip("GOOGLE_CLOUD_PROJECT not set (Vertex AI mode)")
        creds = settings.google_application_credentials or os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS", ""
        )
        if not creds:
            pytest.skip("GOOGLE_APPLICATION_CREDENTIALS not set (Vertex AI mode)")
    else:
        if not settings.llm_api_key:
            pytest.skip("LLM_API_KEY not set (Developer API mode)")


# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kie_extract_sample_receipt():
    """Extract a real receipt image and assert non-empty store_name / total."""
    _skip_if_no_creds()

    if not SAMPLE_IMAGES:
        pytest.skip(f"No .jpg files in {SAMPLE_DIR}")

    settings = Settings()
    client = GeminiKIEClient(settings, langfuse=None)

    img_path = SAMPLE_IMAGES[0]
    jpg_bytes = to_canonical_jpg(img_path.read_bytes())

    print(f"\n▶ Testing with: {img_path.name} ({len(jpg_bytes):,} bytes)")
    print(f"  mode={'vertexai' if settings.google_genai_use_vertexai else 'devapi'}")
    print(f"  model={settings.kie_model}")

    result = await client.extract_from_image(jpg_bytes)

    print("\n── Extraction result ────────────────────────────────────────")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("────────────────────────────────────────────────────────────")

    info = result.get("info", {})
    assert info.get("store_name"), f"store_name is empty — result:\n{result}"
    grand_total = result.get("payment", {}).get("grand_total", "")
    assert grand_total, f"payment.grand_total is empty — result:\n{result}"


@pytest.mark.asyncio
async def test_kie_raw_response_structure():
    """Inspect the raw google-genai response object directly.

    This test does NOT go through GeminiKIEClient; it calls generate_content
    directly so we can print the full response structure when debugging empty
    response issues (thinking model token exhaustion, etc.).
    """
    _skip_if_no_creds()

    if not SAMPLE_IMAGES:
        pytest.skip(f"No .jpg files in {SAMPLE_DIR}")

    from google import genai
    from google.genai import types

    settings = Settings()

    if settings.google_genai_use_vertexai:
        gc = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location or "global",
        )
    else:
        gc = genai.Client(api_key=settings.llm_api_key)

    img_path = SAMPLE_IMAGES[0]
    jpg_bytes = to_canonical_jpg(img_path.read_bytes())

    # ── Call WITHOUT thinking_config so we can see the raw failure ──────────
    config_no_think = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.1,
        max_output_tokens=512,  # small — fast timeout for debugging
    )

    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_bytes(data=jpg_bytes, mime_type="image/jpeg"),
                types.Part(text="Extract store name and total. Output JSON only."),
            ],
        )
    ]

    print(f"\n▶ Raw call (no thinking_config) model={settings.kie_model}")
    response = await gc.aio.models.generate_content(
        model=settings.kie_model,
        contents=contents,
        config=config_no_think,
    )

    print(f"  response.text = {response.text!r}")
    print(f"  candidates    = {len(response.candidates) if response.candidates else 0}")
    if response.candidates:
        c = response.candidates[0]
        print(f"  finish_reason = {c.finish_reason}")
        if c.content and c.content.parts:
            for i, p in enumerate(c.content.parts):
                thought = getattr(p, "thought", False)
                text_preview = repr(p.text[:80]) if p.text else "None"
                print(f"  parts[{i}] thought={thought} text={text_preview}")

    # ── Call WITH thinking_config disabled ───────────────────────────────────
    config_no_think2 = types.GenerateContentConfig(
        response_mime_type="application/json",
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        temperature=0.1,
        max_output_tokens=512,
    )

    print(f"\n▶ Raw call (thinking_budget=0) model={settings.kie_model}")
    response2 = await gc.aio.models.generate_content(
        model=settings.kie_model,
        contents=contents,
        config=config_no_think2,
    )

    print(f"  response.text = {response2.text!r}")
    print(
        f"  candidates    = {len(response2.candidates) if response2.candidates else 0}"
    )
    if response2.candidates:
        c2 = response2.candidates[0]
        print(f"  finish_reason = {c2.finish_reason}")
        if c2.content and c2.content.parts:
            for i, p in enumerate(c2.content.parts):
                thought = getattr(p, "thought", False)
                text_preview = repr(p.text[:80]) if p.text else "None"
                print(f"  parts[{i}] thought={thought} text={text_preview}")

    # At minimum the no-think call should produce a non-empty response
    fr = response2.candidates[0].finish_reason if response2.candidates else "N/A"
    assert response2.text, (
        f"Even with thinking_budget=0 the response is empty. finish_reason={fr}"
    )
