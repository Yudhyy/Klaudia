"""Explicit SQLAlchemy registration for this history's owned tables."""

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Date,
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
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

metadata = MetaData()

Table(
    "ledger_operation",
    metadata,
    Column("workspace", Text, nullable=False),
    Column("idempotency_key", Text, nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("receipt", JSONB, nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    PrimaryKeyConstraint("workspace", "idempotency_key", name="ledger_operation_pkey"),
)

Table(
    "ledger_resource",
    metadata,
    Column("resource_id", Text, nullable=False),
    Column("sheet_id", Integer, nullable=False),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("grain", Text, nullable=False),
    Column("aliases", ARRAY(Text), nullable=False),
    Column("alias_keys", ARRAY(Text), nullable=False),
    Column("entity", Text, nullable=True),
    Column("period_start", Date, nullable=True),
    Column("period_end", Date, nullable=True),
    Column("table_range", Text, nullable=False),
    Column("first_row", Integer, nullable=False),
    Column("first_column", Integer, nullable=False),
    Column("last_row", Integer, nullable=False),
    Column("last_column", Integer, nullable=False),
    Column("columns", JSONB, nullable=False),
    Column("column_keys", ARRAY(Text), nullable=False),
    Column("record_count", Integer, nullable=False),
    Column("source_revision", BigInteger, nullable=False),
    Column("revision", BigInteger, nullable=False, server_default=text("1")),
    Column("search_text", Text, nullable=False),
    Column(
        "search_vector",
        TSVECTOR,
        Computed("to_tsvector('simple'::regconfig, search_text)", persisted=True),
        nullable=True,
    ),
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
    PrimaryKeyConstraint("resource_id", name="ledger_resource_pkey"),
    ForeignKeyConstraint(
        ["sheet_id"],
        ["ledger_sheet.sheet_id"],
        name="ledger_resource_sheet_id_fkey",
        ondelete="CASCADE",
    ),
    UniqueConstraint(
        "sheet_id", "table_range", name="ledger_resource_sheet_id_table_range_key"
    ),
)

Table(
    "ledger_sheet",
    metadata,
    Column("sheet_id", Integer, Identity(always=False), nullable=False),
    Column("workspace", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("position", Integer, nullable=False, server_default=text("0")),
    Column("grid", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
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
    Column("revision", BigInteger, nullable=False, server_default=text("0")),
    PrimaryKeyConstraint("sheet_id", name="ledger_sheet_pkey"),
    ForeignKeyConstraint(
        ["workspace"],
        ["ledger_spreadsheet.spreadsheet_id"],
        name="ledger_sheet_workspace_fkey",
        ondelete="CASCADE",
    ),
    UniqueConstraint("workspace", "title", name="ledger_sheet_workspace_title_key"),
)

Table(
    "ledger_spreadsheet",
    metadata,
    Column("spreadsheet_id", Text, nullable=False),
    Column("user_id", Integer, nullable=False),
    Column("name", Text, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    PrimaryKeyConstraint("spreadsheet_id", name="ledger_spreadsheet_pkey"),
    UniqueConstraint("user_id", "name", name="ledger_spreadsheet_user_id_name_key"),
)

Table(
    "ledger_table_operation",
    metadata,
    Column("user_id", Integer, nullable=False),
    Column("idempotency_key", Text, nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("workspace", Text, nullable=True),
    Column("receipt", JSONB, nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("request_payload", Text, nullable=True),
    Column("approval_required", Boolean, nullable=False, server_default=text("false")),
    Column("approval_id", Text, nullable=True),
    Column("approval_status", Text, nullable=True),
    Column("approval_expires_at", DateTime(timezone=True), nullable=True),
    Column("approved_fingerprint", Text, nullable=True),
    PrimaryKeyConstraint(
        "user_id", "idempotency_key", name="ledger_table_operation_pkey"
    ),
)

Table(
    "ledger_typed_cell",
    metadata,
    Column("cell_id", Text, nullable=False),
    Column("sheet_id", Integer, nullable=False),
    Column("row_number", Integer, nullable=False),
    Column("column_number", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    Column("raw_value", JSONB, nullable=True),
    Column("unit", Text, nullable=True),
    Column("display_format", Text, nullable=True),
    Column("expression", JSONB, nullable=True),
    Column(
        "dependencies", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    ),
    Column("calculated_value", Text, nullable=True),
    Column("calculation_status", Text, nullable=False),
    Column("calculation_error", Text, nullable=True),
    Column("engine_version", Text, nullable=True),
    CheckConstraint(
        "(calculation_status = ANY (ARRAY['input'::text, 'pending'::text, 'current'::text, 'failed'::text]))",
        name="ledger_typed_cell_calculation_status_check",
    ),
    CheckConstraint(
        "(column_number >= 1 AND column_number <= 256)",
        name="ledger_typed_cell_column_number_check",
    ),
    CheckConstraint(
        "(kind = ANY (ARRAY['decimal'::text, 'text'::text, 'boolean'::text, 'date'::text]))",
        name="ledger_typed_cell_kind_check",
    ),
    PrimaryKeyConstraint("cell_id", name="ledger_typed_cell_pkey"),
    CheckConstraint(
        "(row_number >= 1 AND row_number <= 1000)",
        name="ledger_typed_cell_row_number_check",
    ),
    ForeignKeyConstraint(
        ["sheet_id"],
        ["ledger_sheet.sheet_id"],
        name="ledger_typed_cell_sheet_id_fkey",
        ondelete="CASCADE",
    ),
    UniqueConstraint(
        "sheet_id",
        "row_number",
        "column_number",
        name="ledger_typed_cell_sheet_id_row_number_column_number_key",
    ),
)

Index(
    "ledger_resource_columns",
    metadata.tables["ledger_resource"].c["column_keys"],
    postgresql_using="gin",
)
Index(
    "ledger_resource_search",
    metadata.tables["ledger_resource"].c["search_vector"],
    postgresql_using="gin",
)
Index(
    "ledger_resource_trigrams",
    metadata.tables["ledger_resource"].c["search_text"],
    postgresql_using="gin",
    postgresql_ops={"search_text": "gin_trgm_ops"},
)
Index(
    "ledger_table_approval_id",
    metadata.tables["ledger_table_operation"].c["approval_id"],
    unique=True,
    postgresql_where=text("approval_id IS NOT NULL"),
)
