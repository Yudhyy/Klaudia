"""DeepSeek vision receipt extraction client."""

from __future__ import annotations

import base64
import logging
from contextlib import nullcontext
from typing import Any

from openai import AsyncOpenAI

from klaudia_api.exceptions import OCRError
from klaudia_api.adapters.observability.langfuse import LangfuseService
from klaudia_api.adapters.documents.extraction.parser import parse_extraction_json
from klaudia_api.adapters.documents.extraction.prompt import build_extraction_prompt
from klaudia_api.composition.settings import Settings

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 8192


class DeepSeekKIEClient:
    """Return receipt JSON from DeepSeek vision input."""

    def __init__(
        self,
        settings: Settings,
        langfuse: LangfuseService | None = None,
    ) -> None:
        if not settings.deepseek_base_url:
            raise ValueError("DeepSeek KIE requires DEEPSEEK_BASE_URL")
        if not settings.deepseek_api_key:
            raise ValueError("DeepSeek KIE requires DEEPSEEK_API_KEY")

        self._model = settings.kie_model
        self._langfuse = langfuse
        self._client = AsyncOpenAI(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
            timeout=float(settings.extraction_page_timeout_seconds),
        )

    @property
    def model_id(self) -> str:
        return self._model

    async def extract_from_image(self, jpg_bytes: bytes) -> dict[str, Any]:
        image_base64 = base64.b64encode(jpg_bytes).decode("ascii")
        messages = [
            {"role": "system", "content": build_extraction_prompt()},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Extract this receipt into the JSON schema. Follow every "
                            "rule above. Output only the JSON object."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}",
                            "detail": "original",
                        },
                    },
                ],
            },
        ]
        span = (
            self._langfuse.span(
                "deepseek.kie.image",
                as_type="generation",
                metadata={"model": self._model, "image_bytes": len(jpg_bytes)},
            )
            if self._langfuse is not None
            else nullcontext(None)
        )

        with span as observation:
            try:
                completion = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=0.0,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    response_format={"type": "json_object"},
                    extra_body={"thinking": {"type": "disabled"}},
                )
                raw_response = completion.choices[0].message.content or ""
            except Exception as exc:
                logger.error("DeepSeek KIE call failed: %s", exc)
                _record_error(observation, str(exc))
                raise OCRError(f"DeepSeek KIE failed: {exc}") from exc

            if not raw_response.strip():
                _record_error(observation, "empty response")
                raise OCRError("DeepSeek KIE returned an empty response")

            try:
                extraction = parse_extraction_json(raw_response)
            except Exception as exc:
                logger.error("DeepSeek KIE JSON parse failed: %s", exc)
                _record_error(observation, str(exc))
                raise OCRError(f"DeepSeek KIE JSON unparseable: {exc}") from exc

            if observation is not None:
                try:
                    observation.update(output=extraction, model=self._model)
                except Exception:
                    pass
            return extraction

    async def shutdown(self) -> None:
        await self._client.close()


def _record_error(observation: Any, message: str) -> None:
    if observation is None:
        return
    try:
        observation.update(level="ERROR", status_message=message)
    except Exception:
        pass
