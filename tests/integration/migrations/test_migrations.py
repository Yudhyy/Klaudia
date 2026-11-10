"""Exercise release setup and adoption in newly created PostgreSQL databases."""

import importlib

import asyncpg
import pytest


async def test_task_effect_and_receipt_use_the_lock_connection(migration_database):
    """Trace checkpoint, effect and receipt writes to the backend holding the task lock."""
    import json

    from app.adapters.persistence.migrations import upgrade_database
    from app.services.workflow.store import TaskStore
    from ledger.store import LedgerStore
    from ledger_core.append_contracts import TableAppendProposal
    from tests.e2e.capability_cases import seeded_capability_case

    database_url, connection = migration_database
    await upgrade_database(database_url)
    store = LedgerStore(database_url)
    tasks = TaskStore(database_url)
    await store.connect_existing()
    await tasks.connect()
    try:
        session_id = await connection.fetchval(
            "INSERT INTO session (user_id) VALUES (1) RETURNING session_id"
        )
        async with seeded_capability_case(store, "append", user_id=1) as fixture:
            table_id = await connection.fetchval(
                "SELECT resource_id FROM ledger_resource WHERE sheet_id IN (SELECT sheet_id FROM ledger_sheet WHERE workspace=$1)",
                fixture.workbook_id,
            )
            task_id = await tasks.create(
                (1, session_id),
                {
                    "turn": {
                        "text": "Append once",
                        "active_workbook_id": fixture.workbook_id,
                        "document_ids": [],
                        "request_key": "connection-probe",
                    }
                },
            )
            await connection.execute("""
                CREATE TABLE migration_connection_probe (pid INTEGER, table_name TEXT);
                CREATE FUNCTION trace_migration_connection() RETURNS trigger AS $$
                BEGIN INSERT INTO migration_connection_probe VALUES (pg_backend_pid(), TG_TABLE_NAME); RETURN NEW; END;
                $$ LANGUAGE plpgsql;
                CREATE TRIGGER trace_sheet AFTER UPDATE ON ledger_sheet FOR EACH ROW EXECUTE FUNCTION trace_migration_connection();
                CREATE TRIGGER trace_receipt AFTER INSERT OR UPDATE ON ledger_table_operation FOR EACH ROW EXECUTE FUNCTION trace_migration_connection();
                CREATE TRIGGER trace_checkpoint AFTER UPDATE ON workflow_task FOR EACH ROW EXECUTE FUNCTION trace_migration_connection();
            """)
            async with tasks.open(1, task_id) as task:
                backend_pid = await task.connection.fetchval("SELECT pg_backend_pid()")
                assert not await connection.fetchval(
                    "SELECT pg_try_advisory_lock(hashtextextended($1,0))", task_id
                )
                proposal = TableAppendProposal(
                    table_id=table_id,
                    expected_sheet_revision=0,
                    expected_catalogue_revision=1,
                    records=[
                        {
                            "Date": "2026-06-30",
                            "Merchant": "Probe",
                            "Category": "Travel",
                            "Amount": 25,
                        }
                    ],
                )
                prepared = await task.operations.prepare(1, proposal)
                await task.save({"pending_calls": []})
                receipt = await task.operations.execute(1, prepared["operation_ref"])
                assert (
                    await task.operations.execute(1, prepared["operation_ref"])
                    == receipt
                )
            observed = await connection.fetch(
                "SELECT * FROM migration_connection_probe"
            )
            assert {row["pid"] for row in observed} == {backend_pid}
            assert {row["table_name"] for row in observed} == {
                "ledger_sheet",
                "ledger_table_operation",
                "workflow_task",
            }
            assert (
                json.loads(
                    await connection.fetchval(
                        "SELECT receipt FROM ledger_table_operation WHERE idempotency_key=$1",
                        prepared["operation_ref"],
                    )
                )
                == receipt
            )
    finally:
        await tasks.close()
        await store.close()


