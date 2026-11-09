"""Atomic document revisions using the application's PostgreSQL pool."""

import json

import asyncpg

from app.adapters.persistence.migrations import APPLICATION_HISTORY
from ledger.migrations.runtime import check_history

from app.services.memory.contracts import DocumentEdit, DocumentPath, MemoryDocument
from app.services.memory.ports import DocumentConflict


class PostgresMemoryReader:
    """Read context through a pool or an already held task connection."""

    def __init__(self, reader: asyncpg.Pool | asyncpg.Connection) -> None:
        """Reuse the supplied PostgreSQL read access.

        Args:
            reader: Application pool or connection holding a task lock.
        """
        self._reader = reader

    async def read(self, user_id: int, path: DocumentPath) -> MemoryDocument:
        """Read only the authenticated owner's current document.

        Args:
            user_id: Server-derived owner identity.
            path: Allowed document path.

        Returns:
            Current content, a tombstone, or an explicit missing document.
        """
        row = await self._reader.fetchrow(
            "SELECT path, revision, status, content, actor_id, updated_at, source_note, policy "
            "FROM memory_document WHERE user_id = $1 AND path = $2",
            user_id,
            path,
        )
        return self._document(row) if row else MemoryDocument(path=path)

    @staticmethod
    def _document(row: asyncpg.Record) -> MemoryDocument:
        """Decode exact policy JSON with the current document contract.

        Args:
            row: Current or committed database row.

        Returns:
            Validated document with structured policy when present.
        """
        fields = dict(row)
        fields.pop("user_id", None)
        fields["policy"] = json.loads(fields["policy"]) if fields["policy"] else None
        return MemoryDocument(**fields)


class PostgresMemoryDocumentStore(PostgresMemoryReader):
    """Store human edits and tombstones with atomic revision history."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Reuse the application pool for reads and atomic edits.

        Args:
            pool: Connected PostgreSQL pool.
        """
        super().__init__(pool)
        self._pool = pool

    async def initialize(self) -> None:
        """Check the application migration without modifying stored documents.

        Raises:
            RuntimeError: Required migrations or owned objects are missing.
        """
        async with self._pool.acquire() as connection:
            await check_history(connection, APPLICATION_HISTORY)

    async def write(
        self, user_id: int, path: DocumentPath, edit: DocumentEdit
    ) -> MemoryDocument:
        """Create or replace content only at the expected revision.

        Args:
            user_id: Server-derived owner and actor identity.
            path: Allowed document path.
            edit: Validated content, source note and expected revision.

        Returns:
            The committed document.

        Raises:
            DocumentConflict: Another edit won or the expected revision is stale.
        """
        if edit.policy is not None and path != DocumentPath.ACCOUNTING_POLICY:
            raise ValueError("Structured policy belongs only to /accounting-policy.md")
        policy_json = edit.policy.model_dump_json() if edit.policy is not None else None
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """INSERT INTO memory_document
                   (user_id, path, revision, status, content, actor_id, source_note, policy)
                   SELECT $1, $2, 1, 'active', $3, $1, $4, $6::jsonb WHERE $5::bigint = 0
                   ON CONFLICT (user_id, path) DO NOTHING RETURNING *""",
                user_id,
                path,
                edit.content,
                edit.source_note,
                edit.expected_revision,
                policy_json,
            )
            if row is None and edit.expected_revision > 0:
                row = await connection.fetchrow(
                    """UPDATE memory_document SET revision = revision + 1,
                       status = 'active', content = $3, source_note = $4, policy = $6::jsonb,
                       updated_at = CURRENT_TIMESTAMP
                       WHERE user_id = $1 AND path = $2 AND revision = $5 RETURNING *""",
                    user_id,
                    path,
                    edit.content,
                    edit.source_note,
                    edit.expected_revision,
                    policy_json,
                )
            return await self._record_revision(connection, row)

    async def delete(
        self, user_id: int, path: DocumentPath, expected_revision: int
    ) -> MemoryDocument:
        """Hide current content while retaining the revision history.

        Args:
            user_id: Server-derived owner and actor identity.
            path: Allowed document path.
            expected_revision: Revision observed by the caller.

        Returns:
            A committed tombstone with its new revision.

        Raises:
            DocumentConflict: Document is absent or its revision is stale.
        """
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """UPDATE memory_document SET revision = revision + 1,
                   status = 'deleted', content = NULL, source_note = '', policy = NULL,
                   updated_at = CURRENT_TIMESTAMP
                   WHERE user_id = $1 AND path = $2 AND revision = $3 RETURNING *""",
                user_id,
                path,
                expected_revision,
            )
            return await self._record_revision(connection, row)

    @staticmethod
    async def _record_revision(
        connection: asyncpg.Connection, row: asyncpg.Record | None
    ) -> MemoryDocument:
        """Append history within the same transaction as the current value.

        Args:
            connection: Transaction holding the document's write lock.
            row: Newly written document, if the revision check passed.

        Returns:
            The document whose history was recorded.

        Raises:
            DocumentConflict: The write did not match the expected revision.
        """
        if row is None:
            raise DocumentConflict(
                "Document revision changed; read it before editing again"
            )
        await connection.execute(
            "INSERT INTO memory_document_revision "
            "(user_id, path, revision, status, content, actor_id, updated_at, source_note, policy) "
            "SELECT user_id, path, revision, status, content, actor_id, updated_at, source_note, policy "
            "FROM memory_document "
            "WHERE user_id = $1 AND path = $2",
            row["user_id"],
            row["path"],
        )
        return PostgresMemoryReader._document(row)
