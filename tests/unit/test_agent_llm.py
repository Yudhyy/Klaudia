import pytest
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from langchain_core.tools import tool

from app.adapters.llm.model_factory import build_chat_llm


@tool
def _echo(value: str) -> str:
    """Return the input value.

    Args:
        value: Text to return.

    Returns:
        The input text.
    """
    return value


@pytest.mark.parametrize(
    "provider_kwargs",
    [
        {"llm_api_key": "fake", "use_vertexai": False},
        {
            "google_cloud_project": "fake-project",
            "use_vertexai": True,
        },
    ],
)
def test_gemini_thinking_level_survives_tool_binding(provider_kwargs):
    """Keep the configured thinking level when an agent binds tools."""
    model = build_chat_llm(
        model="gemini-3-flash-preview",
        provider="google",
        thinking_level="minimal",
        **provider_kwargs,
    )

    tool_bound_model = model.bind_tools([_echo])

    assert tool_bound_model.bound.thinking_level == "minimal"


def test_gemini_rejects_unsupported_none_thinking_level():
    """Reject the undocumented Gemini `none` thinking level."""
    with pytest.raises(ValueError, match="Invalid thinking_level='none'"):
        build_chat_llm(
            model="gemini-3-flash-preview",
            provider="google",
            llm_api_key="fake",
            thinking_level="none",
        )


@pytest.mark.parametrize(
    "provider", ["vllm", "qwen", "openai", "deepseek", " DeepSeek "]
)
@pytest.mark.parametrize("disable_thinking", [True, False])
def test_openai_aliases_preserve_constructor_options(
    monkeypatch, provider, disable_thinking
):
    """Keep endpoint, credentials and provider thinking options after the move."""
    constructor = Mock()
    monkeypatch.setitem(
        sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=constructor)
    )
    model = build_chat_llm(
        model="configured-model",
        provider=provider,
        temperature=0.25,
        openai_base_url="http://127.0.0.1:9999/v1",
        openai_api_key="test-key",
        disable_thinking=disable_thinking,
    )
    extra_body = None
    if disable_thinking:
        extra_body = (
            {"thinking": {"type": "disabled"}}
            if provider.strip().lower() == "deepseek"
            else {"chat_template_kwargs": {"enable_thinking": False}}
        )
    constructor.assert_called_once_with(
        model="configured-model",
        base_url="http://127.0.0.1:9999/v1",
        api_key="test-key",
        temperature=0.25,
        extra_body=extra_body,
    )
    assert model is constructor.return_value


@pytest.mark.parametrize("provider", ["google", "gemini", "vertexai", " GOOGLE ", ""])
def test_google_aliases_preserve_developer_api_options(monkeypatch, provider):
    """Keep alias routing, key use and normalized thinking levels."""
    constructor = Mock()
    monkeypatch.setitem(
        sys.modules,
        "langchain_google_genai",
        SimpleNamespace(ChatGoogleGenerativeAI=constructor),
    )
    build_chat_llm(
        model="configured-model",
        provider=provider,
        llm_api_key="test-key",
        thinking_level="LOW",
        temperature=0.25,
    )
    constructor.assert_called_once_with(
        model="configured-model",
        google_api_key="test-key",
        temperature=0.25,
        thinking_level="low",
    )


def test_vertex_options_preserve_project_location_and_thinking(monkeypatch):
    """Keep project-based construction and the default global location."""
    constructor = Mock()
    monkeypatch.setitem(
        sys.modules,
        "langchain_google_genai",
        SimpleNamespace(ChatGoogleGenerativeAI=constructor),
    )
    build_chat_llm(
        model="configured-model",
        use_vertexai=True,
        google_cloud_project="test-project",
        google_cloud_location=None,
        thinking_level="HIGH",
    )
    constructor.assert_called_once_with(
        model="configured-model",
        vertexai=True,
        project="test-project",
        location="global",
        temperature=0.5,
        thinking_level="high",
    )


def test_self_hosted_endpoint_preserves_placeholder_key(monkeypatch):
    """Construct a self-hosted model when no server key is configured."""
    constructor = Mock()
    monkeypatch.setitem(
        sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=constructor)
    )
    build_chat_llm(
        model="configured-model",
        provider="vllm",
        openai_base_url="http://localhost:9999/v1",
    )
    assert constructor.call_args.kwargs["api_key"] == "EMPTY"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"provider": "unknown"}, "Unknown MODEL_PROVIDER"),
        ({"provider": "deepseek"}, "requires a base URL"),
        ({"provider": "vllm"}, "requires a base URL"),
        ({"provider": "google"}, "llm_api_key is required"),
        ({"provider": "google", "use_vertexai": True}, "google_cloud_project is empty"),
    ],
)
def test_factory_preserves_configuration_errors(options, message):
    """Keep construction failures visible to application startup."""
    with pytest.raises(ValueError, match=message):
        build_chat_llm(model="configured-model", **options)


def test_factory_preserves_provider_constructor_failures(monkeypatch):
    """Propagate provider failures without returning a substitute model."""
    failure = RuntimeError("Provider construction failed")
    constructor = Mock(side_effect=failure)
    monkeypatch.setitem(
        sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=constructor)
    )
    with pytest.raises(RuntimeError) as raised:
        build_chat_llm(
            model="configured-model",
            provider="deepseek",
            openai_base_url="http://localhost:9999/v1",
        )
    assert raised.value is failure
