"""PostgreSQL persistence for the owned application storage interface."""

import json
from typing import Any

import asyncpg

from document_intelligence.contracts import DocumentPage, FileUpload, PageOutcome


class PostgresDocumentStore:
    """Reuse the application pool without owning its lifetime."""

    def __init__(self, pool: asyncpg.Pool | asyncpg.Connection) -> None:
        """Bind the supplied pool or held connection.

        Args:
            pool: Application-owned PostgreSQL access.
        """
        self._pool = pool

    async def create_file(self, upload: FileUpload) -> int:
        """Save prepared file metadata or a pre-extraction failure.

        Args:
            upload: Original session, owner and file metadata.

        Returns:
            Stored file identity.
        """
        return int(
            await self._pool.fetchval(
                """INSERT INTO metadata_file
                (session_id, user_id, type, file_name, total_pages, status, status_message)
                VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
                upload.session_id,
                upload.user_id,
                upload.file_type,
                upload.file_name,
                upload.total_pages,
                upload.status,
                upload.status_message,
            )
        )

    async def save_page(self, file_id: int, page: PageOutcome) -> int:
        """Append one inline page with the original stored status and message.

        Args:
            file_id: Prepared file identity.
            page: Terminal inline outcome.

        Returns:
            Stored page identity.
        """
        if page.status == "failed":
            return int(
                await self._pool.fetchval(
                    """INSERT INTO pages (metadata_file_id, page, status, status_message)
                    VALUES ($1, $2, 'failed', 'OCR failed') RETURNING id""",
                    file_id,
                    page.page,
                )
            )
        return int(
            await self._pool.fetchval(
                """INSERT INTO pages
                (metadata_file_id, page, agent_extracted, status, status_message)
                VALUES ($1, $2, $3, 'extracted', $4) RETURNING id""",
                file_id,
                page.page,
                json.dumps(page.extraction, ensure_ascii=False),
                "cached" if page.cache_layer else "extracted",
            )
        )

    async def finish_file(self, file_id: int, status: str, message: str) -> None:
        """Save the observed inline aggregate after page persistence.

        Args:
            file_id: Prepared file identity.
            status: Observed aggregate status.
            message: Current status summary.
        """
        await self._pool.execute(
            "UPDATE metadata_file SET status = $1, status_message = $2 WHERE id = $3",
            status,
            message,
            file_id,
        )

    async def search_documents(
        self, user_id: int, name: str = "", offset: int = 0
    ) -> list[dict[str, Any]]:
        """Read bounded metadata under both file and session ownership.

        Args:
            user_id: Authenticated owner.
            name: Literal case-insensitive filename substring.
            offset: Matching metadata rows already read.

        Returns:
            At most 21 metadata rows, newest first.
        """
        return [
            dict(row)
            for row in await self._pool.fetch(
                """SELECT f.id AS file_id, left(f.file_name, 256) AS file_name,
                      f.total_pages, left(f.status, 64) AS status
               FROM metadata_file f JOIN session s ON s.session_id = f.session_id
               WHERE f.user_id = $1 AND s.user_id = $1
                 AND strpos(lower(f.file_name), lower($2)) > 0
               ORDER BY f.id DESC LIMIT 21 OFFSET $3""",
                user_id,
                name,
                offset,
            )
        ]

    async def read_document_page(
        self, user_id: int, page: DocumentPage
    ) -> dict[str, Any] | None:
        """Read one bounded segment while both file and session remain owned.

        Args:
            user_id: Authenticated owner.
            page: File, page and character offset to read.

        Returns:
            Current extraction segment, or None for missing access.
        """
        row = await self._pool.fetchrow(
            """SELECT f.id AS file_id, p.page, left(p.status, 64) AS status,
                      substring(p.agent_extracted FROM $4 + 1 FOR 8192) AS extraction_text,
                      length(p.agent_extracted) AS total_characters
               FROM metadata_file f JOIN session s ON s.session_id = f.session_id
               JOIN pages p ON p.metadata_file_id = f.id
               WHERE f.user_id = $1 AND s.user_id = $1 AND f.id = $2 AND p.page = $3""",
            user_id,
            page.file_id,
            page.page,
            page.offset,
        )
        return dict(row) if row else None

    async def count_owned_documents(self, user_id: int, document_ids: list[int]) -> int:
        """Recheck saved document access through the supplied task connection.

        Args:
            user_id: Original authenticated task owner.
            document_ids: Distinct saved document identities.

        Returns:
            Number of files that still retain both ownership checks.
        """
        return await self._pool.fetchval(
            "SELECT count(*) FROM metadata_file f JOIN session s ON s.session_id = f.session_id WHERE f.user_id = $1 AND s.user_id = $1 AND f.id = ANY($2::int[])",
            user_id,
            document_ids,
        )

    async def get_session_owner(self, session_id: int) -> int | None:
        """Return the user that owns a session."""
        row = await self._pool.fetchrow(
            "SELECT user_id FROM session WHERE session_id = $1", session_id
        )
        return row["user_id"] if row else None

    async def get_session_files(self, session_id: int) -> list[dict[str, Any]]:
        """Return files uploaded in a session."""
        return [
            dict(row)
            for row in await self._pool.fetch(
                "SELECT * FROM metadata_file WHERE session_id = $1 ORDER BY created_at",
                session_id,
            )
        ]

    async def find_blob(self, user_id: int, blake3: str) -> dict[str, Any] | None:
        """Find a content blob in a user's keyspace."""
        row = await self._pool.fetchrow(
            "SELECT * FROM file_blob WHERE user_id = $1 AND blake3 = $2",
            user_id,
            blake3,
        )
        return dict(row) if row else None

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
        blob_id = await self._pool.fetchval(
            """
            INSERT INTO file_blob
                (user_id, blake3, minio_key, content_type, size_bytes, page_count)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING blob_id
            """,
            user_id,
            blake3,
            minio_key,
            content_type,
            size_bytes,
            page_count,
        )
        return int(blob_id)

    async def upsert_blob_page(
        self, *, blob_id: int, page: int, page_blake3: str, page_minio_key: str
    ) -> None:
        """Create or update one page in a content blob."""
        await self._pool.execute(
            """
            INSERT INTO file_blob_page (blob_id, page, page_blake3, page_minio_key)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT(blob_id, page) DO UPDATE SET
                page_blake3 = EXCLUDED.page_blake3,
                page_minio_key = EXCLUDED.page_minio_key
            """,
            blob_id,
            page,
            page_blake3,
            page_minio_key,
        )

    async def get_cached_extraction(
        self, user_id: int, page_blake3: str
    ) -> dict[str, Any] | None:
        """Return a stored extraction by user and page hash."""
        row = await self._pool.fetchrow(
            """
            SELECT extraction_json, ocr_model, schema_version, created_at
            FROM blob_extraction
            WHERE user_id = $1 AND page_blake3 = $2
            """,
            user_id,
            page_blake3,
        )
        return dict(row) if row else None

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
        await self._pool.execute(
            """
            INSERT INTO blob_extraction
                (user_id, page_blake3, extraction_json, ocr_model, schema_version)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT(user_id, page_blake3) DO UPDATE SET
                extraction_json = EXCLUDED.extraction_json,
                ocr_model = EXCLUDED.ocr_model,
                schema_version = EXCLUDED.schema_version
            """,
            user_id,
            page_blake3,
            extraction_json,
            ocr_model,
            schema_version,
        )

    async def link_metadata_file_blob(
        self, metadata_file_id: int, blob_id: int
    ) -> None:
        """Link an uploaded file to its content blob."""
        await self._pool.execute(
            """
            INSERT INTO metadata_file_blob (metadata_file_id, blob_id)
            VALUES ($1, $2)
            ON CONFLICT(metadata_file_id) DO UPDATE SET blob_id = EXCLUDED.blob_id
            """,
            metadata_file_id,
            blob_id,
        )
