import base64
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class FileAttachment(BaseModel):
    """Attachment sent by the user in a chat message."""

    filename: str
    content_type: str  # e.g. "application/pdf", "image/png"
    data: bytes  # raw file bytes (base64-decoded)

    @field_validator("data", mode="before")
    @classmethod
    def decode_base64(cls, v: object) -> bytes:
        if isinstance(v, str):
            # Strip optional data URI prefix (e.g. "data:image/jpeg;base64,")
            if "," in v and v.startswith("data:"):
                v = v.split(",", 1)[1]
            return base64.b64decode(v)
        return v


class MetadataFile(BaseModel):
    """Persisted file metadata from the database."""

    id: int
    session_id: int
    user_id: int
    file_type: str  # "pdf" | "image"
    total_pages: int
    file_name: str
    status: str  # "completed" | "partial" | "failed"
    status_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
