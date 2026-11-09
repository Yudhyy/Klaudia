"""Auth routes use supplied identity use cases and retain HTTP behavior."""

from collections.abc import AsyncIterator
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from app.routes.v1 import auth
from app.adapters.auth.passwords import BcryptPasswords
from app.adapters.auth.tokens import JwtTokenIssuer
from app.services.auth.passwords import hash_password, verify_password
from identity_core.contracts import AuthenticatedAccount, Login, Registration
from identity_core.service import IdentityService

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
def token_issuer():
    """Observe token issuance with the existing signed-token implementation."""
    return Mock(
        wraps=JwtTokenIssuer(
            "identity-test-secret-with-at-least-32-bytes", timedelta(days=7)
        )
    )


@pytest.fixture
def identity_service(identity, token_issuer):
    """Connect real use cases to the narrow account-store fixture."""
    return IdentityService(identity, BcryptPasswords(), token_issuer)


@pytest.fixture
async def client(identity_service, monkeypatch) -> AsyncIterator[httpx.AsyncClient]:
    """Serve auth routes with only identity use cases on the container."""
    monkeypatch.setattr(auth.limiter, "enabled", False)
    app = FastAPI()
    app.state.container = SimpleNamespace(identity_service=identity_service)
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
    client, identity, token_issuer
):
    """Propagate a failed last-login write before issuing an access token."""
    identity.get_user_by_username.return_value = {
        "user_id": 42,
        "username": "alice",
        "password_hash": hash_password(PASSWORD),
    }
    identity.update_last_login.side_effect = ConnectionError("login write failed")
    with pytest.raises(ConnectionError, match="login write failed"):
        await client.post(
            "/auth/login", json={"username": "alice", "password": PASSWORD}
        )
    token_issuer.issue.assert_not_called()


async def test_register_delegates_validated_fields(
    client, identity_service, monkeypatch
):
    """Map the supplied use-case output without reading account storage."""
    register = AsyncMock(return_value=AuthenticatedAccount(91, "created", "issued"))
    monkeypatch.setattr(identity_service, "register", register)
    response = await client.post("/auth/register", json=REGISTRATION)
    assert (response.status_code, response.json()) == (
        201,
        {
            "access_token": "issued",
            "token_type": "bearer",
            "user_id": 91,
            "username": "created",
        },
    )
    register.assert_awaited_once_with(
        Registration("alice", "alice@example.com", PASSWORD)
    )


async def test_login_delegates_credentials(client, identity_service, monkeypatch):
    """Return the stored identity supplied by the login use case."""
    login = AsyncMock(return_value=AuthenticatedAccount(91, "stored", "issued"))
    monkeypatch.setattr(identity_service, "login", login)
    response = await client.post(
        "/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert (response.status_code, response.json()["username"]) == (200, "stored")
    login.assert_awaited_once_with(Login("alice", PASSWORD))


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"username": "ab"},
        {"username": "a" * 65},
        {"username": "alice smith"},
        {"email": "invalid-email"},
        {"password": "short"},
        {"password": "a" * 129},
    ],
)
async def test_registration_validation_stops_before_use_case(
    client, identity_service, monkeypatch, invalid_fields
):
    """Keep HTTP field validation ahead of account operations."""
    register = AsyncMock()
    monkeypatch.setattr(identity_service, "register", register)
    response = await client.post("/auth/register", json=REGISTRATION | invalid_fields)
    assert response.status_code == 422
    register.assert_not_awaited()
