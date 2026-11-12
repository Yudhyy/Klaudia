"""Exercise account registration and login through supplied capabilities."""

import asyncio
from unittest.mock import AsyncMock, Mock, call

import pytest


@pytest.fixture
def capabilities():
    """Supply storage, password and token capabilities without infrastructure."""
    dependencies = Mock()
    dependencies.store.get_user_by_username = AsyncMock(return_value=None)
    dependencies.store.create_user = AsyncMock(return_value=42)
    dependencies.store.update_last_login = AsyncMock()
    dependencies.passwords.hash.return_value = "stored-hash"
    dependencies.passwords.verify.return_value = True
    dependencies.tokens.issue.return_value = "access-token"
    return dependencies


@pytest.fixture
def identity(capabilities):
    """Build the use cases with the three supplied capabilities."""
    from identity_core.service import IdentityService

    return IdentityService(
        capabilities.store, capabilities.passwords, capabilities.tokens
    )


@pytest.fixture
def registration():
    """Supply an account request already validated by its caller."""
    from identity_core.contracts import Registration

    return Registration("alice", "alice@example.com", "private-password")


@pytest.fixture
def login(capabilities):
    """Use the stored account name after an exact credential lookup."""
    from identity_core.contracts import Login

    capabilities.store.get_user_by_username.return_value = {
        "user_id": 42,
        "username": "stored-alice",
        "email": "alice@example.com",
        "password_hash": "stored-hash",
    }
    return Login("alice", "private-password")


async def test_registration_hashes_before_storage_and_issues_after_storage(
    identity, capabilities, registration
):
    """Save only the hash and do not record registration as a login."""
    grant = await identity.register(registration)
    assert (grant.user_id, grant.username, grant.access_token) == (
        42,
        "alice",
        "access-token",
    )
    assert capabilities.mock_calls == [
        call.store.get_user_by_username("alice"),
        call.passwords.hash("private-password"),
        call.store.create_user(
            username="alice", email="alice@example.com", password_hash="stored-hash"
        ),
        call.tokens.issue(42),
    ]


async def test_duplicate_username_stops_before_hashing(
    identity, capabilities, registration
):
    """Reject an existing username before password or storage side effects."""
    from identity_core.contracts import UsernameTakenError

    capabilities.store.get_user_by_username.return_value = {"user_id": 42}
    with pytest.raises(UsernameTakenError):
        await identity.register(registration)
    assert capabilities.mock_calls == [call.store.get_user_by_username("alice")]


@pytest.mark.parametrize("stage", ["passwords.hash", "store.create_user"])
async def test_registration_keeps_generic_creation_failure(
    identity, capabilities, registration, stage
):
    """Map failures only in the existing hash and account-write scope."""
    from identity_core.contracts import AccountCreationError

    capability, method = stage.split(".")
    getattr(getattr(capabilities, capability), method).side_effect = ValueError(
        "private-storage-detail"
    )
    with pytest.raises(AccountCreationError) as failure:
        await identity.register(registration)
    assert "private-storage-detail" not in str(failure.value)
    capabilities.tokens.issue.assert_not_called()


async def test_registration_lookup_failure_stays_visible(
    identity, capabilities, registration
):
    """Do not turn an initial lookup failure into a duplicate-account error."""
    capabilities.store.get_user_by_username.side_effect = ConnectionError("lookup")
    with pytest.raises(ConnectionError, match="lookup"):
        await identity.register(registration)
    capabilities.passwords.hash.assert_not_called()


async def test_registration_cancellation_stays_visible(
    identity, capabilities, registration
):
    """Do not map a cancelled account write to a duplicate-account response."""
    capabilities.store.create_user.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await identity.register(registration)
    capabilities.tokens.issue.assert_not_called()


async def test_registration_token_failure_keeps_the_created_account(
    identity, capabilities, registration
):
    """A token failure occurs after the account write and stays visible."""
    capabilities.tokens.issue.side_effect = RuntimeError("token issue failed")
    with pytest.raises(RuntimeError, match="token issue failed"):
        await identity.register(registration)
    capabilities.store.create_user.assert_awaited_once()


async def test_login_records_success_before_issuing_token(
    identity, capabilities, login
):
    """Use the stored identity and issue a token after the login write."""
    grant = await identity.login(login)
    assert (grant.user_id, grant.username, grant.access_token) == (
        42,
        "stored-alice",
        "access-token",
    )
    assert capabilities.mock_calls == [
        call.store.get_user_by_username("alice"),
        call.passwords.verify("private-password", "stored-hash"),
        call.store.update_last_login(42),
        call.tokens.issue(42),
    ]


async def test_unknown_login_does_not_check_a_password(identity, capabilities, login):
    """Unknown accounts stop before password, login-write or token work."""
    from identity_core.contracts import InvalidCredentialsError

    capabilities.store.get_user_by_username.return_value = None
    with pytest.raises(InvalidCredentialsError):
        await identity.login(login)
    assert capabilities.mock_calls == [call.store.get_user_by_username("alice")]


async def test_wrong_password_does_not_record_login(identity, capabilities, login):
    """A rejected password cannot update login state or issue a token."""
    from identity_core.contracts import InvalidCredentialsError

    capabilities.passwords.verify.return_value = False
    with pytest.raises(InvalidCredentialsError):
        await identity.login(login)
    capabilities.store.update_last_login.assert_not_awaited()
    capabilities.tokens.issue.assert_not_called()


@pytest.mark.parametrize("stage", ["lookup", "verify", "write"])
async def test_login_dependency_failure_prevents_token(
    identity, capabilities, login, stage
):
    """Keep login dependency failures visible without issuing a token."""
    methods = {
        "lookup": capabilities.store.get_user_by_username,
        "verify": capabilities.passwords.verify,
        "write": capabilities.store.update_last_login,
    }
    methods[stage].side_effect = ConnectionError("login dependency failed")
    with pytest.raises(ConnectionError, match="login dependency failed"):
        await identity.login(login)
    capabilities.tokens.issue.assert_not_called()


async def test_login_token_failure_keeps_the_login_write(identity, capabilities, login):
    """A token failure does not undo the successful login timestamp."""
    capabilities.tokens.issue.side_effect = RuntimeError("token issue failed")
    with pytest.raises(RuntimeError, match="token issue failed"):
        await identity.login(login)
    capabilities.store.update_last_login.assert_awaited_once_with(42)


def test_contract_representations_omit_passwords_and_tokens(registration):
    """Keep secrets out of the default contract representation."""
    from identity_core.contracts import AuthenticatedAccount, Login

    contracts = (
        registration,
        Login("alice", "private-password"),
        AuthenticatedAccount(42, "alice", "private-token"),
    )
    assert all("private-" not in repr(contract) for contract in contracts)


def test_legacy_identity_contracts_share_canonical_types():
    """Retain existing imports without a second set of contract definitions."""
    from identity_core import ports
    from app.services.auth import store

    assert store.IdentityStore is ports.IdentityStore
    assert store.UserCredentials is ports.UserCredentials


def test_auth_helpers_share_the_canonical_adapter_functions():
    """Retain password and token imports for current supported callers."""
    from klaudia_api.adapters.auth import passwords, tokens
    from app.services.auth import passwords as legacy_passwords
    from app.services.auth import tokens as legacy_tokens

    assert legacy_passwords.hash_password is passwords.hash_password
    assert legacy_passwords.verify_password is passwords.verify_password
    assert legacy_tokens.create_access_token is tokens.create_access_token
    assert legacy_tokens.decode_access_token is tokens.decode_access_token
    assert legacy_tokens.InvalidTokenError is tokens.InvalidTokenError
