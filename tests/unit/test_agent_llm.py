import pytest
from langchain_core.tools import tool

from klaudia.core.agent.llm import build_chat_llm


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
