"""Storage capabilities used by ingestion, archive tools and progress reads."""

from typing import Any, Protocol

from app.services.documents.contracts import (
    DocumentPage,
    FileOutcome,
    FileUpload,
    ExtractionScope,
    PageOutcome,
    QueuedPage,
)


class DocumentArchive(Protocol):
    """Read current document facts under both file and session ownership."""

    async def search_documents(
        self, user_id: int, name: str = "", offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return at most 21 matching metadata rows for bounded archive pagination.

        Args:
            user_id: Authenticated owner.
            name: Literal case-insensitive filename substring.
            offset: Number of matching files already read.

        Returns:
            Owned document metadata, newest first.
        """
        ...

    async def read_document_page(
        self, user_id: int, page: DocumentPage
    ) -> dict[str, Any] | None:
        """Read at most 8192 characters from a currently owned document page.

        Args:
            user_id: Authenticated owner.
            page: Document identity, page number and character offset.

        Returns:
            Extraction segment and total length, or None for missing access.
        """
        ...

    async def count_owned_documents(self, user_id: int, document_ids: list[int]) -> int:
        """Count saved input files that retain both file and session ownership.

        Args:
            user_id: Authenticated task owner.
            document_ids: Distinct saved document identities.

        Returns:
            Number of documents still available to the owner.
        """
        ...


class DocumentIngestionStore(Protocol):
    """Persist upload preparation and extraction caches without raw SQL."""

    async def get_session_owner(self, session_id: int) -> int | None:
        """Return the user that owns a session."""
        ...

    async def find_blob(self, user_id: int, blake3: str) -> dict[str, Any] | None:
        """Find a content blob in a user's keyspace."""
        ...

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
        ...

    async def upsert_blob_page(
        self, *, blob_id: int, page: int, page_blake3: str, page_minio_key: str
    ) -> None:
        """Create or update one page in a content blob."""
        ...

    async def get_cached_extraction(
        self, user_id: int, page_blake3: str
    ) -> dict[str, Any] | None:
        """Return a stored extraction by user and page hash."""
        ...

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
        ...

    async def link_metadata_file_blob(
        self, metadata_file_id: int, blob_id: int
    ) -> None:
        """Link an uploaded file to its content blob."""
        ...

    async def create_file(self, upload: FileUpload) -> int:
        """Record a prepared upload or a pre-extraction failure.

        Args:
            upload: Original session, owner and prepared file metadata.

        Returns:
            Stored file identity.
        """
        ...

    async def save_page(self, file_id: int, page: PageOutcome) -> int:
        """Append an inline extraction page with the current stored status format.

        Args:
            file_id: Prepared file identity.
            page: Terminal page outcome.

        Returns:
            Stored page identity.
        """
        ...

    async def finish_file(self, file_id: int, status: str, message: str) -> None:
        """Save the inline file status after all prepared pages finish.

        Args:
            file_id: Prepared file identity.
            status: Observed aggregate status.
            message: Current human-readable status summary.
        """
        ...


class ExtractionOutcomeStore(Protocol):
    """Keep queued page writes and file completion atomic and owner checked."""

    async def resolve_page(
        self, job: QueuedPage
    ) -> tuple[ExtractionScope, dict[str, Any]]:
        """Resolve a job only while its file, session and blob remain owned.

        Args:
            job: Original owner, file, blob and page identities.

        Returns:
            Checked extraction scope and saved page source.

        Raises:
            ResourceNotFoundError: The queued page is absent or foreign.
        """
        ...

    async def read(
        self, scope: ExtractionScope, *, timeout_seconds: float | None = None
    ) -> FileOutcome:
        """Read bounded saved pages and update their file aggregate together.

        Args:
            scope: Original owner, session and file identities.
            timeout_seconds: Optional bound within the configured store deadline.

        Returns:
            Current outcome with missing pages explicitly pending.

        Raises:
            ResourceNotFoundError: The file or session is no longer owned.
            TimeoutError: Store access exceeds the supplied deadline.
        """
        ...

    async def save(self, scope: ExtractionScope, page: PageOutcome) -> FileOutcome:
        """Commit a terminal page and its file aggregate in one transaction.

        A failed retry must not erase a saved success for the same page.

        Args:
            scope: Original owner, session and file identities.
            page: Terminal outcome within the saved file manifest.

        Returns:
            Saved page outcomes and their aggregate status.

        Raises:
            ResourceNotFoundError: The file or session is no longer owned.
            ValueError: Page number or terminal status is invalid.
            TimeoutError: Store access exceeds its configured deadline.
        """
        ...
