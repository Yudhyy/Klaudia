"""Verify standard Alembic authoring without a path around release ownership."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import Column, MetaData, Text

from app.adapters.persistence.migrations import HISTORIES, upgrade_database
from ledger.migrations.runtime import MIGRATION_LOCK, upgrade_histories

ROOT = Path(__file__).resolve().parents[3]


def authoring_config(history_name: str, directory: Path) -> Path:
    """Copy an owned environment and its root authoring settings.

    Args:
        history_name: Owner selected for the candidate revision.
        directory: Disposable environment directory.

    Returns:
        Configuration path for the standard Alembic command.
    """
    shutil.copytree(HISTORIES[history_name].directory, directory)
    configuration = Config(str(ROOT / "alembic.ini"), ini_section=history_name)
    configuration.set_main_option("script_location", str(directory))
    configuration.set_main_option("prepend_sys_path", str(ROOT))
    config_path = directory / "authoring.ini"
    with config_path.open("w") as config_file:
        configuration.file_config.write(config_file)
    return config_path


async def run_alembic(
    config_path: Path, arguments: list[str], database_url: str
) -> tuple[int, str]:
    """Run the real CLI with an explicit disposable database target.

    Args:
        config_path: Alembic configuration for the selected environment.
        arguments: Selected history and Alembic command arguments.
        database_url: Disposable PostgreSQL database.

    Returns:
        Exit code and captured diagnostics.
    """
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(config_path),
        *arguments,
        cwd=ROOT,
        env=dict(os.environ, DATABASE_URL=database_url),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=30)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return process.returncode, output.decode()


@pytest.mark.parametrize("history_name", ["application", "ledger"])
async def test_standard_alembic_check_uses_owned_current_schema(
    migration_database, history_name
):
    """Compare one owner's metadata while the other owner's tables exist."""
    database_url, connection = migration_database
    await upgrade_database(database_url)
    before = await connection.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )
    exit_code, output = await run_alembic(
        ROOT / "alembic.ini", ["-n", history_name, "check"], database_url
    )
    assert exit_code == 0, output
    assert "No new upgrade operations detected" in output
    assert (
        await connection.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        )
        == before
    )


@pytest.mark.parametrize(
    "action, target", [("upgrade", "head"), ("stamp", "head"), ("downgrade", "base")]
)
async def test_raw_alembic_cannot_bypass_release_runner(
    migration_database, action, target
):
    """Reject direct schema writes before an environment opens a database."""
    database_url, connection = migration_database
    exit_code, output = await run_alembic(
        ROOT / "alembic.ini", ["-n", "ledger", action, target], database_url
    )
    assert exit_code != 0
    assert "coordinated migration runner" in output
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
        )
        == 0
    )


async def test_autogenerate_requires_a_versioned_database(migration_database, tmp_path):
    """Leave a blank database and revision directory unchanged during authoring."""
    database_url, connection = migration_database
    directory = tmp_path / "ledger"
    config_path = authoring_config("ledger", directory)
    before = set((directory / "versions").glob("*.py"))
    exit_code, output = await run_alembic(
        config_path,
        ["-n", "ledger", "revision", "--autogenerate", "-m", "add notes"],
        database_url,
    )
    assert exit_code != 0
    assert "before authoring" in output
    assert set((directory / "versions").glob("*.py")) == before
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
        )
        == 0
    )


async def test_authoring_obeys_the_release_lock(migration_database):
    """Keep schema comparison from overlapping an active release."""
    database_url, connection = migration_database
    await upgrade_database(database_url)
    await connection.fetchval("SELECT pg_advisory_lock($1)", MIGRATION_LOCK)
    try:
        exit_code, output = await run_alembic(
            ROOT / "alembic.ini", ["-n", "ledger", "check"], database_url
        )
        assert exit_code != 0
        assert "busy" in output
    finally:
        await connection.fetchval("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK)


async def test_generated_revision_can_be_reviewed_and_upgraded(
    migration_database, tmp_path
):
    """Generate against a read-only snapshot, then apply its reviewed contract once."""
    database_url, connection = migration_database
    await upgrade_database(database_url)
    history = HISTORIES["ledger"]
    directory = tmp_path / "ledger"
    config_path = authoring_config("ledger", directory)
    (
        directory / "env.py"
    ).write_text('''"""Inspect a candidate metadata change in the test environment."""
from pathlib import Path
from alembic import context
from sqlalchemy import Column, MetaData, Text
from ledger.migrations import LEDGER_HISTORY
from ledger.migrations.runtime import History
from ledger.migrations.authoring import run_environment

class ReadOnlyHistory(History):
    """Observe PostgreSQL's actual transaction mode during comparison."""
    def include_object(self, schema_object, name, kind, reflected, compare_to):
        """Check database mode before applying the owner's object filter."""
        assert context.get_context().connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
        return super().include_object(schema_object, name, kind, reflected, compare_to)

metadata = MetaData()
for table in LEDGER_HISTORY.metadata.tables.values():
    table.to_metadata(metadata)
metadata.tables["ledger_sheet"].append_column(Column("notes", Text()))
history = ReadOnlyHistory(LEDGER_HISTORY.name, LEDGER_HISTORY.revision, LEDGER_HISTORY.version_table, Path(context.script.dir), metadata)
run_environment(history)
''')
    exit_code, output = await run_alembic(
        config_path,
        [
            "-n",
            "ledger",
            "revision",
            "--autogenerate",
            "--rev-id",
            "ledger_0002",
            "-m",
            "add sheet notes",
        ],
        database_url,
    )
    assert exit_code == 0, output
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE table_name='ledger_sheet' AND column_name='notes'"
        )
        == 0
    )
    assert (
        await connection.fetchval("SELECT version_num FROM ledger_alembic_version")
        == history.revision
    )
    configuration = Config(str(config_path), ini_section="ledger")
    generated = ScriptDirectory.from_config(configuration).get_revision("ledger_0002")
    assert generated.down_revision == history.revision
    assert Path(generated.path).name.endswith("_add_sheet_notes.py")
    metadata = MetaData()
    for table in history.metadata.tables.values():
        table.to_metadata(metadata)
    metadata.tables["ledger_sheet"].append_column(Column("notes", Text()))
    expected = history.baseline()
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
    candidate = replace(
        history, directory=directory, metadata=metadata, revision="ledger_0002"
    )
    await upgrade_histories(database_url, (candidate,))
    await upgrade_histories(database_url, (candidate,))
    assert (
        await connection.fetchval("SELECT version_num FROM ledger_alembic_version")
        == "ledger_0002"
    )
    assert (
        await connection.fetchval("SELECT to_regclass('public.session')") == "session"
    )
