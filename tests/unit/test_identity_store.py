"""Identity persistence borrows a pool and preserves database failures."""

from unittest.mock import AsyncMock

import asyncpg
import pytest


async def test_identity_adapter_borrows_pool_without_schema_or_lifecycle(monkeypatch):
    """Use the caller's pool for all three operations without owning its lifetime."""
    from klaudia_api.adapters.identity import PostgresIdentityStore

    create_pool = AsyncMock(side_effect=AssertionError("Unexpected identity pool"))
    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.fetchval.return_value = 42
    pool.fetchrow.return_value = {
        "user_id": 42,
        "username": "alice",
        "email": "alice@example.com",
        "password_hash": "hash-a",
    }
    identity = PostgresIdentityStore(pool)
    assert await identity.create_user("alice", "alice@example.com", "hash-a") == 42
    assert await identity.get_user_by_username("alice") == pool.fetchrow.return_value
    await identity.update_last_login(42)
    pool.execute.assert_awaited_once()
    pool.close.assert_not_awaited()
    create_pool.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "arguments", "pool_method"),
    [
        ("get_user_by_username", ("alice",), "fetchrow"),
        ("create_user", ("alice", "alice@example.com", "hash-a"), "fetchval"),
        ("update_last_login", (42,), "execute"),
    ],
)
async def test_identity_adapter_keeps_connection_failures(
    method, arguments, pool_method
):
    """Leave database failures visible to the caller's existing error handling."""
    from klaudia_api.adapters.identity import PostgresIdentityStore

    pool = AsyncMock(spec=asyncpg.Pool)
    getattr(pool, pool_method).side_effect = ConnectionError("store unavailable")
    identity = PostgresIdentityStore(pool)
    with pytest.raises(ConnectionError, match="store unavailable"):
        await getattr(identity, method)(*arguments)
