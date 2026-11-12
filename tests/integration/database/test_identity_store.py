"""Identity storage behavior against the isolated PostgreSQL database."""

import asyncpg
import pytest


@pytest.fixture(params=("adapter", "compatibility"))
def identity_store(postgres_db, request):
    """Apply the same behavior checks to canonical and retained callers."""
    if request.param == "adapter":
        from klaudia_api.adapters.identity import PostgresIdentityStore

        return PostgresIdentityStore(postgres_db.pool)
    return postgres_db


async def test_identity_roundtrip(identity_store):
    """Return the generated ID and the unchanged credential fields."""
    user_id = await identity_store.create_user("alice", "alice@example.com", "hash-a")
    assert user_id > 1
    assert await identity_store.get_user_by_username("alice") == {
        "user_id": user_id,
        "username": "alice",
        "email": "alice@example.com",
        "password_hash": "hash-a",
    }


@pytest.mark.parametrize("username", ["missing", "ALICE", "alice' OR TRUE --"])
async def test_identity_lookup_matches_the_exact_username(identity_store, username):
    """Bind the supplied username without case changes or SQL interpretation."""
    await identity_store.create_user("alice", "alice@example.com", "hash-a")
    assert await identity_store.get_user_by_username(username) is None


@pytest.mark.parametrize(
    ("username", "email"),
    [("alice", "other@example.com"), ("other", "alice@example.com")],
)
async def test_identity_duplicates_keep_database_conflict(
    identity_store, username, email
):
    """Pass unique-constraint errors to the auth caller without translation."""
    await identity_store.create_user("alice", "alice@example.com", "hash-a")
    with pytest.raises(asyncpg.UniqueViolationError):
        await identity_store.create_user(username, email, "hash-b")


async def test_last_login_for_missing_account_is_a_noop(identity_store, postgres_db):
    """An absent account does not change the seed account's login time."""
    await identity_store.update_last_login(999)
    assert (
        await postgres_db.fetchval(
            'SELECT last_login FROM "user" WHERE user_id = $1', (1,)
        )
        is None
    )
