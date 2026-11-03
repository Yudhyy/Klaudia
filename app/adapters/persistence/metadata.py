"""Explicit SQLAlchemy registration for this history's owned tables."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

Table(
    "blob_extraction",
    metadata,
    Column("user_id", Integer, nullable=False),
    Column("page_blake3", Text, nullable=False),
    Column("extraction_json", Text, nullable=False),
    Column("ocr_model", Text, nullable=True),
    Column("schema_version", Text, nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    PrimaryKeyConstraint("user_id", "page_blake3", name="blob_extraction_pkey"),
)

Table(
    "conversation",
    metadata,
    Column("message_id", Text, nullable=False),
    Column("session_id", Integer, nullable=False),
    Column("user_id", Integer, nullable=False),
    Column("sender", Text, nullable=False),
    Column("message_text", Text, nullable=False),
    Column("file_id", Integer, nullable=True),
    Column(
        "timestamp",
        DateTime(timezone=True),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    ForeignKeyConstraint(
        ["file_id"], ["metadata_file.id"], name="conversation_file_id_fkey"
    ),
    PrimaryKeyConstraint("message_id", name="conversation_pkey"),
    ForeignKeyConstraint(
        ["session_id"], ["session.session_id"], name="conversation_session_id_fkey"
    ),
    ForeignKeyConstraint(
        ["user_id"], ["user.user_id"], name="conversation_user_id_fkey"
    ),
)

Table(
    "file_blob",
    metadata,
    Column("blob_id", Integer, Identity(always=False), nullable=False),
    Column("user_id", Integer, nullable=False),
    Column("blake3", Text, nullable=False),
    Column("minio_key", Text, nullable=False),
    Column("content_type", Text, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("page_count", Integer, nullable=False, server_default=text("1")),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    PrimaryKeyConstraint("blob_id", name="file_blob_pkey"),
    UniqueConstraint("user_id", "blake3", name="file_blob_user_id_blake3_key"),
)

Table(
    "file_blob_page",
    metadata,
    Column("blob_id", Integer, nullable=False),
    Column("page", Integer, nullable=False),
    Column("page_blake3", Text, nullable=False),
    Column("page_minio_key", Text, nullable=False),
    ForeignKeyConstraint(
        ["blob_id"], ["file_blob.blob_id"], name="file_blob_page_blob_id_fkey"
    ),
    PrimaryKeyConstraint("blob_id", "page", name="file_blob_page_pkey"),
)

Table(
    "memory_document",
    metadata,
    Column("user_id", Integer, nullable=False),
    Column("path", Text, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("status", Text, nullable=False),
    Column("content", Text, nullable=True),
    Column("actor_id", Integer, nullable=False),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("source_note", Text, nullable=False),
    Column("policy", JSONB, nullable=True),
    CheckConstraint("(actor_id = user_id)", name="memory_document_check"),
    CheckConstraint(
        "(status = 'active'::text AND content IS NOT NULL OR status = 'deleted'::text AND content IS NULL)",
        name="memory_document_check1",
    ),
    CheckConstraint(
        "(octet_length(content) <= 8192)", name="memory_document_content_check"
    ),
    CheckConstraint(
        "(path = ANY (ARRAY['/preferences.md'::text, '/conventions.md'::text, '/accounting-policy.md'::text]))",
        name="memory_document_path_check",
    ),
    PrimaryKeyConstraint("user_id", "path", name="memory_document_pkey"),
    CheckConstraint("(revision > 0)", name="memory_document_revision_check"),
    CheckConstraint(
        "(char_length(source_note) <= 512)", name="memory_document_source_note_check"
    ),
    CheckConstraint(
        "(status = ANY (ARRAY['active'::text, 'deleted'::text]))",
        name="memory_document_status_check",
    ),
    ForeignKeyConstraint(
        ["user_id"],
        ["user.user_id"],
        name="memory_document_user_id_fkey",
        ondelete="CASCADE",
    ),
)

Table(
    "memory_document_revision",
    metadata,
    Column("user_id", Integer, nullable=False),
    Column("path", Text, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("status", Text, nullable=False),
    Column("content", Text, nullable=True),
    Column("actor_id", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("source_note", Text, nullable=False),
    Column("policy", JSONB, nullable=True),
    PrimaryKeyConstraint(
        "user_id", "path", "revision", name="memory_document_revision_pkey"
    ),
    ForeignKeyConstraint(
        ["user_id", "path"],
        ["memory_document.user_id", "memory_document.path"],
        name="memory_document_revision_user_id_path_fkey",
        ondelete="CASCADE",
    ),
)

Table(
    "metadata_file",
    metadata,
    Column("id", Integer, Identity(always=False), nullable=False),
    Column("session_id", Integer, nullable=False),
    Column("user_id", Integer, nullable=False),
    Column("type", Text, nullable=False),
    Column("total_pages", Integer, nullable=False, server_default=text("0")),
    Column("file_name", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'pending'::text")),
    Column("status_message", Text, nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    PrimaryKeyConstraint("id", name="metadata_file_pkey"),
    ForeignKeyConstraint(
        ["session_id"], ["session.session_id"], name="metadata_file_session_id_fkey"
    ),
    ForeignKeyConstraint(
        ["user_id"], ["user.user_id"], name="metadata_file_user_id_fkey"
    ),
)

Table(
    "metadata_file_blob",
    metadata,
    Column("metadata_file_id", Integer, nullable=False),
    Column("blob_id", Integer, nullable=False),
    ForeignKeyConstraint(
        ["blob_id"], ["file_blob.blob_id"], name="metadata_file_blob_blob_id_fkey"
    ),
    PrimaryKeyConstraint("metadata_file_id", name="metadata_file_blob_pkey"),
)

Table(
    "pages",
    metadata,
    Column("id", Integer, Identity(always=False), nullable=False),
    Column("metadata_file_id", Integer, nullable=False),
    Column("page", Integer, nullable=False),
    Column("agent_extracted", Text, nullable=True),
    Column("status", Text, nullable=False, server_default=text("'pending'::text")),
    Column("status_message", Text, nullable=True),
    ForeignKeyConstraint(
        ["metadata_file_id"], ["metadata_file.id"], name="pages_metadata_file_id_fkey"
    ),
    PrimaryKeyConstraint("id", name="pages_pkey"),
)

Table(
    "session",
    metadata,
    Column("session_id", Integer, Identity(always=False), nullable=False),
    Column("user_id", Integer, nullable=False),
    Column("session_name", Text, nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    PrimaryKeyConstraint("session_id", name="session_pkey"),
    ForeignKeyConstraint(["user_id"], ["user.user_id"], name="session_user_id_fkey"),
)

Table(
    "user",
    metadata,
    Column("user_id", Integer, Identity(always=False), nullable=False),
    Column("username", Text, nullable=False),
    Column("email", Text, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("last_login", DateTime(timezone=True), nullable=True),
    UniqueConstraint("email", name="user_email_key"),
    PrimaryKeyConstraint("user_id", name="user_pkey"),
    UniqueConstraint("username", name="user_username_key"),
)

Table(
    "workflow_task",
    metadata,
    Column("task_id", Text, nullable=False),
    Column("user_id", Integer, nullable=False),
    Column("session_id", Integer, nullable=False),
    Column("request_key", Text, nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("input_payload", Text, nullable=False),
    Column("checkpoint", Text, nullable=True),
    Column("status", Text, nullable=False, server_default=text("'ready'::text")),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    PrimaryKeyConstraint("task_id", name="workflow_task_pkey"),
    ForeignKeyConstraint(
        ["session_id"],
        ["session.session_id"],
        name="workflow_task_session_id_fkey",
        ondelete="CASCADE",
    ),
    UniqueConstraint(
        "user_id",
        "session_id",
        "request_key",
        name="workflow_task_user_id_session_id_request_key_key",
    ),
)

Index("idx_file_blob_page_hash", metadata.tables["file_blob_page"].c["page_blake3"])
Index(
    "workflow_task_session",
    metadata.tables["workflow_task"].c["user_id"],
    metadata.tables["workflow_task"].c["session_id"],
)