async def test_migration_lock_spans_history_commits(migration_database, monkeypatch):
    """Keep exclusion active after ledger commits and before application setup."""
    from sqlalchemy.util import await_only
    from ledger_postgres.migrations import runtime
    from app.adapters.persistence.migrations import upgrade_database

    database_url, connection = migration_database
    configuration = runtime.migration_config
    lock_observations = []

    def inspect_lock(migration_connection, history):
        """Try the same lock from another session at the history boundary."""
        if history.name == "application":
            lock_observations.append(
                await_only(
                    connection.fetchval(
                        "SELECT pg_try_advisory_lock($1)", runtime.MIGRATION_LOCK
                    )
                )
            )
        return configuration(migration_connection, history)

    monkeypatch.setattr(runtime, "migration_config", inspect_lock)
    await upgrade_database(database_url)
    assert lock_observations == [False]


async def test_domain_backed_column_cannot_be_adopted(migration_database):
    """Reject a domain that adds an unrecorded approval-deadline constraint."""
    from app.adapters.persistence.migrations import HISTORIES, upgrade_database

    database_url, connection = migration_database
    for history in HISTORIES.values():
        await connection.execute(
            (history.directory / f"{history.revision}.sql").read_text()
        )
    await connection.execute(
        "CREATE DOMAIN migration_deadline AS TIMESTAMPTZ CHECK (VALUE > '2000-01-01'::timestamptz); ALTER TABLE ledger_table_operation ALTER COLUMN approval_expires_at TYPE migration_deadline"
    )
    with pytest.raises(RuntimeError, match="layout"):
        await upgrade_database(database_url)
    assert (
        await connection.fetchval("SELECT to_regclass('public.ledger_alembic_version')")
        is None
    )


@pytest.mark.parametrize("process_name", ["worker", "mcp"])
async def test_processes_reject_missing_release_revisions(
    migration_database, monkeypatch, process_name
):
    """Stop worker and integrated MCP before creating any application objects."""
    database_url, connection = migration_database
    with pytest.raises(RuntimeError, match="migration"):
        if process_name == "worker":
            from app.services.extraction.queue import state
            from config.settings import Settings

            monkeypatch.setattr(
                state,
                "get_settings",
                lambda: Settings(_env_file=None, DATABASE_URL=database_url),
            )
            await state._startup(None)
        else:
            from ledger.server import ledger_lifespan

            monkeypatch.setenv("DATABASE_URL", database_url)
            monkeypatch.setenv("LEDGER_SCHEMA_MODE", "check")
            async with ledger_lifespan(None):
                pytest.fail("Integrated MCP started without release revisions")
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
        )
        == 0
    )


async def test_blank_integrated_setup_and_repeat(migration_database):
    """Install both histories and retain both fixed revisions on repeat runs."""
    migrations = importlib.import_module("app.adapters.persistence.migrations")
    database_url, connection = migration_database
    await migrations.upgrade_database(database_url)
    first_tables = await connection.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )
    await migrations.upgrade_database(database_url)
    assert (
        await connection.fetchval("SELECT version_num FROM ledger_alembic_version")
        == migrations.HISTORIES["ledger"].revision
    )
    assert (
        await connection.fetchval("SELECT version_num FROM app_alembic_version")
        == migrations.HISTORIES["application"].revision
    )
    assert len(first_tables) == 20
    assert (
        await connection.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        )
        == first_tables
    )
    assert (
        await connection.fetchval('SELECT username FROM "user" WHERE user_id=1')
        == "dev"
    )


async def test_ledger_only_then_application_preserves_rows(migration_database):
    """Add application objects without replacing ledger rows or their revision."""
    migrations = importlib.import_module("app.adapters.persistence.migrations")
    ledger_migrations = importlib.import_module("ledger.migrations")
    database_url, connection = migration_database
    await ledger_migrations.upgrade_ledger(database_url)
    assert await connection.fetchval("SELECT to_regclass('public.session')") is None
    await connection.execute(
        "INSERT INTO ledger_spreadsheet (spreadsheet_id,user_id,name) VALUES ('retained',5,'Retained')"
    )
    await connection.execute(
        "INSERT INTO ledger_sheet (workspace,title,grid) VALUES ('retained','Claims','[[123]]')"
    )
    before = await connection.fetch("SELECT * FROM ledger_sheet")
    revision = await connection.fetchval(
        "SELECT version_num FROM ledger_alembic_version"
    )
    await migrations.upgrade_database(database_url)
    assert await connection.fetch("SELECT * FROM ledger_sheet") == before
    assert (
        await connection.fetchval("SELECT version_num FROM ledger_alembic_version")
        == revision
    )


