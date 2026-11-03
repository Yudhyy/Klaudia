"""Auth routes use identity storage without an extraction database client."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from app.routes.v1 import auth
from app.services.auth.passwords import hash_password, verify_password
from config.settings import Settings

PASSWORD = "hunter2hunter2"
REGISTRATION = {
    "username": "alice",
    "email": "alice@example.com",
    "password": PASSWORD,
}


@pytest.fixture
def identity():
    """Supply only the three identity operations to route tests."""
    return SimpleNamespace(
        get_user_by_username=AsyncMock(return_value=None),
        create_user=AsyncMock(return_value=42),
        update_last_login=AsyncMock(),
    )


@pytest.fixture
async def client(identity, monkeypatch) -> AsyncIterator[httpx.AsyncClient]:
    """Serve auth routes with no extraction client on the container."""
    settings = Settings(
        _env_file=None, JWT_SECRET="identity-test-secret-with-at-least-32-bytes"
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(auth.limiter, "enabled", False)
    app = FastAPI()
    app.state.container = SimpleNamespace(identity_store=identity)
    app.include_router(auth.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        yield http_client


async def test_registration_uses_only_identity_storage(client, identity):
    """Pass a password hash to the identity interface and retain response fields."""
    response = await client.post("/auth/register", json=REGISTRATION)
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"access_token", "token_type", "user_id", "username"}
    assert (body["user_id"], body["username"], body["token_type"]) == (
        42,
        "alice",
        "bearer",
    )
    saved_credentials = identity.create_user.await_args.kwargs
    assert saved_credentials["username"] == "alice"
    assert saved_credentials["email"] == "alice@example.com"
    assert verify_password(PASSWORD, saved_credentials["password_hash"])
    identity.update_last_login.assert_not_awaited()


async def test_duplicate_username_does_not_create_an_account(client, identity):
    """Keep the username precheck and its exact conflict response."""
    identity.get_user_by_username.return_value = {"user_id": 42}
    response = await client.post("/auth/register", json=REGISTRATION)
    assert (response.status_code, response.json()) == (
        409,
        {"detail": "Username already taken"},
    )
    identity.create_user.assert_not_awaited()


async def test_registration_keeps_generic_storage_failure_mapping(client, identity):
    """Retain the current conflict response without exposing the store error."""
    identity.create_user.side_effect = ConnectionError("private-store-detail")
    response = await client.post("/auth/register", json=REGISTRATION)
    assert (response.status_code, response.json()) == (
        409,
        {"detail": "Account already exists"},
    )


async def test_login_uses_only_identity_storage(client, identity):
    """Save the authenticated account's login time before returning its token."""
    identity.get_user_by_username.return_value = {
        "user_id": 42,
        "username": "alice",
        "password_hash": hash_password(PASSWORD),
    }
    response = await client.post(
        "/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["user_id"] == 42
    identity.get_user_by_username.assert_awaited_once_with("alice")
    identity.update_last_login.assert_awaited_once_with(42)


@pytest.mark.parametrize("stored_hash", [None, "", "not-a-real-hash"])
async def test_rejected_login_does_not_update_identity(client, identity, stored_hash):
    """Unknown accounts and invalid credential records keep the same response."""
    if stored_hash is not None:
        identity.get_user_by_username.return_value = {
            "user_id": 42,
            "username": "alice",
            "password_hash": stored_hash,
        }
    response = await client.post(
        "/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert (response.status_code, response.json()) == (
        401,
        {"detail": "Invalid username or password"},
    )
    identity.update_last_login.assert_not_awaited()


async def test_login_write_failure_prevents_token_creation(
    client, identity, monkeypatch
):
    """Propagate a failed last-login write before issuing an access token."""
    identity.get_user_by_username.return_value = {
        "user_id": 42,
        "username": "alice",
        "password_hash": hash_password(PASSWORD),
    }
    identity.update_last_login.side_effect = ConnectionError("login write failed")
    issue_token = Mock()
    monkeypatch.setattr(auth, "_issue_token", issue_token)
    with pytest.raises(ConnectionError, match="login write failed"):
        await client.post(
            "/auth/login", json={"username": "alice", "password": PASSWORD}
        )
    issue_token.assert_not_called()
