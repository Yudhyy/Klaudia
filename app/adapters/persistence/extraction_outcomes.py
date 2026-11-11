"""Owner-checked PostgreSQL extraction outcomes and atomic file completion."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from document_intelligence.contracts import (
    MAX_EXTRACTION_CHARACTERS,
    MAX_ERROR_CHARACTERS,
    ExtractionScope,
    QueuedPage,
    PageOutcome,
    FileOutcome,
    page_outcome,
    aggregate_pages,
)
from ledger_core.resources import ResourceNotFoundError


class PostgresExtractionOutcomes:
    """Store owner-checked page state and file status in one transaction.

    Image/PDF preparation declares pages 1..total_pages before dispatch. Missing
    rows stay pending. A file lock serializes worker/cache writes, including
    retries, without relying on uniqueness in the historical pages schema.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        max_pages: int = 50,
        *,
        operation_timeout_seconds: float = 5.0,
    ) -> None:
        """Reuse the application pool and current outcome bounds.

        Args:
            pool: Application-owned PostgreSQL pool.
            max_pages: Maximum declared page manifest size.
            operation_timeout_seconds: Deadline for each database operation.

        Raises:
            ValueError: The operation deadline is not positive.
        """
        if operation_timeout_seconds <= 0:
            raise ValueError("Extraction outcome deadline must be positive")
        self._pool = pool
        self._max_pages = max_pages
        self._operation_timeout_seconds = operation_timeout_seconds

    async def resolve_page(
        self, job: QueuedPage
    ) -> tuple[ExtractionScope, dict[str, Any]]:
        """Resolve only a page linked to a currently owned file and blob."""
        async with asyncio.timeout(self._operation_timeout_seconds):
            row = await self._pool.fetchrow(
                """SELECT f.session_id, f.total_pages, p.page_blake3, p.page_minio_key
               FROM metadata_file f JOIN session s ON s.session_id = f.session_id
               JOIN metadata_file_blob link ON link.metadata_file_id = f.id
               JOIN file_blob b ON b.blob_id = link.blob_id
               LEFT JOIN file_blob_page p ON p.blob_id = b.blob_id AND p.page = $4
               WHERE f.id = $2 AND f.user_id = $1 AND s.user_id = $1
                 AND b.user_id = $1 AND b.blob_id = $3""",
                job.user_id,
                job.file_id,
                job.blob_id,
                job.page,
            )
        if row is None or not 1 <= job.page <= min(row["total_pages"], self._max_pages):
            raise ResourceNotFoundError("Extraction page not found")
        return ExtractionScope(job.user_id, row["session_id"], job.file_id), dict(row)

    async def read(
        self, scope: ExtractionScope, *, timeout_seconds: float | None = None
    ) -> FileOutcome:
        """Return at most max_pages outcomes after checking both owners.

        Page text is capped; oversized extraction is explicitly marked and can
        be retrieved through the paginated archive interface.
        """
        async with self._transaction(timeout_seconds) as connection:
            file = await self._lock_file(connection, scope)
            return await self._snapshot(connection, scope, file)

    async def save(self, scope: ExtractionScope, page: PageOutcome) -> FileOutcome:
        """Commit a terminal page and its file roll-up in the same transaction."""
        if page.status not in {"extracted", "cached", "failed"}:
            raise ValueError("Only terminal page outcomes can be saved")
        async with self._transaction() as connection:
            file = await self._lock_file(connection, scope)
            if not 1 <= page.page <= file["total_pages"]:
                raise ValueError("Page is outside the expected file pages")
            existing = await connection.fetchrow(
                """SELECT id, status FROM pages
                       WHERE metadata_file_id = $1 AND page = $2
                       ORDER BY id DESC LIMIT 1""",
                scope.file_id,
                page.page,
            )
            # A failed retry must not erase a previously committed success.
            if existing is None or existing["status"] != "extracted":
                await self._write_page(connection, scope, page)
            return await self._snapshot(connection, scope, file)

    @asynccontextmanager
    async def _transaction(
        self, timeout_seconds: float | None = None
    ) -> AsyncIterator[asyncpg.Connection]:
        """Bound pool acquisition and the complete file transaction together.

        Args:
            timeout_seconds: Optional deadline within the configured bound.

        Yields:
            One connection for file locking, page writes and aggregate updates.

        Raises:
            TimeoutError: Pool or transaction work exceeds the deadline.
        """
        deadline = self._operation_timeout_seconds
        if timeout_seconds is not None:
            deadline = min(deadline, timeout_seconds)
        try:
            async with asyncio.timeout(deadline):
                async with self._pool.acquire() as connection:
                    async with connection.transaction():
                        yield connection
        except TimeoutError as exc:
            raise TimeoutError(
                "Extraction outcomes exceeded the database deadline"
            ) from exc

    async def _lock_file(
        self, connection: asyncpg.Connection, scope: ExtractionScope
    ) -> dict[str, Any]:
        """Lock the file and its session after checking both current owners.

        Args:
            connection: Connection holding the outcome transaction.
            scope: Original owner, session and file identities.

        Returns:
            Bounded file name, page count and current status message.

        Raises:
            ResourceNotFoundError: File or session ownership is absent.
            ValueError: The stored file exceeds the page limit.
        """
        row = await connection.fetchrow(
            """SELECT f.total_pages, left(f.file_name, 256) AS file_name,
                      left(f.status_message, 1024) AS status_message
               FROM metadata_file f JOIN session s ON s.session_id = f.session_id
               WHERE f.id = $1 AND f.session_id = $2
                 AND f.user_id = $3 AND s.user_id = $3
               FOR UPDATE OF f, s""",
            scope.file_id,
            scope.session_id,
            scope.user_id,
        )
        if row is None:
            raise ResourceNotFoundError("Extraction file not found")
        if not 0 <= row["total_pages"] <= self._max_pages:
            raise ValueError("File exceeds the extraction outcome page limit")
        return dict(row)

    async def _write_page(
        self, connection: asyncpg.Connection, scope: ExtractionScope, page: PageOutcome
    ) -> None:
        """Write a terminal page within the transaction holding its file lock.

        Args:
            connection: Connection holding the outcome transaction.
            scope: Original file identity and owner.
            page: Terminal page state within the checked manifest.
        """
        status = "failed" if page.status == "failed" else "extracted"
        message = (page.error or "Extraction failed")[:MAX_ERROR_CHARACTERS]
        if page.status != "failed":
            message = (
                f"cached:{page.cache_layer}" if page.status == "cached" else "extracted"
            )
        extraction = (
            json.dumps(page.extraction, ensure_ascii=False)
            if status == "extracted"
            else None
        )
        updated = await connection.fetchval(
            """UPDATE pages SET agent_extracted = $3, status = $4, status_message = $5
               WHERE id = (SELECT id FROM pages WHERE metadata_file_id = $1 AND page = $2
                           ORDER BY id DESC LIMIT 1) RETURNING id""",
            scope.file_id,
            page.page,
            extraction,
            status,
            message,
        )
        if updated is None:
            await connection.execute(
                """INSERT INTO pages (metadata_file_id, page, agent_extracted, status, status_message)
                   VALUES ($1, $2, $3, $4, $5)""",
                scope.file_id,
                page.page,
                extraction,
                status,
                message,
            )

    async def _snapshot(
        self,
        connection: asyncpg.Connection,
        scope: ExtractionScope,
        file: dict[str, Any],
    ) -> FileOutcome:
        """Read distinct expected pages and save the file aggregate together.

        Args:
            connection: Connection holding the file and session locks.
            scope: Original file identity and owner.
            file: Checked file manifest and bounded metadata.

        Returns:
            Saved page facts and the aggregate state committed with them.
        """
        rows = await connection.fetch(
            """SELECT DISTINCT ON (page) page, status, left(status_message, 1024) AS message,
                      left(agent_extracted, $3) AS extraction,
                      length(agent_extracted) > $3 AS truncated
               FROM pages WHERE metadata_file_id = $1 AND page BETWEEN 1 AND $2
               ORDER BY page, id DESC LIMIT $2""",
            scope.file_id,
            file["total_pages"],
            MAX_EXTRACTION_CHARACTERS,
        )
        by_page = {row["page"]: dict(row) for row in rows}
        pages = tuple(
            page_outcome(number, by_page.get(number))
            for number in range(1, file["total_pages"] + 1)
        )
        status, message = aggregate_pages(pages)
        if not pages:
            message = file["status_message"] or "No pages prepared"
        await connection.execute(
            "UPDATE metadata_file SET status = $1, status_message = $2 WHERE id = $3",
            status,
            message,
            scope.file_id,
        )
        return FileOutcome(scope, file["file_name"], pages, status, message)
