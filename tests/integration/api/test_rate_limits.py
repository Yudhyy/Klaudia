"""Rate-limit integration tests with PostgreSQL and a fake orchestrator.

Limits are read from Settings at request time, so each test pins its own
values via env + get_settings.cache_clear().
"""

from types import SimpleNamespace
from typing import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

import config.settings as settings_mod
from app.models.chat import KlaudiaMessage, KlaudiaResponse
from app.routes import v1_router


class FakeOrchestrator:
    """Minimal stand-in so /v1/chat succeeds without the LLM stack."""

    async def process(
        self, messages, session_id, user_id, user_name, spreadsheet_id=None
    ):
        return KlaudiaResponse(
            message=KlaudiaMessage(role="assistant", content="ok"),
            session_id=session_id or 1,
            processing_time_ms=1,
        )


@pytest.fixture
async def client(monkeypatch, postgres_db) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_AUTH", "3/minute")
    monkeypatch.setenv("RATE_LIMIT_CHAT", "2/minute")
    settings_mod.get_settings.cache_clear()

    from app.helpers.ratelimit import attach_rate_limiter, limiter

    # Moving-window state is process-global; wipe it between tests.
    limiter.reset()

    app = FastAPI()
    app.state.container = SimpleNamespace(db_client=postgres_db)
    app.state.orchestrator = FakeOrchestrator()
    attach_rate_limiter(app)
    app.include_router(v1_router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as http_client:
        yield http_client
    settings_mod.get_settings.cache_clear()


async def _register(client: httpx.AsyncClient, name: str) -> dict:
    resp = await client.post(
        "/v1/auth/register",
        json={
            "username": name,
            "email": f"{name}@example.com",
            "password": "hunter2hunter2",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_login_rate_limited_by_ip(client):
    for _ in range(3):
        resp = await client.post(
            "/v1/auth/login",
            json={"username": "ghost", "password": "wrong-password"},
        )
        assert resp.status_code == 401
    blocked = await client.post(
        "/v1/auth/login",
        json={"username": "ghost", "password": "wrong-password"},
    )
    assert blocked.status_code == 429


async def test_chat_rate_limited_per_user(client):
    alice = await _register(client, "alice")
    headers = {"Authorization": f"Bearer {alice['access_token']}"}
    payload = {"messages": [{"role": "user", "content": "hi"}]}

    for _ in range(2):
        resp = await client.post("/v1/chat", json=payload, headers=headers)
        assert resp.status_code == 200, resp.text
    blocked = await client.post("/v1/chat", json=payload, headers=headers)
    assert blocked.status_code == 429


async def test_chat_limit_does_not_bleed_across_users(client):
    """Register burns 2 of the 3/minute auth budget, then each user gets an
    independent chat budget."""
    alice = await _register(client, "alice")
    bob = await _register(client, "bob")
    payload = {"messages": [{"role": "user", "content": "hi"}]}

    for _ in range(2):
        resp = await client.post(
            "/v1/chat",
            json=payload,
            headers={"Authorization": f"Bearer {alice['access_token']}"},
        )
        assert resp.status_code == 200

    fresh = await client.post(
        "/v1/chat",
        json=payload,
        headers={"Authorization": f"Bearer {bob['access_token']}"},
    )
    assert fresh.status_code == 200


async def test_429_includes_retry_after(client):
    for _ in range(3):
        await client.post(
            "/v1/auth/login", json={"username": "ghost", "password": "wrongwrong"}
        )
    blocked = await client.post(
        "/v1/auth/login", json={"username": "ghost", "password": "wrongwrong"}
    )
    assert blocked.status_code == 429
    assert "retry-after" in {k.lower() for k in blocked.headers}
