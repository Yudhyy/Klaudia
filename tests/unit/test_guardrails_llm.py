"""Unit tests for the guardrails LLM provider router.

Covers backend selection (google vs deepseek), the DeepSeek thinking-disable
wire format, credential validation, and lifecycle ownership.
"""

from types import SimpleNamespace

import pytest

from klaudia_api.exceptions import LLMError
from klaudia_api.adapters.guardrails.config import GuardrailsConfig
from klaudia_api.adapters.guardrails.llm import (
    DeepSeekGuardrailLLM,
    GuardrailsLLMRouter,
)


class _RecordingGeminiClient:
    """Stand-in for the shared google-genai LLMClient."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.closed = False

    async def chat(
        self, messages, model=None, temperature=None, max_tokens=4096, span_name="x"
    ):
        self.calls.append(
            {"messages": messages, "model": model, "temperature": temperature}
        )
        return "YES"

    async def shutdown(self) -> None:
        self.closed = True


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        message = SimpleNamespace(content=self._content)
        choice = SimpleNamespace(message=message)
        usage = SimpleNamespace(prompt_tokens=5, completion_tokens=1, total_tokens=6)
        return SimpleNamespace(choices=[choice], usage=usage)


class _FakeAsyncOpenAI:
    """Captures constructor args and stands in for openai.AsyncOpenAI."""

    instances: list["_FakeAsyncOpenAI"] = []

    def __init__(self, base_url=None, api_key=None) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.closed = False
        self._completions = _FakeCompletions("YES")
        self.chat = SimpleNamespace(completions=self._completions)
        _FakeAsyncOpenAI.instances.append(self)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _patch_openai(monkeypatch):
    _FakeAsyncOpenAI.instances = []
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    yield


def _cfg(**over) -> GuardrailsConfig:
    base = dict(groq_api_key="x")
    base.update(over)
    return GuardrailsConfig(**base)


# --- backend selection -------------------------------------------------------


async def test_google_provider_delegates_to_gemini_client():
    gemini = _RecordingGeminiClient()
    router = GuardrailsLLMRouter(gemini, _cfg(guardrails_provider="google"))

    result = await router.chat(
        messages=[{"role": "user", "content": "hi"}],
        model="gemini-3.1-flash-lite",
        temperature=0.0,
        max_tokens=10,
    )

    assert result == "YES"
    assert len(gemini.calls) == 1
    assert gemini.calls[0]["model"] == "gemini-3.1-flash-lite"


async def test_unknown_provider_falls_back_to_gemini():
    gemini = _RecordingGeminiClient()
    router = GuardrailsLLMRouter(gemini, _cfg(guardrails_provider="mystery"))
    await router.chat(messages=[{"role": "user", "content": "hi"}], model="m")
    assert len(gemini.calls) == 1


# --- deepseek wire format ----------------------------------------------------


async def test_deepseek_disables_thinking_via_extra_body():
    router = GuardrailsLLMRouter(
        None,
        _cfg(
            guardrails_provider="deepseek",
            deepseek_base_url="https://api.deepseek.com/v1",
            deepseek_api_key="sk-test",
            disable_thinking=True,
        ),
    )

    result = await router.chat(
        messages=[{"role": "user", "content": "hi"}],
        model="deepseek-flash",
        temperature=0.0,
        max_tokens=10,
    )

    assert result == "YES"
    client = _FakeAsyncOpenAI.instances[0]
    assert client.base_url == "https://api.deepseek.com/v1"
    kwargs = client._completions.last_kwargs
    assert kwargs["model"] == "deepseek-flash"
    assert kwargs["temperature"] == 0.0
    assert kwargs["max_tokens"] == 10
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


async def test_deepseek_leaves_thinking_untouched_when_not_disabled():
    llm = DeepSeekGuardrailLLM(
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
        disable_thinking=False,
    )
    await llm.chat(messages=[{"role": "user", "content": "hi"}], model="deepseek-flash")
    kwargs = _FakeAsyncOpenAI.instances[0]._completions.last_kwargs
    assert kwargs["extra_body"] is None


async def test_deepseek_reuses_single_client_across_calls():
    llm = DeepSeekGuardrailLLM("https://api.deepseek.com/v1", "sk-test")
    await llm.chat(messages=[{"role": "user", "content": "a"}], model="deepseek-flash")
    await llm.chat(messages=[{"role": "user", "content": "b"}], model="deepseek-flash")
    assert len(_FakeAsyncOpenAI.instances) == 1


# --- validation & error handling --------------------------------------------


def test_deepseek_requires_base_url():
    with pytest.raises(ValueError, match="DEEPSEEK_BASE_URL"):
        DeepSeekGuardrailLLM(base_url="", api_key="sk-test")


def test_deepseek_requires_api_key():
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        DeepSeekGuardrailLLM(base_url="https://api.deepseek.com/v1", api_key="")


async def test_deepseek_requires_explicit_model():
    llm = DeepSeekGuardrailLLM("https://api.deepseek.com/v1", "sk-test")
    with pytest.raises(ValueError, match="explicit model"):
        await llm.chat(messages=[{"role": "user", "content": "hi"}], model=None)


async def test_deepseek_wraps_api_errors_in_llmerror(monkeypatch):
    llm = DeepSeekGuardrailLLM("https://api.deepseek.com/v1", "sk-test")

    async def _boom(**_kwargs):
        raise RuntimeError("upstream 500")

    # Force the client to exist, then break create().
    llm._get_client()._completions.create = _boom
    with pytest.raises(LLMError):
        await llm.chat(
            messages=[{"role": "user", "content": "hi"}], model="deepseek-flash"
        )


# --- lifecycle ownership -----------------------------------------------------


async def test_router_shutdown_closes_deepseek_client():
    router = GuardrailsLLMRouter(
        None,
        _cfg(
            guardrails_provider="deepseek",
            deepseek_base_url="https://api.deepseek.com/v1",
            deepseek_api_key="sk-test",
        ),
    )
    await router.chat(
        messages=[{"role": "user", "content": "hi"}], model="deepseek-flash"
    )
    await router.shutdown()
    assert _FakeAsyncOpenAI.instances[0].closed is True


async def test_router_shutdown_does_not_close_shared_gemini_client():
    gemini = _RecordingGeminiClient()
    router = GuardrailsLLMRouter(gemini, _cfg(guardrails_provider="google"))
    await router.shutdown()
    assert gemini.closed is False
