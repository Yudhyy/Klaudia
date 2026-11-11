"""Saved document outcomes and bounded extraction evidence."""

import json
from dataclasses import dataclass, field
from typing import Any

MAX_EXTRACTION_CHARACTERS = 262144
MAX_ERROR_CHARACTERS = 1024


@dataclass(frozen=True)
class ExtractionScope:
    """Identify an extraction file under its original user and session."""

    user_id: int
    session_id: int
    file_id: int


@dataclass(frozen=True)
class QueuedPage:
    """Identify a queued page and its owner-scoped content blob."""

    user_id: int
    file_id: int
    blob_id: int
    page: int


@dataclass(frozen=True)
class PageOutcome:
    """Keep saved page state, bounded facts and explicit failure details."""

    page: int
    status: str
    extraction: dict[str, Any] = field(default_factory=dict)
    cache_layer: str | None = None
    error: str | None = None
    extraction_truncated: bool = False


@dataclass(frozen=True)
class FileOutcome:
    """Expose current saved pages and their aggregate file state."""

    scope: ExtractionScope
    file_name: str
    pages: tuple[PageOutcome, ...]
    status: str
    message: str

    @property
    def pending_pages(self) -> tuple[int, ...]:
        """List expected pages that have no terminal saved outcome.

        Returns:
            Pending page numbers in manifest order.
        """
        return tuple(page.page for page in self.pages if page.status == "pending")

    @property
    def complete(self) -> bool:
        """Report whether every expected page has a terminal saved outcome.

        Returns:
            True when no expected page remains pending.
        """
        return not self.pending_pages

    def as_payload(self) -> dict[str, Any]:
        """Build the existing chat extraction payload without storage details.

        Returns:
            Bounded file/page facts, completion state and pending page numbers.
        """
        return {
            "file_id": self.scope.file_id,
            "file_name": self.file_name,
            "pages": [
                {
                    "page": page.page,
                    "extraction": page.extraction,
                    "status": "extracted" if page.status == "cached" else page.status,
                    "error": page.error,
                    "extraction_truncated": page.extraction_truncated,
                }
                for page in self.pages
            ],
            "status": self.status,
            "complete": self.complete,
            "pending_pages": list(self.pending_pages),
            "summary": f"File {self.file_name}: {self.message}",
        }


def page_outcome(number: int, row: dict[str, Any] | None) -> PageOutcome:
    """Decode one bounded saved page without treating missing rows as success.

    Args:
        number: Expected one-based page number.
        row: Last saved row after the adapter applies text limits.

    Returns:
        Pending, failed or extracted state with explicit text-limit errors.
    """
    if row is None or row["status"] not in {"extracted", "failed"}:
        return PageOutcome(number, "pending")
    if row["status"] == "failed":
        return PageOutcome(number, "failed", error=row["message"])
    message = row["message"] or ""
    cached = message == "cached" or message.startswith("cached:")
    extraction = {}
    error = None
    if row["truncated"]:
        error = "Extraction exceeds outcome text limit; use the archive page reader"
    else:
        try:
            extraction = json.loads(row["extraction"] or "{}")
            if not isinstance(extraction, dict):
                raise ValueError("Extraction is not an object")
        except (ValueError, TypeError):
            extraction = {}
            error = "Saved extraction is invalid"
    return PageOutcome(
        number,
        "cached" if cached else "extracted",
        extraction,
        message.partition(":")[2] or None if cached else None,
        error,
        bool(row["truncated"]),
    )


def aggregate_pages(pages: tuple[PageOutcome, ...]) -> tuple[str, str]:
    """Derive the existing file status from distinct expected page outcomes.

    Args:
        pages: Saved outcomes in manifest order, including missing pages.

    Returns:
        Existing file status and its human-readable summary.
    """
    pending = sum(page.status == "pending" for page in pages)
    successes = sum(page.status in {"extracted", "cached"} for page in pages)
    total = len(pages)
    if pending:
        return (
            "pending",
            f"{total - pending}/{total} page(s) finished; {pending} pending",
        )
    if successes == total and total:
        return "completed", f"All {total} page(s) extracted"
    if successes:
        return "partial", f"{successes}/{total} page(s) extracted"
    return "failed", "All pages failed"


@dataclass(frozen=True)
class DocumentPage:
    """Identify one bounded archived extraction segment."""

    file_id: int
    page: int
    offset: int = 0


@dataclass(frozen=True)
class FileUpload:
    """Describe a prepared upload without exposing storage queries."""

    session_id: int
    user_id: int
    file_type: str
    file_name: str
    total_pages: int
    status: str = "pending"
    status_message: str | None = None


@dataclass(frozen=True)
class UploadAttachment:
    """Carry decoded upload bytes and client metadata to document ingestion."""

    filename: str
    content_type: str
    data: bytes
