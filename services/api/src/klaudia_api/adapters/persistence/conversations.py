"""PostgreSQL persistence for the owned application storage interface."""

from typing import Any
from uuid import uuid4

import asyncpg


class PostgresConversationStore:
    """Reuse the application pool without owning its lifetime."""

    def __init__(self, pool: asyncpg.Pool | asyncpg.Connection) -> None:
        """Bind the supplied pool or held connection.

        Args:
            pool: Application-owned PostgreSQL access.
        """
        self._pool = pool

    async def get_session_owner(self, session_id: int) -> int | None:
        """Return the user that owns a session."""
        row = await self._pool.fetchrow(
            "SELECT user_id FROM session WHERE session_id = $1", session_id
        )
        return row["user_id"] if row else None

    async def create_session(
        self, user_id: int, session_name: str | None = None
    ) -> int:
        """Create a session and return its ID."""
        session_id = await self._pool.fetchval(
            "INSERT INTO session (user_id, session_name) VALUES ($1, $2) RETURNING session_id",
            user_id,
            session_name,
        )
        return int(session_id)

    async def get_conversation_history(
        self, session_id: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Return recent session messages in reverse time order."""
        return [
            dict(row)
            for row in await self._pool.fetch(
                """
            SELECT sender, message_text, file_id, timestamp
            FROM conversation
            WHERE session_id = $1
            ORDER BY timestamp DESC
            LIMIT $2
            """,
                session_id,
                limit,
            )
        ]

    async def save_message(
        self,
        session_id: int,
        user_id: int,
        sender: str,
        message_text: str,
        file_id: int | None = None,
    ) -> str:
        """Store a conversation message and return its ID."""
        message_id = uuid4().hex
        await self._pool.execute(
            """
            INSERT INTO conversation
                (message_id, session_id, user_id, sender, message_text, file_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            message_id,
            session_id,
            user_id,
            sender,
            message_text,
            file_id,
        )
        return message_id

    async def update_session_timestamp(self, session_id: int) -> None:
        """Mark a session as recently updated."""
        await self._pool.execute(
            "UPDATE session SET updated_at = CURRENT_TIMESTAMP WHERE session_id = $1",
            session_id,
        )

    async def get_sessions(self, user_id: int) -> list[dict[str, Any]]:
        """Return a user's sessions in reverse update order."""
        return [
            dict(row)
            for row in await self._pool.fetch(
                """
            SELECT session_id, session_name, created_at, updated_at
            FROM session
            WHERE user_id = $1
            ORDER BY updated_at DESC
            """,
                user_id,
            )
        ]

    async def get_session_messages(self, session_id: int) -> list[dict[str, Any]]:
        """Return session messages in chronological order."""
        return [
            dict(row)
            for row in await self._pool.fetch(
                """
            SELECT sender, message_text, file_id, timestamp
            FROM conversation
            WHERE session_id = $1
            ORDER BY timestamp ASC
            """,
                session_id,
            )
        ]
