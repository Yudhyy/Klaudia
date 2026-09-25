"""Comparison identity cleanup deletes only the account created by its fixture."""

from uuid import uuid4

import pytest

from app.services.extraction.infra.db_client import AppDBClient
from config.settings import Settings
from tests.e2e.comparison_owner import comparison_owner
from tests.integration.postgres import POSTGRES_TEST_URL


async def test_comparison_owner_cleans_sessions_after_failure_without_touching_other_owner():
    """Cleanup includes failed trials but leaves another fixture's account intact."""
    settings = Settings(DATABASE_URL=POSTGRES_TEST_URL)
    assert settings.database_url == POSTGRES_TEST_URL
    database = AppDBClient(settings)
    await database.connect()
    try:
        async with comparison_owner(database.pool) as other_id:
            with pytest.raises(RuntimeError, match="trial failed"):
                async with comparison_owner(database.pool) as user_id:
                    assert user_id != other_id
                    session_id = await database.pool.fetchval(
                        "INSERT INTO session (user_id) VALUES ($1) RETURNING session_id",
                        user_id,
                    )
                    await database.pool.execute(
                        "INSERT INTO conversation (message_id, session_id, user_id, sender, message_text) VALUES ($1, $2, $3, 'user', 'Read')",
                        uuid4().hex,
                        session_id,
                        user_id,
                    )
                    raise RuntimeError("trial failed")
            assert (
                await database.pool.fetchval(
                    'SELECT count(*) FROM "user" WHERE user_id = $1', user_id
                )
                == 0
            )
            assert (
                await database.pool.fetchval(
                    'SELECT count(*) FROM "user" WHERE user_id = $1', other_id
                )
                == 1
            )
            assert (
                await database.pool.fetchval(
                    "SELECT count(*) FROM session WHERE user_id = $1", user_id
                )
                == 0
            )
    finally:
        await database.close()
