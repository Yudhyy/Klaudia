"""Gemini receipt extraction client."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

from google import genai
from google.genai import types

from klaudia_api.exceptions import LLMError
from klaudia_api.adapters.observability.langfuse import LangfuseService
from klaudia_api.adapters.documents.extraction.parser import parse_extraction_json
from klaudia_api.adapters.documents.extraction.prompt import build_extraction_prompt
from klaudia_api.composition.settings import Settings

logger = logging.getLogger(__name__)


class GeminiKIEClient:
    """Return receipt JSON from Gemini vision input."""

    def __init__(
        self,
        settings: Settings,
        langfuse: LangfuseService | None = None,
    ) -> None:
        self._model = settings.kie_model
        self._langfuse = langfuse
        if settings.google_genai_use_vertexai:
            if not settings.google_cloud_project:
                raise ValueError(
                    "GOOGLE_GENAI_USE_VERTEXAI=True but GOOGLE_CLOUD_PROJECT is empty"
                )
            self._client = genai.Client(
                vertexai=True,
                project=settings.google_cloud_project,
                location=settings.google_cloud_location or "global",
            )
            logger.info("GeminiKIEClient: Vertex AI mode model=%s", self._model)
        else:
            if not settings.llm_api_key:
                raise ValueError(
                    "LLM_API_KEY required when GOOGLE_GENAI_USE_VERTEXAI=False"
                )
            self._client = genai.Client(api_key=settings.llm_api_key)
            logger.info("GeminiKIEClient: Developer API mode model=%s", self._model)

        self._system_prompt = build_extraction_prompt()

    @property
    def model_id(self) -> str:
        return self._model

    async def extract_from_image(self, jpg_bytes: bytes) -> dict[str, Any]:
        """Multimodal call: Gemini reads the receipt image directly."""
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=jpg_bytes, mime_type="image/jpeg"),
                    types.Part(
                        text="Extract this receipt into the JSON schema. "
                        "Follow every rule above. Output only the JSON object."
                    ),
                ],
            )
        ]
        return await self._generate(contents, span_name="gemini.kie.image")

    async def _generate(
        self,
        contents: list[types.Content],
        *,
        span_name: str,
    ) -> dict[str, Any]:
        config = types.GenerateContentConfig(
            system_instruction=self._system_prompt,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.MINIMAL
            ),
            temperature=0.1,
            top_p=0.95,
            max_output_tokens=8192,
        )

        span_cm = (
            self._langfuse.span(
                span_name,
                as_type="generation",
                metadata={"model": self._model},
            )
            if self._langfuse is not None
            else _nullspan()
        )

        with span_cm as obs:
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=config,
                )
            except Exception as e:
                logger.error("Gemini KIE call failed: %s", e)
                if obs is not None:
                    try:
                        obs.update(level="ERROR", status_message=str(e))
                    except Exception:
                        pass
                raise LLMError(f"Gemini KIE failed: {e}") from e

            raw = response.text or ""
            if not raw and response.candidates:
                # Fallback: aggregate non-thought text parts directly.
                # On thinking models response.text can be None when the SDK
                # doesn't find a plain text part in the first candidate.
                candidate = response.candidates[0]
                if candidate.content and candidate.content.parts:
                    parts_text = [
                        p.text
                        for p in candidate.content.parts
                        if p.text and not getattr(p, "thought", False)
                    ]
                    raw = "".join(parts_text)
                    if raw:
                        logger.debug(
                            "Gemini KIE: response.text was empty, "
                            "recovered from %d part(s)",
                            len(parts_text),
                        )

            if not raw:
                finish_reason = (
                    response.candidates[0].finish_reason
                    if response.candidates
                    else "NO_CANDIDATES"
                )
                parts_count = (
                    len(response.candidates[0].content.parts)
                    if response.candidates
                    and response.candidates[0].content
                    and response.candidates[0].content.parts
                    else 0
                )
                logger.error(
                    "Gemini KIE empty response "
                    "(model=%s finish_reason=%s candidates=%d parts=%d)",
                    self._model,
                    finish_reason,
                    len(response.candidates) if response.candidates else 0,
                    parts_count,
                )
                if obs is not None:
                    try:
                        obs.update(
                            level="ERROR",
                            status_message=(
                                f"empty response finish_reason={finish_reason}"
                            ),
                        )
                    except Exception:
                        pass
                raise LLMError(
                    f"Gemini KIE returned empty response "
                    f"(finish_reason={finish_reason})"
                )
            try:
                parsed = parse_extraction_json(raw)
            except Exception as e:
                logger.error("Gemini KIE JSON parse failed: %s", e)
                if obs is not None:
                    try:
                        obs.update(level="ERROR", status_message=str(e))
                    except Exception:
                        pass
                raise LLMError(f"Gemini KIE JSON unparseable: {e}") from e

            if obs is not None:
                try:
                    obs.update(output=parsed, model=self._model)
                except Exception:
                    pass
            return parsed


@contextmanager
def _nullspan():
    yield None
