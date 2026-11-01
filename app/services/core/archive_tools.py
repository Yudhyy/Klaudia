"""Bounded, owner-checked document retrieval for the main chat runtime."""

from typing import Any

from app.services.extraction.infra.db_client import AppDBClient
from agent_core.agent.ports.errors import ResourceNotFoundError


class ArchiveReader:
    """Expose document facts as untrusted evidence under authenticated ownership."""

    def __init__(self, database: AppDBClient, user_id: int) -> None:
        """Bind server identity outside model-visible tool arguments.

        Args:
            database: Existing document and extraction store.
            user_id: JWT-derived identity supplied by chat.
        """
        self._database = database
        self._user_id = user_id

    async def search(self, name: str = "", offset: int = 0) -> dict[str, Any]:
        """Find owned metadata without fetching any page bodies.

        Args:
            name: Literal, case-insensitive filename substring.
            offset: Number of matching documents already inspected.

        Returns:
            Bounded metadata and an explicit continuation offset.
        """
        documents = await self._database.fetchall(
            """SELECT f.id AS file_id, left(f.file_name, 256) AS file_name,
                      f.total_pages, left(f.status, 64) AS status
               FROM metadata_file f JOIN session s ON s.session_id = f.session_id
               WHERE f.user_id = $1 AND s.user_id = $1
                 AND strpos(lower(f.file_name), lower($2)) > 0
               ORDER BY f.id DESC LIMIT 21 OFFSET $3""",
            (self._user_id, name, offset),
        )
        return {
            "documents": documents[:20],
            "next_offset": offset + 20 if len(documents) > 20 else None,
        }

    async def read(self, file_id: int, page: int, offset: int = 0) -> dict[str, Any]:
        """Fetch a bounded extraction segment after checking current ownership.

        Args:
            file_id: Archived document identity.
            page: One-based page number.
            offset: Zero-based character offset in the stored extraction text.

        Returns:
            Page status, text segment and continuation evidence.

        Raises:
            ResourceNotFoundError: Document/page is missing or foreign.
        """
        document = await self._database.fetchone(
            """SELECT f.id AS file_id, p.page, left(p.status, 64) AS status,
                      substring(p.agent_extracted FROM $4 + 1 FOR 8192) AS extraction_text,
                      length(p.agent_extracted) AS total_characters
               FROM metadata_file f JOIN session s ON s.session_id = f.session_id
               JOIN pages p ON p.metadata_file_id = f.id
               WHERE f.user_id = $1 AND s.user_id = $1 AND f.id = $2 AND p.page = $3""",
            (self._user_id, file_id, page, offset),
        )
        if document is None:
            raise ResourceNotFoundError("Document page not found")
        total = document["total_characters"] or 0
        document["offset"] = offset
        document["next_offset"] = offset + 8192 if offset + 8192 < total else None
        return document