async def test_partial_application_fails_before_either_stamp(migration_database):
    """Reject incomplete application storage before creating either history."""
    migrations = importlib.import_module("app.adapters.persistence.migrations")
    database_url, connection = migration_database
    await connection.execute('CREATE TABLE "user" (user_id INTEGER PRIMARY KEY)')
    with pytest.raises(RuntimeError, match="layout"):
        await migrations.upgrade_database(database_url)
    assert (
        await connection.fetchval("SELECT to_regclass('public.ledger_alembic_version')")
        is None
    )
    assert (
        await connection.fetchval("SELECT to_regclass('public.app_alembic_version')")
        is None
    )


async def test_startup_rejects_unmigrated_database(migration_database):
    """Require the release step before application pools can start."""
    from app.services.extraction.infra.db_client import AppDBClient
    from config.settings import Settings

    database_url, connection = migration_database
    database = AppDBClient(Settings(_env_file=None, DATABASE_URL=database_url))
    try:
        with pytest.raises(RuntimeError, match="migration"):
            await database.connect()
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
            )
            == 0
        )
    finally:
        await database.close()


@pytest.mark.parametrize(
    "persistence_state", ["pending_approval", "committed_before_checkpoint"]
)
async def test_existing_records_and_legacy_objects_survive_adoption(
    migration_database, persistence_state
):
    """Adopt installed layouts without rewriting credentials, tasks or receipts."""
    import json

    from tests.agent_compatibility import read_fixture

    migrations = importlib.import_module("app.adapters.persistence.migrations")
    database_url, connection = migration_database
    for history in migrations.HISTORIES.values():
        await connection.execute(
            (history.directory / f"{history.revision}.sql").read_text()
        )
    await connection.execute(
        "ALTER TABLE ledger_table_operation ADD COLUMN request JSONB"
    )
    await connection.execute(
        "CREATE TABLE pending_approval (approval_id TEXT PRIMARY KEY)"
    )
    await connection.execute("CREATE TABLE memory_history (id UUID PRIMARY KEY)")
    await connection.execute(
        "CREATE TABLE memory_message (sequence BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY)"
    )
    saved = read_fixture("continuation.json")
    owner = saved["context"]["user_id"]
    await connection.execute(
        "INSERT INTO \"user\" (user_id,username,email,password_hash) VALUES ($1,'retained','retained@example.invalid','unchanged-hash')",
        owner,
    )
    await connection.execute(
        "INSERT INTO session (session_id,user_id) VALUES ($1,$2)",
        saved["session"]["session_id"],
        owner,
    )
    file_id = await connection.fetchval(
        "INSERT INTO metadata_file (session_id,user_id,type,total_pages,file_name,status) VALUES ($1,$2,'pdf',1,'retained.pdf','completed') RETURNING id",
        saved["session"]["session_id"],
        owner,
    )
    await connection.execute(
        "INSERT INTO pages (metadata_file_id,page,agent_extracted,status) VALUES ($1,1,'retained extraction','completed')",
        file_id,
    )
    blob_id = await connection.fetchval(
        "INSERT INTO file_blob (user_id,blake3,minio_key,content_type,size_bytes) VALUES ($1,'retained-blob','retained/source.pdf','application/pdf',1024) RETURNING blob_id",
        owner,
    )
    await connection.execute(
        "INSERT INTO file_blob_page (blob_id,page,page_blake3,page_minio_key) VALUES ($1,1,'retained-page','retained/page.png')",
        blob_id,
    )
    await connection.execute(
        "INSERT INTO metadata_file_blob (metadata_file_id,blob_id) VALUES ($1,$2)",
        file_id,
        blob_id,
    )
    await connection.execute(
        "INSERT INTO blob_extraction (user_id,page_blake3,extraction_json) VALUES ($1,'retained-page','{\"total\":\"45.00\"}')",
        owner,
    )
    await connection.execute(
        "INSERT INTO conversation (message_id,session_id,user_id,sender,message_text,file_id) VALUES ('retained-message',$1,$2,'assistant','Retained reply',$3)",
        saved["session"]["session_id"],
        owner,
        file_id,
    )
    await connection.execute(
        "INSERT INTO memory_document (user_id,path,revision,status,content,actor_id,source_note) VALUES ($1,'/preferences.md',2,'active','Keep exact amounts.',$1,'Retained source')",
        owner,
    )
    await connection.execute(
        "INSERT INTO memory_document_revision (user_id,path,revision,status,content,actor_id,updated_at,source_note) SELECT user_id,path,revision,status,content,actor_id,updated_at,source_note FROM memory_document WHERE user_id=$1",
        owner,
    )
    for table_name, rows in saved["snapshots"][persistence_state].items():
        for row in rows:
            columns = ", ".join(f'"{name}"' for name in row)
            await connection.execute(
                f"INSERT INTO {table_name} ({columns}) SELECT {columns} FROM jsonb_populate_record(NULL::{table_name}, $1::jsonb)",
                json.dumps(row),
            )
    tables = [
        row["tablename"]
        for row in await connection.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        )
    ]
    before = {
        table: await connection.fetch(
            f'SELECT to_jsonb(t) AS row FROM "{table}" t ORDER BY to_jsonb(t)::text'
        )
        for table in tables
    }
    await migrations.upgrade_database(database_url)
    after = {
        table: await connection.fetch(
            f'SELECT to_jsonb(t) AS row FROM "{table}" t ORDER BY to_jsonb(t)::text'
        )
        for table in tables
    }
    assert before == after
    assert (
        await connection.fetchval(
            "SELECT request_payload FROM ledger_table_operation WHERE idempotency_key=$1",
            saved["operation_ref"],
        )
        == saved["snapshots"][persistence_state]["ledger_table_operation"][0][
            "request_payload"
        ]
    )


