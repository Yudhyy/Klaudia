"""Route scope and output guardrail checks by model provider."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Optional, Protocol

from klaudia_api.exceptions import LLMError
from klaudia_api.adapters.observability.langfuse import LangfuseService
from klaudia_api.adapters.guardrails.config import GuardrailsConfig

logger = logging.getLogger(__name__)

_DEEPSEEK_PROVIDERS = frozenset({"deepseek"})


@contextmanager
def _nullspan():
    yield None


class GuardrailChatLLM(Protocol):
    """Minimal chat surface the scope/output checks depend on.

    Matches LLMClient.chat so the Gemini client satisfies it directly.
    """

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 4096,
        span_name: str = "guardrail.generate",
    ) -> str: ...


class DeepSeekGuardrailLLM:
    """OpenAI-compatible chat for DeepSeek, with reasoning forced off.

    The AsyncOpenAI client is created lazily on first use and reused across
    calls (the two parallel scope checks + output check share one connection
    pool). Closed via shutdown().
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        disable_thinking: bool = True,
        langfuse: Optional[LangfuseService] = None,
    ) -> None:
        if not base_url:
            raise ValueError("GUARDRAILS_PROVIDER=deepseek requires DEEPSEEK_BASE_URL")
        if not api_key:
            raise ValueError("GUARDRAILS_PROVIDER=deepseek requires DEEPSEEK_API_KEY")
        self._base_url = base_url
        self._api_key = api_key
        self._disable_thinking = disable_thinking
        self._langfuse = langfuse
        self._client: Any = None  # lazy AsyncOpenAI

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(base_url=self._base_url, api_key=self._api_key)
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 4096,
        span_name: str = "guardrail.generate",
    ) -> str:
        if not model:
            raise ValueError("DeepSeek guardrail requires an explicit model name")

        temp = 0.0 if temperature is None else temperature
        # None leaves the server default untouched; disabled turns reasoning off.
        extra_body = (
            {"thinking": {"type": "disabled"}} if self._disable_thinking else None
        )

        span_cm = (
            self._langfuse.span(
                span_name,
                as_type="generation",
                input=messages,
                metadata={
                    "model": model,
                    "provider": "deepseek",
                    "temperature": temp,
                    "max_tokens": max_tokens,
                },
            )
            if self._langfuse is not None
            else _nullspan()
        )

        with span_cm as obs:
            client = self._get_client()
            try:
                completion = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temp,
                    max_tokens=max_tokens,
                    extra_body=extra_body,
                )
            except Exception as e:
                logger.error(f"DeepSeek guardrail error: {e}")
                if obs is not None:
                    try:
                        obs.update(level="ERROR", status_message=str(e))
                    except Exception:
                        pass
                raise LLMError(str(e)) from e

            text = (completion.choices[0].message.content or "").strip()
            if obs is not None:
                try:
                    obs.update(
                        output=text,
                        model=model,
                        usage_details=_extract_openai_usage(completion),
                    )
                except Exception:
                    pass
            return text

    async def shutdown(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None


class GuardrailsLLMRouter:
    """Routes guardrail chat calls to the configured backend.

    Owns the DeepSeek client's lifecycle; the Gemini client is shared and owned
    by the container, so shutdown() only closes what this router created.
    """

    def __init__(
        self,
        gemini_client: GuardrailChatLLM,
        config: GuardrailsConfig,
        langfuse: Optional[LangfuseService] = None,
    ) -> None:
        self._provider = (config.guardrails_provider or "google").strip().lower()
        if self._provider in _DEEPSEEK_PROVIDERS:
            self._backend: GuardrailChatLLM = DeepSeekGuardrailLLM(
                base_url=config.deepseek_base_url,
                api_key=config.deepseek_api_key,
                disable_thinking=config.disable_thinking,
                langfuse=langfuse,
            )
            logger.info("Guardrails LLM backend: deepseek")
        else:
            self._backend = gemini_client
            logger.info("Guardrails LLM backend: google (Gemini)")

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 4096,
        span_name: str = "guardrail.generate",
    ) -> str:
        return await self._backend.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            span_name=span_name,
        )

    async def shutdown(self) -> None:
        # Only close the DeepSeek client we own; the shared Gemini client is
        # shut down by the container.
        if self._provider in _DEEPSEEK_PROVIDERS:
            await self._backend.shutdown()  # type: ignore[attr-defined]


def _extract_openai_usage(completion: Any) -> dict[str, int]:
    """Best-effort token usage extraction from an OpenAI-compatible response."""
    usage = getattr(completion, "usage", None)
    if not usage:
        return {}
    out: dict[str, int] = {}
    for attr, key in (
        ("prompt_tokens", "input"),
        ("completion_tokens", "output"),
        ("total_tokens", "total"),
    ):
        val = getattr(usage, attr, None)
        if isinstance(val, int):
            out[key] = val
    return out
