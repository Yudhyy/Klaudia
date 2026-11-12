"""Bounded, owner-checked document retrieval for the main chat runtime."""

from typing import Any

from document_intelligence.contracts import DocumentPage
from document_intelligence.ports import DocumentArchive
from agent_core.agent.ports.errors import ResourceNotFoundError


class ArchiveReader:
    """Expose document facts as untrusted evidence under authenticated ownership."""

    def __init__(self, database: DocumentArchive, user_id: int) -> None:
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
        documents = await self._database.search_documents(self._user_id, name, offset)
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
        document = await self._database.read_document_page(
            self._user_id, DocumentPage(file_id, page, offset)
        )
        if document is None:
            raise ResourceNotFoundError("Document page not found")
        total = document["total_characters"] or 0
        document["offset"] = offset
        document["next_offset"] = offset + 8192 if offset + 8192 < total else None
        return document