async def test_adopts_supplied_pre_revision_ledger_fixture(migration_database):
    """Keep the old grid and identity while adopting its system-owned workbook."""
    from ledger.migrations import upgrade_ledger

    database_url, connection = migration_database
    await connection.execute("""
        CREATE TABLE ledger_sheet (
            sheet_id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            workspace TEXT NOT NULL, title TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0, grid JSONB NOT NULL DEFAULT '[]',
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (workspace,title)
        );
        INSERT INTO ledger_sheet (workspace,title,grid) VALUES ('legacy','Claims','[[123]]');
    """)
    await upgrade_ledger(database_url)
    assert dict(
        await connection.fetchrow("SELECT sheet_id,grid,revision FROM ledger_sheet")
    ) == {"sheet_id": 1, "grid": "[[123]]", "revision": 0}
    assert (
        await connection.fetchval(
            "SELECT user_id FROM ledger_spreadsheet WHERE spreadsheet_id='legacy'"
        )
        == 0
    )
    await upgrade_ledger(database_url)
    assert await connection.fetchval("SELECT revision FROM ledger_sheet") == 0


async def test_runner_rejects_contending_session(migration_database):
    """Keep a second runner from creating or stamping any objects."""
    from ledger.migrations.runtime import MIGRATION_LOCK
    from app.adapters.persistence.migrations import upgrade_database

    database_url, connection = migration_database
    await connection.fetchval("SELECT pg_advisory_lock($1)", MIGRATION_LOCK)
    try:
        with pytest.raises(RuntimeError, match="busy"):
            await upgrade_database(database_url)
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
            )
            == 0
        )
    finally:
        await connection.fetchval("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK)
    await upgrade_database(database_url)


async def test_interrupted_application_revision_can_resume(
    migration_database, monkeypatch
):
    """Roll back an interrupted app baseline and retain the committed ledger head."""
    from ledger_postgres.migrations import runtime
    from app.adapters.persistence.migrations import upgrade_database

    database_url, connection = migration_database
    execute_baseline = runtime.execute_baseline

    def fail_application(migration_connection, sql):
        """Raise after application DDL to test transaction recovery."""
        execute_baseline(migration_connection, sql)
        if "workflow_task" in sql:
            raise RuntimeError("Simulated process interruption")

    monkeypatch.setattr(runtime, "execute_baseline", fail_application)
    with pytest.raises(RuntimeError, match="interruption"):
        await upgrade_database(database_url)
    assert (
        await connection.fetchval("SELECT version_num FROM ledger_alembic_version")
        == "ledger_0001"
    )
    assert (
        await connection.fetchval("SELECT to_regclass('public.workflow_task')") is None
    )
    assert (
        await connection.fetchval("SELECT to_regclass('public.app_alembic_version')")
        is None
    )
    monkeypatch.setattr(runtime, "execute_baseline", execute_baseline)
    await upgrade_database(database_url)
    assert (
        await connection.fetchval("SELECT version_num FROM app_alembic_version")
        == "app_0001"
    )


async def test_invalid_nontransactional_index_blocks_adoption(migration_database):
    """Detect a failed concurrent index before a retry can stamp the baseline."""
    from ledger.migrations import LEDGER_HISTORY, upgrade_ledger

    database_url, connection = migration_database
    await connection.execute((LEDGER_HISTORY.directory / "ledger_0001.sql").read_text())
    await connection.execute(
        "INSERT INTO ledger_spreadsheet (spreadsheet_id,user_id,name) VALUES ('duplicate',3,'Duplicate')"
    )
    await connection.execute(
        "INSERT INTO ledger_sheet (workspace,title) VALUES ('duplicate','First'),('duplicate','Second')"
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await connection.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY incomplete_index ON ledger_sheet(workspace)"
        )
    assert not await connection.fetchval(
        "SELECT indisvalid FROM pg_index WHERE indexrelid='incomplete_index'::regclass"
    )
    with pytest.raises(RuntimeError, match="layout"):
        await upgrade_ledger(database_url)
    assert (
        await connection.fetchval("SELECT to_regclass('public.ledger_alembic_version')")
        is None
    )
    await connection.execute("DROP INDEX incomplete_index")
    await upgrade_ledger(database_url)


async def test_later_revision_runs_once(migration_database, tmp_path):
    """Apply a new revision to an installed baseline without replaying it."""
    import json
    import shutil
    from dataclasses import replace

    from ledger.migrations import LEDGER_HISTORY, upgrade_ledger
    from ledger.migrations.runtime import upgrade_histories

    database_url, connection = migration_database
    await upgrade_ledger(database_url)
    directory = tmp_path / "ledger_history"
    shutil.copytree(LEDGER_HISTORY.directory, directory)
    (
        directory / "versions" / "ledger_0002.py"
    ).write_text('''"""Add a test-only nullable column."""
from alembic import op
import sqlalchemy as sa
revision = "ledger_0002"
down_revision = "ledger_0001"
def upgrade():
    """Apply the next test revision."""
    op.add_column("ledger_sheet", sa.Column("notes", sa.Text()))
def downgrade():
    """Remove the test column."""
    op.drop_column("ledger_sheet", "notes")
''')
    expected = LEDGER_HISTORY.baseline()
    notes = dict(
        next(
            column
            for column in expected["columns"]
            if column["table_name"] == "ledger_sheet"
            and column["column_name"] == "title"
        )
    )
    notes.update(column_name="notes", is_nullable="YES")
    expected["columns"].append(notes)
    (directory / "ledger_0002.json").write_text(json.dumps(expected))
    next_history = replace(LEDGER_HISTORY, directory=directory, revision="ledger_0002")
    await upgrade_histories(database_url, (next_history,))
    await connection.execute(
        "INSERT INTO ledger_spreadsheet (spreadsheet_id,user_id,name) VALUES ('next',8,'Next')"
    )
    await connection.execute(
        "INSERT INTO ledger_sheet (workspace,title,notes) VALUES ('next','Claims','Retained')"
    )
    await upgrade_histories(database_url, (next_history,))
    assert await connection.fetchval("SELECT notes FROM ledger_sheet") == "Retained"
    assert (
        await connection.fetchval("SELECT version_num FROM ledger_alembic_version")
        == "ledger_0002"
    )


async def test_autogenerate_preserves_other_history_and_legacy_objects(
    migration_database,
):
    """Generate an empty diff while both histories and retained objects coexist."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import make_url
    from app.adapters.persistence.migrations import HISTORIES, upgrade_database

    database_url, connection = migration_database
    await upgrade_database(database_url)
    await connection.execute("CREATE TABLE memory_history (id UUID PRIMARY KEY)")
    await connection.execute(
        "ALTER TABLE ledger_table_operation ADD COLUMN request JSONB"
    )

    def compare_owned(migration_connection, history):
        """Compare one owner's registered metadata with its filtered objects."""
        migration_context = MigrationContext.configure(
            migration_connection,
            opts={
                "include_name": history.include_name,
                "include_object": history.include_object,
                "compare_server_default": True,
            },
        )
        return compare_metadata(migration_context, history.metadata)

    engine = create_async_engine(
        make_url(database_url).set(drivername="postgresql+asyncpg")
    )
    try:
        async with engine.connect() as migration_connection:
            for history in HISTORIES.values():
                assert await migration_connection.run_sync(compare_owned, history) == []
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "alter_statement",
    [
        'ALTER TABLE "user" ALTER COLUMN created_at TYPE TIMESTAMPTZ(0)',
        'ALTER TABLE "user" ALTER COLUMN username TYPE TEXT COLLATE "C"',
        "ALTER SEQUENCE ledger_sheet_sheet_id_seq RENAME TO moved_sheet_seq; CREATE SEQUENCE ledger_sheet_sheet_id_seq AS INTEGER",
    ],
)
async def test_unknown_type_or_sequence_layout_cannot_be_adopted(
    migration_database, alter_statement
):
    """Reject precision, collation, and identity-sequence association drift."""
    from app.adapters.persistence.migrations import HISTORIES, upgrade_database

    database_url, connection = migration_database
    for history in HISTORIES.values():
        await connection.execute(
            (history.directory / f"{history.revision}.sql").read_text()
        )
    await connection.execute(alter_statement)
    with pytest.raises(RuntimeError, match="layout"):
        await upgrade_database(database_url)
    assert (
        await connection.fetchval("SELECT to_regclass('public.ledger_alembic_version')")
        is None
    )
    assert (
        await connection.fetchval("SELECT to_regclass('public.app_alembic_version')")
        is None
    )


@pytest.mark.parametrize(
    "alter_statement",
    [
        "DROP TABLE ledger_alembic_version; CREATE VIEW ledger_alembic_version AS SELECT 'ledger_0001'::varchar(32) AS version_num",
        "ALTER TABLE ledger_alembic_version ALTER COLUMN version_num TYPE TEXT",
        "ALTER TABLE ledger_alembic_version DROP CONSTRAINT ledger_alembic_version_pkc",
        "CREATE DOMAIN migration_revision AS VARCHAR(32) CHECK (VALUE='ledger_0001'); ALTER TABLE ledger_alembic_version ALTER COLUMN version_num TYPE migration_revision",
    ],
)
@pytest.mark.parametrize("entrypoint", ["upgrade", "startup"])
async def test_revision_registry_requires_its_owned_table(
    migration_database, alter_statement, entrypoint
):
    """Reject a forged registry view or a changed version-table contract."""
    from app.adapters.persistence.migrations import upgrade_database, check_database

    database_url, connection = migration_database
    await upgrade_database(database_url)
    await connection.execute(alter_statement)
    with pytest.raises(RuntimeError, match="migration|revision|registry"):
        if entrypoint == "upgrade":
            await upgrade_database(database_url)
        else:
            await check_database(connection)
