"""Compatibility imports for saved extraction outcomes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.persistence.extraction_outcomes import PostgresExtractionOutcomes
from document_intelligence.contracts import (
    MAX_EXTRACTION_CHARACTERS,
    MAX_ERROR_CHARACTERS,
    ExtractionScope,
    QueuedPage,
    PageOutcome,
    FileOutcome,
)

if TYPE_CHECKING:
    from app.adapters.persistence.application import ApplicationDatabase


class ExtractionOutcomes(PostgresExtractionOutcomes):
    """Retain the former database-client constructor for existing callers."""

    def __init__(
        self,
        database: ApplicationDatabase,
        max_pages: int = 50,
        *,
        operation_timeout_seconds: float = 5.0,
    ) -> None:
        """Reuse the caller's pool without changing outcome or deadline rules.

        Args:
            database: Existing application database client.
            max_pages: Maximum saved page manifest size.
            operation_timeout_seconds: Bound for each database operation.
        """
        super().__init__(
            database.pool,
            max_pages,
            operation_timeout_seconds=operation_timeout_seconds,
        )


__all__ = [
    "MAX_EXTRACTION_CHARACTERS",
    "MAX_ERROR_CHARACTERS",
    "ExtractionScope",
    "QueuedPage",
    "PageOutcome",
    "FileOutcome",
    "ExtractionOutcomes",
]
