"""Build chat models for the configured agent provider."""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

# Provider buckets. "openai" is kept as a back-compat alias for "vllm".
_GEMINI_PROVIDERS = frozenset({"google", "gemini", "vertexai"})
_VLLM_PROVIDERS = frozenset({"vllm", "qwen", "openai"})
_DEEPSEEK_PROVIDERS = frozenset({"deepseek"})
_OPENAI_COMPATIBLE = _VLLM_PROVIDERS | _DEEPSEEK_PROVIDERS

# Gemini 3 thinking levels accepted by ChatGoogleGenerativeAI.
_VALID_THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high"})

# OpenAI client rejects an empty api_key; a self-hosted vLLM started without
# --api-key ignores the value, so this placeholder is safe there.
_VLLM_PLACEHOLDER_KEY = "EMPTY"


def _openai_thinking_extra_body(
    provider: str, disable_thinking: bool
) -> Optional[dict[str, Any]]:
    """Return the provider-specific extra_body to turn reasoning off.

    Each OpenAI-compatible server toggles thinking differently:
        deepseek → {"thinking": {"type": "disabled"}}
        vllm/qwen → {"chat_template_kwargs": {"enable_thinking": False}}  (/no_think)
    None means "leave the server default untouched".
    """
    if not disable_thinking:
        return None
    if provider in _DEEPSEEK_PROVIDERS:
        return {"thinking": {"type": "disabled"}}
    if provider in _VLLM_PROVIDERS:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return None


def build_chat_llm(
    *,
    model: str,
    provider: str = "google",
    temperature: float = 0.5,
    use_vertexai: bool = False,
    llm_api_key: Optional[str] = None,
    google_cloud_project: Optional[str] = None,
    google_cloud_location: Optional[str] = "global",
    openai_base_url: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    thinking_level: Optional[str] = None,
    disable_thinking: bool = True,
) -> BaseChatModel:
    """Build a chat model for the configured provider.

    Args:
        provider: "google" (Gemini), "vllm" (Qwen on vLLM), or "deepseek".
        thinking_level: Gemini-only thinking budget. None = model default.
        disable_thinking: OpenAI-compatible only. Force thinking off via extra_body.
    """
    normalized = (provider or "google").strip().lower()
    if normalized in _OPENAI_COMPATIBLE:
        return _build_openai_llm(
            model=model,
            provider=normalized,
            temperature=temperature,
            base_url=openai_base_url,
            api_key=openai_api_key,
            disable_thinking=disable_thinking,
        )
    if normalized in _GEMINI_PROVIDERS:
        return _build_gemini_llm(
            model=model,
            temperature=temperature,
            use_vertexai=use_vertexai,
            llm_api_key=llm_api_key,
            google_cloud_project=google_cloud_project,
            google_cloud_location=google_cloud_location,
            thinking_level=thinking_level,
        )
    raise ValueError(
        f"Unknown MODEL_PROVIDER={provider!r}. "
        "Valid values: 'google', 'vllm', 'deepseek'."
    )


def _build_openai_llm(
    *,
    model: str,
    provider: str,
    temperature: float,
    base_url: Optional[str],
    api_key: Optional[str],
    disable_thinking: bool,
) -> BaseChatModel:
    """Build a ChatOpenAI bound to an OpenAI-compatible endpoint."""
    from langchain_openai import ChatOpenAI

    if not base_url:
        raise ValueError(
            f"MODEL_PROVIDER={provider} requires a base URL "
            "(DEEPSEEK_BASE_URL or VLLM_LLM_ENDPOINT)."
        )

    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key or _VLLM_PLACEHOLDER_KEY,
        temperature=temperature,
        extra_body=_openai_thinking_extra_body(provider, disable_thinking),
    )


def _build_gemini_llm(
    *,
    model: str,
    temperature: float,
    use_vertexai: bool,
    llm_api_key: Optional[str],
    google_cloud_project: Optional[str],
    google_cloud_location: Optional[str],
    thinking_level: Optional[str],
) -> BaseChatModel:
    """Build ChatGoogleGenerativeAI for Vertex AI or the Gemini Developer API."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    normalized_thinking_level = None
    if thinking_level is not None:
        normalized_thinking_level = thinking_level.lower()
        if normalized_thinking_level not in _VALID_THINKING_LEVELS:
            raise ValueError(
                f"Invalid thinking_level={thinking_level!r}. "
                f"Valid values: {sorted(_VALID_THINKING_LEVELS)}"
            )

    if use_vertexai:
        if not google_cloud_project:
            raise ValueError("use_vertexai=True but google_cloud_project is empty")
        return ChatGoogleGenerativeAI(
            model=model,
            vertexai=True,
            project=google_cloud_project,
            location=google_cloud_location or "global",
            temperature=temperature,
            thinking_level=normalized_thinking_level,
        )

    if not llm_api_key:
        raise ValueError("llm_api_key is required when use_vertexai=False")
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=llm_api_key,
        temperature=temperature,
        thinking_level=normalized_thinking_level,
    )
