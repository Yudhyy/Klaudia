import logging
from contextlib import contextmanager
from typing import Any, Optional

from google import genai
from google.genai import types

from klaudia_api.exceptions import LLMError
from klaudia_api.adapters.observability.langfuse import LangfuseService
from klaudia_api.composition.settings import Settings

logger = logging.getLogger(__name__)


@contextmanager
def _nullspan():
    yield None


class LLMClient:
    """Async client for Google Gemini via the google-genai SDK."""

    def __init__(
        self, settings: Settings, langfuse: Optional[LangfuseService] = None
    ) -> None:
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
            logger.info(
                "LLMClient: Vertex AI mode (project=%s, location=%s)",
                settings.google_cloud_project,
                settings.google_cloud_location or "global",
            )
        else:
            if not settings.llm_api_key:
                raise ValueError(
                    "LLM_API_KEY is required when GOOGLE_GENAI_USE_VERTEXAI=False"
                )
            self._client = genai.Client(api_key=settings.llm_api_key)
            logger.info("LLMClient: Gemini Developer API mode")
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._langfuse = langfuse

    # Thinking models (gemini-3-*) use output tokens for internal reasoning.
    # A low max_output_tokens budget gets exhausted by thinking, returning empty.
    MIN_OUTPUT_TOKENS = 256

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 4096,
        span_name: str = "gemini.generate_content",
    ) -> str:
        """Send a chat request and return the assistant message content."""
        target_model = model or self._model
        temp = temperature if temperature is not None else self._temperature
        effective_tokens = max(max_tokens, self.MIN_OUTPUT_TOKENS)

        contents = _to_genai_contents(messages)

        langfuse = self._langfuse
        span_cm = (
            langfuse.span(
                span_name,
                as_type="generation",
                input=messages,
                metadata={
                    "model": target_model,
                    "temperature": temp,
                    "max_output_tokens": effective_tokens,
                },
            )
            if langfuse is not None
            else _nullspan()
        )

        try:
            with span_cm as obs:
                try:
                    response = await self._client.aio.models.generate_content(
                        model=target_model,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            temperature=temp,
                            max_output_tokens=effective_tokens,
                            thinking_config=types.ThinkingConfig(
                                thinking_level="minimal"
                            ),
                        ),
                    )
                except Exception as e:
                    logger.error(f"LLM error: {e}")
                    if obs is not None:
                        try:
                            obs.update(level="ERROR", status_message=str(e))
                        except Exception:
                            pass
                    raise LLMError(str(e)) from e

                text = response.text or ""
                if obs is not None:
                    try:
                        obs.update(
                            output=text,
                            model=target_model,
                            usage_details=_extract_usage(response),
                        )
                    except Exception:
                        pass
                return text
        except LLMError:
            raise

    async def shutdown(self) -> None:
        pass


def _extract_usage(response: Any) -> dict[str, int]:
    """Best-effort extraction of token usage from google-genai response."""
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return {}
    out: dict[str, int] = {}
    for attr, key in (
        ("prompt_token_count", "input"),
        ("candidates_token_count", "output"),
        ("total_token_count", "total"),
    ):
        val = getattr(usage, attr, None)
        if isinstance(val, int):
            out[key] = val
    return out


def _to_genai_contents(
    messages: list[dict[str, Any]],
) -> list[types.Content]:
    """Convert OpenAI-style messages to google-genai Content objects."""
    contents: list[types.Content] = []
    system_parts: list[str] = []

    for msg in messages:
        role = msg["role"]
        text = msg.get("content", "")

        if role == "system":
            system_parts.append(text)
            continue

        genai_role = "model" if role == "assistant" else "user"

        if system_parts:
            text = "\n".join(system_parts) + "\n\n" + text
            system_parts.clear()

        contents.append(
            types.Content(
                role=genai_role,
                parts=[types.Part(text=text)],
            )
        )

    if system_parts and not contents:
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part(text="\n".join(system_parts))],
            )
        )

    return contents
