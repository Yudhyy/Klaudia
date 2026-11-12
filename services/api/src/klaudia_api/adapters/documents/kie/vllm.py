"""Fine-tuned Qwen3.5-4B receipt extraction client for vLLM."""

from __future__ import annotations

import base64
import logging
from contextlib import contextmanager
from typing import Any

import httpx

from klaudia_api.exceptions import OCRError
from klaudia_api.adapters.observability.langfuse import LangfuseService
from klaudia_api.adapters.documents.extraction.parser import parse_extraction_json
from klaudia_api.composition.settings import Settings

logger = logging.getLogger(__name__)


class VLLMKIEClient:
    """Receipt image -> JSON via a fine-tuned model served on vLLM."""

    def __init__(
        self,
        settings: Settings,
        langfuse: LangfuseService | None = None,
    ) -> None:
        self._base_url = settings.vllm_kie_endpoint
        self._api_key = settings.vllm_kie_api_key
        self._model = settings.kie_model
        self._client = httpx.AsyncClient(
            timeout=float(settings.extraction_page_timeout_seconds)
        )
        self._langfuse = langfuse

    @property
    def model_id(self) -> str:
        return self._model

    async def extract_from_image(self, jpg_bytes: bytes) -> dict[str, Any]:
        if not self._base_url:
            raise OCRError(
                f"VLLM_KIE_ENDPOINT is not configured but KIE_MODEL={self._model!r} "
                "routes to vLLM. Set the endpoint or point KIE_MODEL at a Gemini model."
            )

        image_b64 = base64.b64encode(jpg_bytes).decode("utf-8")
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                        }
                    ],
                }
            ],
            "temperature": 0.0,
            "max_output_tokens": 8192,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        span_cm = (
            self._langfuse.span(
                "vllm.kie.image",
                as_type="generation",
                metadata={"model": self._model, "image_bytes": len(jpg_bytes)},
            )
            if self._langfuse is not None
            else _nullspan()
        )

        with span_cm as obs:
            try:
                resp = await self._client.post(
                    self._base_url, json=payload, headers=headers
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"] or ""
            except Exception as e:
                logger.error("vLLM KIE call failed: %s", e)
                _obs_error(obs, str(e))
                raise OCRError(f"vLLM KIE failed: {e}") from e

            if not raw.strip():
                _obs_error(obs, "empty response")
                raise OCRError("vLLM KIE returned an empty response")

            try:
                parsed = parse_extraction_json(raw)
            except Exception as e:
                logger.error("vLLM KIE JSON parse failed: %s", e)
                _obs_error(obs, str(e))
                raise OCRError(f"vLLM KIE JSON unparseable: {e}") from e

            if obs is not None:
                try:
                    obs.update(output=parsed, model=self._model)
                except Exception:
                    pass
            return parsed

    async def shutdown(self) -> None:
        await self._client.aclose()


def _obs_error(obs: Any, message: str) -> None:
    if obs is not None:
        try:
            obs.update(level="ERROR", status_message=message)
        except Exception:
            pass


@contextmanager
def _nullspan():
    yield None
