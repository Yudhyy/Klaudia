"""Fresh application identities for sandbox-only financial comparisons."""

from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import uuid4

import asyncpg


@asynccontextmanager
async def comparison_owner(pool: asyncpg.Pool) -> AsyncIterator[int]:
    """Create a disabled test identity and delete its text-only session state.

    Args:
        pool: Isolated sandbox application database, checked by the live runner.

    Yields:
        Database-assigned user ID, never an existing account.

    Raises:
        Exception: Setup or cleanup fails; unexpected dependent records are retained.
    """
    name = f"comparison-{uuid4().hex}"
    user_id = await pool.fetchval(
        'INSERT INTO "user" (username, email, password_hash) VALUES ($1, $2, $3) RETURNING user_id',
        name,
        f"{name}@invalid.example",
        "!disabled-comparison-account",
    )
    try:
        yield user_id
    finally:
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "DELETE FROM conversation WHERE user_id = $1", user_id
                )
                await connection.execute(
                    "DELETE FROM session WHERE user_id = $1", user_id
                )
                await connection.execute(
                    'DELETE FROM "user" WHERE user_id = $1', user_id
                )
