"""Bounded contracts for explicit human context edits."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from app.services.memory.policy import ReconciliationPolicy

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_DOCUMENT_BYTES = 8192
MAX_EXPECTED_REVISION = 2**63 - 2


class DocumentPath(StrEnum):
    """Allow only the supported context documents."""

    PREFERENCES = "/preferences.md"
    CONVENTIONS = "/conventions.md"
    ACCOUNTING_POLICY = "/accounting-policy.md"


class DocumentEdit(BaseModel):
    """Accept content and an untrusted source note, never author identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_revision: int = Field(ge=0, le=MAX_EXPECTED_REVISION, strict=True)
    content: str
    policy: ReconciliationPolicy | None = None
    source_note: str = Field(default="", max_length=512)

    @field_validator("source_note")
    @classmethod
    def validate_source_note(cls, source_note: str) -> str:
        """Reject source notes that PostgreSQL cannot store.

        Args:
            source_note: Untrusted description supplied by the editor.

        Returns:
            The unchanged note.

        Raises:
            ValueError: The note contains a null byte.
        """
        if "\x00" in source_note:
            raise ValueError("Source note must not contain null bytes")
        return source_note

    @field_validator("content")
    @classmethod
    def bound_content(cls, content: str) -> str:
        """Limit stored and agent-visible text by UTF-8 bytes.

        Args:
            content: Human-authored document text.

        Returns:
            The unchanged text.

        Raises:
            ValueError: Text exceeds the byte budget or contains a null byte.
        """
        if "\x00" in content or len(content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValueError(
                "Document must contain at most 8192 UTF-8 bytes and no null bytes"
            )
        return content


class MemoryDocument(BaseModel):
    """Represent a current revision, tombstone, or absent document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: DocumentPath
    revision: int = 0
    status: Literal["missing", "active", "deleted"] = "missing"
    content: str | None = None
    policy: ReconciliationPolicy | None = None
    actor_id: int | None = None
    updated_at: datetime | None = None
    source_note: str = ""
