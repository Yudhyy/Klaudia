"""Coordinate independent Alembic histories on one dedicated connection."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import asyncpg
from alembic import command, context
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, MetaData
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.util import await_only

from ledger.migrations.catalog import CATALOG_QUERIES, read_catalog

MIGRATION_LOCK = 5438511594260415032


@dataclass(frozen=True)
class History:
    """Keep one owned object set with its fixed baseline and revision table."""

    name: str
    revision: str
    version_table: str
    directory: Path
    metadata: MetaData

    def configuration(self) -> Config:
        """Build runtime settings without an authoring configuration file.

        Returns:
            Configuration that also works outside the repository checkout.
        """
        configuration = Config()
        configuration.set_main_option("script_location", str(self.directory))
        return configuration

    def scripts(self) -> ScriptDirectory:
        """Read this history's revision graph without opening a database.

        Returns:
            The installed Alembic revision registry.
        """
        return ScriptDirectory.from_config(self.configuration())

    def at_revision(self, revision: str) -> "History":
        """Select a reviewed schema contract from the same owned history.

        Args:
            revision: Fixed revision already checked against the revision graph.

        Returns:
            History settings with the selected frozen schema contract.
        """
        return replace(self, revision=revision)

    def include_name(self, name: str | None, kind: str, parents: dict) -> bool:
        """Limit reflection to owned public tables.

        Args:
            name: Reflected object name.
            kind: Alembic object category.
            parents: Reflected schema and table names.

        Returns:
            Whether this history owns the reflected object.
        """
        if kind == "schema":
            return name in {None, "public"}
        if parents.get("schema_name") not in {None, "public"}:
            return False
        return kind != "table" or name in self.metadata.tables

    def include_object(self, schema_object, name, kind, reflected, compare_to):
        """Preserve the supported sandbox's unused legacy request column.

        Args:
            schema_object: SQLAlchemy object under comparison.
            name: Object name.
            kind: Alembic object category.
            reflected: Whether the object came from PostgreSQL.
            compare_to: Matching metadata object, when present.

        Returns:
            Whether Alembic can compare this owned object.
        """
        table_name = name if kind == "table" else schema_object.table.name
        if table_name not in self.metadata.tables:
            return False
        return not (
            reflected
            and kind == "column"
            and name == "request"
            and table_name == "ledger_table_operation"
            and compare_to is None
        )

    def baseline(self) -> dict[str, list[dict[str, Any]]]:
        """Load the reviewed schema contract for the fixed baseline.

        Returns:
            Owned catalog definitions, independent of live metadata changes.
        """
        return json.loads((self.directory / f"{self.revision}.json").read_text())


def owned_layout(catalog: dict, history: History) -> dict:
    """Select owned definitions and the supported optional legacy column.

    Args:
        catalog: Public catalog definitions.
        history: Owner and reviewed fixed baseline.

    Returns:
        A normalized layout for exact baseline comparison.
    """
    expected = history.baseline()
    registered = [
        history.at_revision(revision.revision).baseline()
        for revision in history.scripts().walk_revisions()
    ]
    owned_tables = {
        entry["table_name"] for schema in registered for entry in schema["tables"]
    } | history.metadata.tables.keys()
    owned = {}
    for kind, entries in catalog.items():
        if kind in {"functions", "extensions", "sequences"}:
            key = {
                "functions": "proname",
                "extensions": "extname",
                "sequences": "sequencename",
            }[kind]
            owned_names = {
                entry[key] for schema in registered for entry in schema[kind]
            }
            selected = [entry for entry in entries if entry[key] in owned_names]
        else:
            selected = [
                entry for entry in entries if entry["table_name"] in owned_tables
            ]
        if kind == "columns":
            legacy_column = next(
                (
                    entry
                    for entry in selected
                    if entry["table_name"] == "ledger_table_operation"
                    and entry["column_name"] == "request"
                ),
                None,
            )
            if legacy_column is not None:
                allowed = dict(
                    next(
                        entry
                        for entry in expected[kind]
                        if entry["table_name"] == "ledger_table_operation"
                        and entry["column_name"] == "receipt"
                    )
                )
                allowed["column_name"] = "request"
                if legacy_column == allowed:
                    selected = [
                        entry for entry in selected if entry is not legacy_column
                    ]
        owned[kind] = sorted(
            selected, key=lambda entry: json.dumps(entry, sort_keys=True)
        )
    return owned


def baseline_layout(history: History) -> dict:
    """Sort a fixed catalog contract without using mutable runtime metadata.

    Args:
        history: Owner of the baseline.

    Returns:
        The normalized fixed layout.
    """
    return {
        kind: sorted(entries, key=lambda entry: json.dumps(entry, sort_keys=True))
        for kind, entries in history.baseline().items()
    }


def classify_layout(connection: Connection, history: History) -> str:
    """Reject drift before a history creates or stamps a version table.

    Args:
        connection: Connection holding the shared migration lock.
        history: History to inspect.

    Returns:
        The supported blank, existing or current state.

    Raises:
        RuntimeError: The owned schema or revision is unknown or incomplete.
    """
    catalog = read_catalog(connection)
    table_names = {entry["table_name"] for entry in catalog["tables"]}
    scripts = history.scripts()
    if scripts.get_heads() != [history.revision]:
        raise RuntimeError(f"Invalid {history.name} migration head registry")
    baseline = history.at_revision(scripts.get_base())
    recorded = baseline
    if history.version_table in table_names:
        validate_revision_table(catalog, history)
        revisions = list(
            connection.exec_driver_sql(
                f"SELECT version_num FROM public.{history.version_table}"
            ).scalars()
        )
        known_revisions = {revision.revision for revision in scripts.walk_revisions()}
        if len(revisions) != 1 or revisions[0] not in known_revisions:
            raise RuntimeError(
                f"Unknown {history.name} migration revision; stop rollout"
            )
        recorded = history.at_revision(revisions[0])
        state = "current" if revisions == [history.revision] else "upgrade"
    elif not (table_names & history.metadata.tables.keys()):
        layout = owned_layout(catalog, baseline)
        if any(entries for kind, entries in layout.items() if kind != "extensions"):
            raise RuntimeError(f"Partial {history.name} layout; stop before migration")
        return "blank"
    else:
        state = "existing"
    layout = owned_layout(catalog, recorded)
    expected = baseline_layout(recorded)
    if (
        state == "existing"
        and history.name == "ledger"
        and _is_legacy_ledger(layout, expected)
    ):
        return "legacy"
    mismatches = [kind for kind in expected if layout[kind] != expected[kind]]
    if mismatches:
        raise RuntimeError(
            f"Unknown {history.name} layout ({', '.join(mismatches)}); stop before migration"
        )
    return state


def validate_revision_table(catalog: dict, history: History) -> None:
    """Reject forged or changed Alembic revision objects.

    Args:
        catalog: Public catalog definitions from the caller's connection.
        history: Owner of the required revision table.

    Raises:
        RuntimeError: The revision object differs from Alembic's table contract.
    """
    definitions = {
        kind: [
            entry
            for entry in entries
            if entry.get("table_name") == history.version_table
        ]
        for kind, entries in catalog.items()
    }
    tables = definitions["tables"]
    columns = definitions["columns"]
    constraints = definitions["constraints"]
    indexes = definitions["indexes"]
    column_contract = {
        "column_name": "version_num",
        "udt_name": "varchar",
        "udt_schema": "pg_catalog",
        "domain_schema": None,
        "domain_name": None,
        "character_maximum_length": 32,
        "is_nullable": "NO",
        "column_default": None,
        "is_identity": "NO",
        "is_generated": "NEVER",
        "collation_schema": None,
        "collation_name": None,
    }
    valid_table = (
        len(tables) == 1
        and tables[0]["relkind"] == "r"
        and tables[0]["relpersistence"] == "p"
        and not tables[0]["relrowsecurity"]
    )
    valid_column = len(columns) == 1 and all(
        columns[0][key] == value for key, value in column_contract.items()
    )
    valid_key = (
        len(constraints) == 1
        and constraints[0]["contype"] == "p"
        and constraints[0]["definition"] == "PRIMARY KEY (version_num)"
        and constraints[0]["convalidated"]
        and not constraints[0]["condeferrable"]
    )
    valid_index = (
        len(indexes) == 1 and indexes[0]["indisvalid"] and indexes[0]["indisready"]
    )
    if (
        not (valid_table and valid_column and valid_key and valid_index)
        or definitions["triggers"]
    ):
        raise RuntimeError(
            f"Invalid {history.name} migration revision table; stop rollout"
        )


def _is_legacy_ledger(layout: dict, expected: dict) -> bool:
    """Match only the two supplied pre-tenancy ledger layouts.

    Args:
        layout: Installed unversioned ledger objects.
        expected: Reviewed current ledger baseline.

    Returns:
        Whether baseline DDL can adopt this exact legacy layout.
    """
    without_foreign_key = dict(expected)
    without_foreign_key["constraints"] = [
        entry
        for entry in expected["constraints"]
        if entry["conname"] != "ledger_sheet_workspace_fkey"
    ]
    if layout == without_foreign_key:
        return True
    sheet_only = {}
    for kind, entries in without_foreign_key.items():
        if kind == "sequences":
            sheet_only[kind] = [
                entry
                for entry in entries
                if entry["sequencename"] == "ledger_sheet_sheet_id_seq"
            ]
        else:
            sheet_only[kind] = [
                entry
                for entry in entries
                if entry.get("table_name") == "ledger_sheet"
                and entry.get("column_name") != "revision"
            ]
    sheet_only["triggers"] = []
    return layout == sheet_only


def migration_config(connection: Connection, history: History) -> Config:
    """Supply Alembic with the already locked connection.

    Args:
        connection: Dedicated migration connection.
        history: Independent script location and version table.

    Returns:
        Alembic configuration with no second engine or pool.
    """
    configuration = history.configuration()
    configuration.attributes.update(connection=connection, history=history)
    return configuration


def run_environment(
    connection: Connection | None = None, history: History | None = None
) -> None:
    """Use the connection and owned filters supplied by the release runner.

    Args:
        connection: Optional caller-owned release or read-only authoring connection.
        history: Optional owner; injected Alembic attributes supply omitted values.

    Raises:
        RuntimeError: A caller tries to bypass the release connection contract.
    """
    if connection is None:
        connection = context.config.attributes.get("connection")
    if history is None:
        history = context.config.attributes.get("history")
    if connection is None or history is None:
        raise RuntimeError("Use the coordinated migration runner")

    def validate_revision(ctx, step, heads, run_args):
        """Check each revision's frozen schema before its transaction commits.

        Args:
            ctx: Alembic context using the locked release connection.
            step: Applied migration step.
            heads: Revision heads after the version-table update.
            run_args: Extra migration arguments supplied by Alembic.

        Raises:
            RuntimeError: The migration left an unexpected owned layout.
        """
        if len(heads) != 1:
            raise RuntimeError("Each migration history must have one head")
        recorded = history.at_revision(next(iter(heads)))
        catalog = read_catalog(ctx.connection)
        validate_revision_table(catalog, history)
        if owned_layout(catalog, recorded) != baseline_layout(recorded):
            raise RuntimeError(f"Unexpected {history.name} layout after migration")

    context.configure(
        connection=connection,
        target_metadata=history.metadata,
        version_table=history.version_table,
        include_name=history.include_name,
        include_object=history.include_object,
        compare_server_default=True,
        transaction_per_migration=True,
        on_version_apply=validate_revision,
    )
    with context.begin_transaction():
        context.run_migrations()


def execute_baseline(connection: Connection, sql: str) -> None:
    """Execute frozen PostgreSQL DDL inside Alembic's transaction.

    Args:
        connection: Alembic's SQLAlchemy connection using the asyncpg dialect.
        sql: Reviewed frozen SQL, including function bodies.
    """
    await_only(connection.connection.driver_connection.execute(sql))


@contextmanager
def migration_lock(connection: Connection) -> Iterator[None]:
    """Share session exclusion between authoring and release commands.

    Args:
        connection: Dedicated connection that closes after the release step.

    Yields:
        Exclusive schema access across transaction boundaries.

    Raises:
        RuntimeError: Another authoring or release session owns the lock.
    """
    locked = connection.exec_driver_sql(
        f"SELECT pg_try_advisory_lock({MIGRATION_LOCK})"
    ).scalar_one()
    connection.commit()
    if not locked:
        raise RuntimeError(
            "Database migration is busy; retry after the active runner exits"
        )
    try:
        yield
    finally:
        connection.rollback()
        connection.exec_driver_sql(f"SELECT pg_advisory_unlock({MIGRATION_LOCK})")
        connection.commit()


def _upgrade_histories(connection: Connection, histories: tuple[History, ...]) -> None:
    """Keep one session lock across preflight, both histories and commits.

    Args:
        connection: Dedicated connection that closes after the release step.
        histories: Histories in release order.

    Raises:
        RuntimeError: Another runner owns the lock or preflight rejects storage.
    """
    with migration_lock(connection):
        connection.exec_driver_sql("SET search_path TO public, pg_catalog")
        states = [classify_layout(connection, history) for history in histories]
        connection.commit()
        for history, state in zip(histories, states, strict=True):
            if state == "current":
                continue
            configuration = migration_config(connection, history)
            if state == "existing":
                command.stamp(configuration, history.scripts().get_base())
            command.upgrade(configuration, history.revision)
            classify_layout(connection, history)
            connection.commit()


async def upgrade_histories(database_url: str, histories: tuple[History, ...]) -> None:
    """Upgrade one database with a session lock and checked fixed baselines.

    Args:
        database_url: PostgreSQL URL for the explicit release target.
        histories: Independent histories in release order.
    """
    engine = create_async_engine(
        "postgresql+asyncpg://",
        async_creator=partial(asyncpg.connect, database_url),
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_upgrade_histories, histories)
    finally:
        await engine.dispose()


async def check_history(connection: asyncpg.Connection, history: History) -> None:
    """Check required revision and owned definitions without applying DDL.

    Args:
        connection: Existing runtime connection; this function does not own it.
        history: Required owned schema and revision.

    Raises:
        RuntimeError: Release migrations are missing or storage has drifted.
    """
    version_table = await connection.fetchval(
        "SELECT to_regclass($1)", "public." + history.version_table
    )
    if version_table is None:
        raise RuntimeError(
            f"Run database migrations before starting {history.name} services"
        )
    catalog = {
        kind: [dict(row) for row in await connection.fetch(query)]
        for kind, query in CATALOG_QUERIES.items()
    }
    validate_revision_table(catalog, history)
    if history.scripts().get_heads() != [history.revision]:
        raise RuntimeError(f"Invalid {history.name} migration head registry")
    revisions = [
        row["version_num"]
        for row in await connection.fetch(
            f"SELECT version_num FROM public.{history.version_table}"
        )
    ]
    if revisions != [history.revision]:
        raise RuntimeError(
            f"Required {history.name} migration revision is {history.revision}; stop rollout"
        )
    if owned_layout(catalog, history) != baseline_layout(history):
        raise RuntimeError(
            f"Unknown {history.name} layout; stop before starting services"
        )
