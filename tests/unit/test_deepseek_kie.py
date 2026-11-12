"""Unit tests for the DeepSeek vision KIE backend."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from klaudia_api.exceptions import OCRError
from klaudia_api.adapters.documents.extraction.prompt import build_extraction_prompt
from klaudia_api.adapters.documents.kie.deepseek import DeepSeekKIEClient
from klaudia_api.adapters.documents.kie.client import KIEClient
from klaudia_api.composition.settings import Settings


MODEL = "deepseek-flash"
IMAGE_BYTES = b"jpeg-image"


def _settings(**overrides: object) -> Settings:
    """Build isolated DeepSeek KIE settings."""
    values = {
        "MOCK_KIE": False,
        "KIE_MODEL": MODEL,
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
        "DEEPSEEK_API_KEY": "test-key",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _completion(content: str) -> SimpleNamespace:
    """Build a minimal chat completion response."""
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_deepseek_kie_requires_api_key() -> None:
    """Reject live DeepSeek KIE setup without a secret."""
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        DeepSeekKIEClient(_settings(DEEPSEEK_API_KEY=""))


@pytest.mark.asyncio
async def test_deepseek_kie_sends_prompt_and_image_in_supported_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Send the shared prompt and inline JPEG through Chat Completions."""
    create = AsyncMock(return_value=_completion('{"info": {"store_name": "CAFE"}}'))
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        "klaudia_api.adapters.documents.kie.deepseek.AsyncOpenAI",
        lambda **_kwargs: fake_client,
    )
    client = DeepSeekKIEClient(_settings())

    await client.extract_from_image(IMAGE_BYTES)

    image_b64 = base64.b64encode(IMAGE_BYTES).decode("ascii")
    assert create.await_args.kwargs == {
        "model": MODEL,
        "messages": [
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
                            "url": f"data:image/jpeg;base64,{image_b64}",
                            "detail": "original",
                        },
                    },
                ],
            },
        ],
        "temperature": 0.0,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
        "extra_body": {"thinking": {"type": "disabled"}},
    }


@pytest.mark.asyncio
async def test_deepseek_kie_rejects_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a response without JSON content."""
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_completion("")),
            )
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        "klaudia_api.adapters.documents.kie.deepseek.AsyncOpenAI",
        lambda **_kwargs: fake_client,
    )
    client = DeepSeekKIEClient(_settings())

    with pytest.raises(OCRError, match="empty response"):
        await client.extract_from_image(IMAGE_BYTES)


@pytest.mark.asyncio
async def test_deepseek_kie_wraps_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose provider failures as extraction errors."""
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(side_effect=RuntimeError("offline")),
            )
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        "klaudia_api.adapters.documents.kie.deepseek.AsyncOpenAI",
        lambda **_kwargs: fake_client,
    )
    client = DeepSeekKIEClient(_settings())

    with pytest.raises(OCRError, match="DeepSeek KIE failed: offline"):
        await client.extract_from_image(IMAGE_BYTES)


@pytest.mark.asyncio
async def test_deepseek_kie_closes_api_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close the DeepSeek connection pool during shutdown."""
    close = AsyncMock()
    fake_client = SimpleNamespace(close=close)
    monkeypatch.setattr(
        "klaudia_api.adapters.documents.kie.deepseek.AsyncOpenAI",
        lambda **_kwargs: fake_client,
    )
    client = DeepSeekKIEClient(_settings())

    await client.shutdown()

    close.assert_awaited_once_with()


def test_kie_client_routes_deepseek_vision_model() -> None:
    """Select the DeepSeek backend from its model name."""
    client = KIEClient(_settings())

    assert client.mode == "deepseek"


@pytest.mark.asyncio
async def test_kie_client_calls_deepseek_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delegate extraction to the DeepSeek client."""
    extraction = {"info": {"store_name": "CAFE"}}
    backend = SimpleNamespace(
        extract_from_image=AsyncMock(return_value=extraction),
        shutdown=AsyncMock(),
    )
    monkeypatch.setattr(
        "klaudia_api.adapters.documents.kie.client.DeepSeekKIEClient",
        lambda *_args: backend,
    )
    client = KIEClient(_settings())

    extracted = await client.extract_from_image(IMAGE_BYTES)

    assert extracted == extraction
