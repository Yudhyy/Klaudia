"""Keep each migration history within its owned object set."""

import importlib

import pytest


@pytest.mark.parametrize(
    ("module_name", "owned_tables"),
    [
        (
            "ledger.migrations.metadata",
            {
                "ledger_spreadsheet",
                "ledger_sheet",
                "ledger_operation",
                "ledger_resource",
                "ledger_table_operation",
                "ledger_typed_cell",
            },
        ),
        (
            "app.adapters.persistence.metadata",
            {
                "user",
                "session",
                "conversation",
                "metadata_file",
                "pages",
                "file_blob",
                "file_blob_page",
                "blob_extraction",
                "metadata_file_blob",
                "memory_document",
                "memory_document_revision",
                "workflow_task",
            },
        ),
    ],
)
def test_metadata_registers_the_complete_owned_set(module_name, owned_tables):
    """Register each owned table without importing the other history's metadata."""
    registered = importlib.import_module(module_name).metadata
    assert set(registered.tables) == owned_tables


@pytest.mark.parametrize("history_name", ["ledger", "application"])
def test_history_filters_exclude_other_tables(history_name):
    """Exclude foreign tables and both revision tables before reflection."""
    history = importlib.import_module("app.adapters.persistence.migrations").HISTORIES[
        history_name
    ]
    for table_name in {"unrelated", "app_alembic_version", "ledger_alembic_version"}:
        assert not history.include_name(table_name, "table", {"schema_name": None})
    for table_name in history.metadata.tables:
        assert history.include_name(table_name, "table", {"schema_name": None})


def test_standalone_ledger_has_no_application_foreign_keys():
    """Ledger metadata can create its tables without an application registry."""
    registered = importlib.import_module("ledger.migrations.metadata").metadata
    assert all(
        key.column.table.metadata is registered
        for table in registered.tables.values()
        for key in table.foreign_keys
    )


async def test_migration_connection_preserves_the_original_dsn(monkeypatch):
    """Retain asyncpg SSL and URL query options during the release connection."""
    from unittest.mock import AsyncMock, Mock
    from ledger.migrations import runtime

    database_url = "postgresql://fixture:encoded%40password@db.invalid/catalog?sslmode=require&application_name=migration-test"
    engine_factory = Mock(side_effect=RuntimeError("Stop before connection"))
    connect = AsyncMock()
    monkeypatch.setattr(runtime, "create_async_engine", engine_factory)
    monkeypatch.setattr(runtime.asyncpg, "connect", connect)
    with pytest.raises(RuntimeError, match="Stop before connection"):
        await runtime.upgrade_histories(database_url, ())
    await engine_factory.call_args.kwargs["async_creator"]()
    connect.assert_awaited_once_with(database_url)
