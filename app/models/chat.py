import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.models.attachment import FileAttachment, MetadataFile

_GMT8 = timezone(timedelta(hours=8))


def _now_gmt8() -> datetime:
    """Current time in GMT+8.

    If ``E2E_FREEZE_NOW`` is set (ISO-8601, e.g. ``2026-06-30T19:22:00``), that
    fixed instant is returned instead. Only the E2E harness sets it, so the
    system prompt's CURRENT DATE/TIME stays pinned to June across runs (keeping
    "today" consistent with the June-based docs/TABLE.md fixtures). Production
    never sets the var and always sees the real wall clock.
    """
    frozen = os.environ.get("E2E_FREEZE_NOW")
    if frozen:
        try:
            dt = datetime.fromisoformat(frozen)
            return (
                dt.replace(tzinfo=_GMT8) if dt.tzinfo is None else dt.astimezone(_GMT8)
            )
        except ValueError:
            pass
    return datetime.now(tz=_GMT8)


class KlaudiaMessage(BaseModel):
    role: str  # user | assistant | system
    content: str
    attachments: Optional[list[FileAttachment]] = None


class KlaudiaRequest(BaseModel):
    messages: list[KlaudiaMessage]
    session_id: Optional[int] = None  # None = create new session
    # Identity comes from the Bearer token (app.helpers.auth), never the body.
    user_name: str = "User"
    # Which of the user's spreadsheets to operate on (ledger backend).
    # None = the user's default spreadsheet, provisioned on first use.
    # Ownership is validated server-side; foreign ids 404.
    spreadsheet_id: Optional[str] = None
    request_key: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_request_key(self) -> "KlaudiaRequest":
        """Require a stable existing session for keyed text-turn retries.

        Returns:
            Validated request.

        Raises:
            ValueError: A retry key lacks a session or accompanies uploads.
        """
        if self.request_key is not None and (
            self.session_id is None
            or any(message.attachments for message in self.messages)
        ):
            raise ValueError(
                "request_key requires an existing session and a text-only turn"
            )
        return self


class ChatMetadata(BaseModel):
    """Per-turn metadata for personalization."""

    user_name: str = "User"
    date: str = Field(default_factory=lambda: _now_gmt8().strftime("%A, %d %B %Y"))
    time: str = Field(default_factory=lambda: _now_gmt8().strftime("%H:%M"))
    timezone: str = "GMT+8"


class ChatVariable(BaseModel):
    """Session file context injected into system prompt."""

    files: list[MetadataFile] = Field(default_factory=list)

    def format_context(self) -> str:
        if not self.files:
            return "No files in this session."
        lines: list[str] = []
        for i, f in enumerate(self.files, 1):
            lines.append(
                f"{i}. {f.file_name}\n"
                f"   - Type: {f.file_type}\n"
                f"   - Status: {f.status}\n"
                f"   - Pages: {f.total_pages}\n"
                f"   - File ID: {f.id}"
            )
        return "\n".join(lines)


class KlaudiaResponse(BaseModel):
    message: KlaudiaMessage
    session_id: int
    processing_time_ms: int
    tools_used: list[str] = Field(default_factory=list)
    metadata: ChatMetadata = Field(default_factory=ChatMetadata)
    # Irreversible operations the guard refused to run unattended. Each
    # entry backs an approve/reject button; POST /v1/approvals/{id}.
    pending_approvals: list[dict] = Field(default_factory=list)
    task_id: str | None = None
    runtime: str = "main"
    run_status: str | None = None
    operation_references: list[str] = Field(default_factory=list)
    operation_receipts: list[dict] = Field(default_factory=list)
