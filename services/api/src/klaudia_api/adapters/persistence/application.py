"""Application PostgreSQL lifecycle and compatibility storage methods."""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

from klaudia_api.adapters.identity import PostgresIdentityStore
from klaudia_api.adapters.persistence.conversations import PostgresConversationStore
from klaudia_api.adapters.persistence.documents import PostgresDocumentStore
from klaudia_api.adapters.persistence.migrations import check_database
from identity_core.ports import UserCredentials
from klaudia_api.composition.settings import Settings

logger = logging.getLogger(__name__)


def _affected_rows(command_status: str) -> int:
    """Return the row count from an asyncpg command status."""
    final_token = command_status.rsplit(" ", 1)[-1]
    return int(final_token) if final_token.isdigit() else 0


class ApplicationDatabase:
    """Async PostgreSQL client for application-owned data."""

    def __init__(self, settings: Settings) -> None:
        """Store database settings without opening a connection."""
        self._dsn = settings.database_url
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Open the pool after release migrations and retain seed behavior.

        Raises:
            RuntimeError: Required migrations or owned objects are missing.
        """
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10)
        try:
            async with self.pool.acquire() as connection:
                await check_database(connection)
                await connection.execute("""INSERT INTO "user" (user_id, username, email, password_hash)
                    VALUES (1, 'dev', 'dev@local', 'not-a-real-hash')
                    ON CONFLICT (user_id) DO NOTHING""")
            await self.reset_user_id_sequence()
        except BaseException:
            await self.close()
            raise
        logger.info("PostgreSQL pool connected and migration revisions checked")

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        """Return the active pool.

        Raises:
            RuntimeError: If connect has not completed.
        """
        if self._pool is None:
            raise RuntimeError("Database not connected")
        return self._pool

    async def reset_user_id_sequence(self) -> None:
        """Advance the user sequence past explicit seed rows."""
        await self.pool.execute(
            """SELECT setval(
                   pg_get_serial_sequence('"user"', 'user_id'),
                   GREATEST((SELECT COALESCE(MAX(user_id), 1) FROM "user"), 1)
               )"""
        )

    async def fetchone(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        """Fetch one row as a dictionary."""
        row = await self.pool.fetchrow(sql, *params)
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Fetch all rows as dictionaries."""
        rows = await self.pool.fetch(sql, *params)
        return [dict(row) for row in rows]

    async def fetchval(self, sql: str, params: tuple = ()) -> Any:
        """Fetch the first column from one row."""
        return await self.pool.fetchval(sql, *params)

    async def execute(self, sql: str, params: tuple = ()) -> int:
        """Run a statement and return its affected-row count."""
        command_status = await self.pool.execute(sql, *params)
        return _affected_rows(command_status)

    async def get_user_by_username(self, username: str) -> UserCredentials | None:
        """Forward credential lookup for existing database-client callers.

        Args:
            username: Exact account name.

        Returns:
            Stored credentials, or None when no account matches.
        """
        return await PostgresIdentityStore(self.pool).get_user_by_username(username)

    async def create_user(self, username: str, email: str, password_hash: str) -> int:
        """Forward account creation for existing database-client callers.

        Args:
            username: Account name.
            email: Account email address.
            password_hash: Hash produced by the authentication service.

        Returns:
            Generated account ID.

        Raises:
            asyncpg.UniqueViolationError: The username or email already exists.
        """
        return await PostgresIdentityStore(self.pool).create_user(
            username, email, password_hash
        )

    async def update_last_login(self, user_id: int) -> None:
        """Forward login updates for existing database-client callers.

        Args:
            user_id: Authenticated account ID.
        """
        await PostgresIdentityStore(self.pool).update_last_login(user_id)

    async def get_session_owner(self, session_id: int) -> int | None:
        """Return the user that owns a session."""
        return await PostgresConversationStore(self.pool).get_session_owner(session_id)

    async def create_session(
        self, user_id: int, session_name: str | None = None
    ) -> int:
        """Create a session and return its ID."""
        return await PostgresConversationStore(self.pool).create_session(
            user_id, session_name
        )

    async def get_conversation_history(
        self, session_id: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Return recent session messages in reverse time order."""
        return await PostgresConversationStore(self.pool).get_conversation_history(
            session_id, limit
        )

    async def save_message(
        self,
        session_id: int,
        user_id: int,
        sender: str,
        message_text: str,
        file_id: int | None = None,
    ) -> str:
        """Store a conversation message and return its ID."""
        return await PostgresConversationStore(self.pool).save_message(
            session_id, user_id, sender, message_text, file_id
        )

    async def update_session_timestamp(self, session_id: int) -> None:
        """Mark a session as recently updated."""
        return await PostgresConversationStore(self.pool).update_session_timestamp(
            session_id
        )

    async def get_session_files(self, session_id: int) -> list[dict[str, Any]]:
        """Return files uploaded in a session."""
        return await PostgresDocumentStore(self.pool).get_session_files(session_id)

    async def get_sessions(self, user_id: int) -> list[dict[str, Any]]:
        """Return a user's sessions in reverse update order."""
        return await PostgresConversationStore(self.pool).get_sessions(user_id)

    async def get_session_messages(self, session_id: int) -> list[dict[str, Any]]:
        """Return session messages in chronological order."""
        return await PostgresConversationStore(self.pool).get_session_messages(
            session_id
        )

    async def find_blob(self, user_id: int, blake3: str) -> dict[str, Any] | None:
        """Find a content blob in a user's keyspace."""
        return await PostgresDocumentStore(self.pool).find_blob(user_id, blake3)

    async def insert_blob(
        self,
        *,
        user_id: int,
        blake3: str,
        minio_key: str,
        content_type: str,
        size_bytes: int,
        page_count: int,
    ) -> int:
        """Store blob metadata and return its ID."""
        return await PostgresDocumentStore(self.pool).insert_blob(
            user_id=user_id,
            blake3=blake3,
            minio_key=minio_key,
            content_type=content_type,
            size_bytes=size_bytes,
            page_count=page_count,
        )

    async def upsert_blob_page(
        self,
        *,
        blob_id: int,
        page: int,
        page_blake3: str,
        page_minio_key: str,
    ) -> None:
        """Create or update one page in a content blob."""
        return await PostgresDocumentStore(self.pool).upsert_blob_page(
            blob_id=blob_id,
            page=page,
            page_blake3=page_blake3,
            page_minio_key=page_minio_key,
        )

    async def get_cached_extraction(
        self, user_id: int, page_blake3: str
    ) -> dict[str, Any] | None:
        """Return a stored extraction by user and page hash."""
        return await PostgresDocumentStore(self.pool).get_cached_extraction(
            user_id, page_blake3
        )

    async def upsert_extraction(
        self,
        *,
        user_id: int,
        page_blake3: str,
        extraction_json: str,
        ocr_model: str | None,
        schema_version: str | None,
    ) -> None:
        """Create or update a stored page extraction."""
        return await PostgresDocumentStore(self.pool).upsert_extraction(
            user_id=user_id,
            page_blake3=page_blake3,
            extraction_json=extraction_json,
            ocr_model=ocr_model,
            schema_version=schema_version,
        )

    async def link_metadata_file_blob(
        self, metadata_file_id: int, blob_id: int
    ) -> None:
        """Link an uploaded file to its content blob."""
        return await PostgresDocumentStore(self.pool).link_metadata_file_blob(
            metadata_file_id, blob_id
        )
