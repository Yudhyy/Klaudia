"""Postgres catalogue of explicitly registered tables within a workbook scope."""

import json
import re
import uuid
from typing import Any

import asyncpg

from ledger import grid
from ledger.connections import ConnectionProvider
from ledger.calculations import CheckedCalculation, decode_calculation_grid
from ledger.financial_contracts import CheckedFinancialRequest
from ledger.financial_store import execute_owned
from ledger.query import AggregateQuery, aggregate_grid
from ledger.errors import RevisionConflictError, SheetNotFoundError
from ledger.resources import (
    ResourceExistsError,
    ResourceNotFoundError,
    ResourceSearch,
    TableRegistration,
    TableUpdate,
)

CATALOGUE_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS ledger_resource (
    resource_id TEXT PRIMARY KEY,
    sheet_id INTEGER NOT NULL REFERENCES ledger_sheet(sheet_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    grain TEXT NOT NULL,
    aliases TEXT[] NOT NULL,
    alias_keys TEXT[] NOT NULL,
    entity TEXT,
    period_start DATE,
    period_end DATE,
    table_range TEXT NOT NULL,
    first_row INTEGER NOT NULL,
    first_column INTEGER NOT NULL,
    last_row INTEGER NOT NULL,
    last_column INTEGER NOT NULL,
    columns JSONB NOT NULL,
    column_keys TEXT[] NOT NULL,
    record_count INTEGER NOT NULL,
    source_revision BIGINT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1,
    search_text TEXT NOT NULL,
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (sheet_id, table_range)
);
CREATE INDEX IF NOT EXISTS ledger_resource_search ON ledger_resource USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS ledger_resource_trigrams ON ledger_resource USING GIN(search_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ledger_resource_columns ON ledger_resource USING GIN(column_keys);
"""

_SELECT_RESOURCE = """
SELECT r.*, s.workspace, s.title AS sheet_name, s.revision AS current_sheet_revision,
       s.updated_at AS sheet_updated_at, w.name AS workbook_name
FROM ledger_resource r
JOIN ledger_sheet s ON s.sheet_id = r.sheet_id
JOIN ledger_spreadsheet w ON w.spreadsheet_id = s.workspace
"""


def _describe(row: asyncpg.Record) -> dict[str, Any]:
    """Render stored metadata with current identity and freshness evidence.

    Args:
        row: A catalogue row joined to its current sheet and workbook.

    Returns:
        A descriptor containing no cell records.
    """
    return {
        "table_id": row["resource_id"],
        "resource_type": "table",
        "spreadsheet_id": row["workspace"],
        "workbook_name": row["workbook_name"],
        "sheet_id": row["sheet_id"],
        "sheet_name": row["sheet_name"],
        "name": row["name"],
        "description": row["description"],
        "grain": row["grain"],
        "aliases": list(row["aliases"]),
        "entity": row["entity"],
        "period_start": row["period_start"].isoformat()
        if row["period_start"]
        else None,
        "period_end": row["period_end"].isoformat() if row["period_end"] else None,
        "range": row["table_range"],
        "columns": json.loads(row["columns"]),
        "record_count": row["record_count"],
        "source_revision": row["source_revision"],
        "current_sheet_revision": row["current_sheet_revision"],
        "catalogue_revision": row["revision"],
        "freshness": "current"
        if row["source_revision"] == row["current_sheet_revision"]
        else "stale",
        "description_source": "registration",
        "schema_source": "sheet_headers",
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _table_metadata(
    definition: TableRegistration, sheet: asyncpg.Record
) -> dict[str, Any]:
    """Derive a registered table's structural facts from one locked source grid.

    Args:
        definition: Caller-provided region and descriptive metadata.
        sheet: Locked grid and revision from the bound workbook.

    Returns:
        Database fields with exact headers, source revision and canonical bounds.

    Raises:
        ValueError: The range lacks complete, unique text headers.
    """
    grid.validate_bounded_range(definition.table_range, 1_000_000)
    first_row, first_column, last_row, last_column = grid.parse_range(
        definition.table_range
    )
    selected = grid.slice_range(json.loads(sheet["grid"]), definition.table_range)
    if not selected or len(selected[0]) != last_column - first_column + 1:
        raise ValueError("The registered range must start with a complete header row")
    headers = selected[0]
    if len(headers) > 256 or any(
        type(name) is not str or not name.strip() or len(name) > 256 for name in headers
    ):
        raise ValueError(
            "Table headers must be non-empty text of at most 256 characters"
        )
    column_keys = [name.strip().lower() for name in headers]
    if len(set(column_keys)) != len(column_keys):
        raise ValueError(
            "Table headers must be unique ignoring case and surrounding spaces"
        )
    fields = definition.model_dump(exclude={"expected_sheet_revision", "table_range"})
    fields.update(
        {
            "table_range": f"{grid.index_to_col(first_column)}{first_row + 1}:{grid.index_to_col(last_column)}{last_row + 1}",
            "first_row": first_row,
            "first_column": first_column,
            "last_row": last_row,
            "last_column": last_column,
            "columns": [
                {
                    "column_id": f"col_{uuid.uuid4().hex}",
                    "name": name,
                    "offset": index,
                    "semantic_type": "unknown",
                }
                for index, name in enumerate(headers)
            ],
            "column_keys": column_keys,
            "alias_keys": [alias.lower() for alias in definition.aliases],
            "record_count": sum(
                any(value is not None and value != "" for value in row)
                for row in selected[1:]
            ),
            "source_revision": sheet["revision"],
            "search_text": " ".join(
                [
                    definition.name,
                    definition.description,
                    definition.grain,
                    definition.entity or "",
                    *definition.aliases,
                    *headers,
                ]
            ).lower(),
        }
    )
    return fields


class CatalogueStore:
    """Register, inspect and search table metadata inside a caller-bound workbook."""

    def __init__(self, pool: ConnectionProvider) -> None:
        """Use the ledger's existing database pool.

        Args:
            pool: Ledger connections with catalogue migrations applied.
        """
        self._pool = pool

    async def _source(
        self, connection: asyncpg.Connection, scope: tuple[str, TableRegistration]
    ) -> asyncpg.Record:
        """Lock the source sheet and verify the caller's observed revision.

        Args:
            connection: Connection inside a catalogue transaction.
            scope: Bound workbook and the requested table definition.

        Returns:
            The source grid and revision.

        Raises:
            SheetNotFoundError: The target is outside the workbook.
            RevisionConflictError: The source changed since it was read.
        """
        workspace, definition = scope
        sheet = await connection.fetchrow(
            "SELECT grid, revision FROM ledger_sheet WHERE workspace = $1 AND sheet_id = $2 FOR UPDATE",
            workspace,
            definition.sheet_id,
        )
        if sheet is None:
            raise SheetNotFoundError("Sheet not found in the active workbook")
        if sheet["revision"] != definition.expected_sheet_revision:
            raise RevisionConflictError(
                "Source sheet changed; inspect it before registering metadata"
            )
        return sheet

    async def _check_overlap(
        self, connection: asyncpg.Connection, region: tuple[dict[str, Any], str | None]
    ) -> None:
        """Reject overlapping tables while the source sheet lock serialises changes.

        Args:
            connection: Active transaction holding the source sheet lock.
            region: New metadata and the resource ID to exclude on update.

        Raises:
            ResourceExistsError: Another registered table overlaps the region.
        """
        fields, excluded_id = region
        overlap = await connection.fetchval(
            """
            SELECT resource_id FROM ledger_resource
            WHERE sheet_id = $1 AND ($2::text IS NULL OR resource_id <> $2)
              AND first_row <= $3 AND last_row >= $4
              AND first_column <= $5 AND last_column >= $6 LIMIT 1
        """,
            fields["sheet_id"],
            excluded_id,
            fields["last_row"],
            fields["first_row"],
            fields["last_column"],
            fields["first_column"],
        )
        if overlap:
            raise ResourceExistsError(
                "A registered table overlaps this range; inspect or update it by ID"
            )

    async def register(
        self, workspace: str, definition: TableRegistration
    ) -> dict[str, Any]:
        """Create a table identity using headers from an observed source revision.

        Args:
            workspace: Workbook supplied by the authorisation layer.
            definition: Region and caller-provided business meaning.

        Returns:
            The committed table descriptor with stable column identities.

        Raises:
            SheetNotFoundError: The source is outside the workbook.
            RevisionConflictError: The source revision is stale.
            ResourceExistsError: A table already overlaps the selected range.
            ValueError: Source headers are invalid.
        """
        definition = definition.model_copy(deep=True)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                sheet = await self._source(connection, (workspace, definition))
                fields = _table_metadata(definition, sheet)
                await self._check_overlap(connection, (fields, None))
                fields["resource_id"] = f"tbl_{uuid.uuid4().hex}"
                fields["columns"] = json.dumps(fields["columns"])
                names = ", ".join(fields)
                placeholders = ", ".join(
                    f"${index}" for index in range(1, len(fields) + 1)
                )
                await connection.execute(
                    f"INSERT INTO ledger_resource ({names}) VALUES ({placeholders})",
                    *fields.values(),
                )
                row = await connection.fetchrow(
                    _SELECT_RESOURCE + " WHERE r.resource_id = $1",
                    fields["resource_id"],
                )
            return _describe(row)

    async def update(self, workspace: str, change: TableUpdate) -> dict[str, Any]:
        """Revise a registered table on the same sheet while retaining column IDs.

        Args:
            workspace: Workbook supplied by the authorisation layer.
            change: Stable target, expected catalogue revision and full definition.

        Returns:
            The updated descriptor after commit.

        Raises:
            ResourceNotFoundError: The table is outside the workbook.
            RevisionConflictError: The sheet or metadata revision is stale.
            ResourceExistsError: Another table overlaps the new range.
            ValueError: Changed headers need explicit identity remapping.
        """
        change = change.model_copy(deep=True)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                current = await connection.fetchrow(
                    _SELECT_RESOURCE + " WHERE s.workspace = $1 AND r.resource_id = $2",
                    workspace,
                    change.table_id,
                )
                if current is None:
                    raise ResourceNotFoundError(
                        "Table not found in the active workbook"
                    )
                if current["sheet_id"] != change.definition.sheet_id:
                    raise ValueError(
                        "Moving a table between sheets needs explicit identity remapping"
                    )
                sheet = await self._source(connection, (workspace, change.definition))
                current = await connection.fetchrow(
                    "SELECT revision, columns FROM ledger_resource WHERE resource_id = $1 FOR UPDATE",
                    change.table_id,
                )
                if current["revision"] != change.expected_catalogue_revision:
                    raise RevisionConflictError(
                        "Catalogue changed; inspect the table before updating it"
                    )
                fields = _table_metadata(change.definition, sheet)
                old_columns = json.loads(current["columns"])
                if change.column_ids is not None:
                    if len(change.column_ids) != len(fields["columns"]):
                        raise ValueError("Map every new column to an old ID or null")
                    retained = [
                        identity
                        for identity in change.column_ids
                        if identity is not None
                    ]
                    old_by_id = {column["column_id"]: column for column in old_columns}
                    if (
                        len(set(retained)) != len(retained)
                        or not set(retained) <= old_by_id.keys()
                    ):
                        raise ValueError(
                            "Column IDs must be distinct identities from this table"
                        )
                    for column, identity in zip(fields["columns"], change.column_ids):
                        if identity is not None:
                            column["column_id"] = identity
                            column["semantic_type"] = old_by_id[identity][
                                "semantic_type"
                            ]
                elif [column["name"] for column in old_columns] != [
                    column["name"] for column in fields["columns"]
                ]:
                    raise ValueError(
                        "Changed source headers need explicit column identity remapping"
                    )
                await self._check_overlap(connection, (fields, change.table_id))
                fields["columns"] = json.dumps(
                    fields["columns"] if change.column_ids is not None else old_columns
                )
                assignments = ", ".join(
                    f"{name} = ${index}" for index, name in enumerate(fields, 1)
                )
                await connection.execute(
                    f"UPDATE ledger_resource SET {assignments}, revision = revision + 1, updated_at = CURRENT_TIMESTAMP WHERE resource_id = ${len(fields) + 1}",
                    *fields.values(),
                    change.table_id,
                )
                row = await connection.fetchrow(
                    _SELECT_RESOURCE + " WHERE r.resource_id = $1", change.table_id
                )
            return _describe(row)

    async def inspect(self, workspace: str, table_id: str) -> dict[str, Any]:
        """Read a registered schema snapshot and its freshness without loading cells.

        Args:
            workspace: Bound workbook scope.
            table_id: Stable registered table identity.

        Returns:
            Metadata with current sheet identity and source freshness.

        Raises:
            ResourceNotFoundError: The table is absent from this workbook.
        """
        return await self._inspect(("s.workspace", workspace), table_id)

    async def inspect_owned(self, user_id: int, table_id: str) -> dict[str, Any]:
        """Inspect a table only when its workbook currently belongs to this user.

        Args:
            user_id: User identity supplied by authentication, never model input.
            table_id: Requested stable table identity.

        Returns:
            Metadata from the same database snapshot as the ownership check.

        Raises:
            ResourceNotFoundError: The table is absent or belongs to another user.
        """
        return await self._inspect(("w.user_id", user_id), table_id)

    async def calculate_owned(
        self, user_id: int, request: CheckedCalculation
    ) -> dict[str, Any]:
        """Calculate from one snapshot of owned data and registered table bounds.

        Args:
            user_id: Authenticated identity supplied by the application.
            request: Metrics and revisions observed during table inspection.

        Returns:
            Labelled metrics, stable source identities and the resolved query.

        Raises:
            ResourceNotFoundError: The table is absent or foreign.
            RevisionConflictError: Data or metadata changed, or the catalogue is stale.
            ValueError: Metrics, operands, units or bounds are invalid.
        """
        request = request.model_copy(deep=True)
        row = await self._pool.fetchrow(
            """
            SELECT r.resource_id, r.table_range, r.columns, r.revision AS catalogue_revision,
                   r.source_revision, s.sheet_id, s.workspace, s.revision AS sheet_revision, s.grid,
                   EXISTS(SELECT 1 FROM ledger_typed_cell c WHERE c.sheet_id=s.sheet_id
                          AND c.calculation_status IN ('pending','failed')) AS uncalculated
            FROM ledger_resource r
            JOIN ledger_sheet s ON s.sheet_id = r.sheet_id
            JOIN ledger_spreadsheet w ON w.spreadsheet_id = s.workspace
            WHERE w.user_id = $1 AND r.resource_id = $2
            """,
            user_id,
            request.table_id,
        )
        if row is None:
            raise ResourceNotFoundError("Table not found")
        if row["uncalculated"]:
            raise ValueError(
                "Source has failed or pending formulas; inspect typed workbook status"
            )
        if (
            row["sheet_revision"] != request.expected_sheet_revision
            or row["catalogue_revision"] != request.expected_catalogue_revision
            or row["source_revision"] != row["sheet_revision"]
        ):
            raise RevisionConflictError(
                "Source or catalogue changed; inspect the table and refresh stale metadata before calculating"
            )
        query = AggregateQuery(
            table_range=row["table_range"],
            **request.model_dump(
                exclude={
                    "table_id",
                    "expected_sheet_revision",
                    "expected_catalogue_revision",
                }
            ),
        )
        evidence = aggregate_grid(decode_calculation_grid(row["grid"]), query)
        column_ids = {
            column["name"]: column["column_id"] for column in json.loads(row["columns"])
        }
        for group in evidence["groups"]:
            for metric in group["metrics"]:
                metric["column_id"] = column_ids[metric["column"]]
        return {
            "source": {
                "table_id": row["resource_id"],
                "spreadsheet_id": row["workspace"],
                "sheet_id": row["sheet_id"],
                "range": row["table_range"],
                "sheet_revision": row["sheet_revision"],
                "catalogue_revision": row["catalogue_revision"],
            },
            "query": query.model_dump(),
            **evidence,
        }

    async def financial_owned(
        self, user_id: int, request: CheckedFinancialRequest
    ) -> dict[str, Any]:
        """Execute financial intent from one snapshot of all owned sources.

        Args:
            user_id: Authenticated owner identity.
            request: Intent with server-observed source revisions.

        Returns:
            Bounded labelled financial evidence.

        Raises:
            ResourceNotFoundError: Any table is absent or foreign.
            RevisionConflictError: Source or catalogue observations are stale.
            ValueError: Intent or evidence violates execution limits.
        """
        return await execute_owned(self._pool, user_id, request)

    async def _inspect(
        self, scope: tuple[str, str | int], table_id: str
    ) -> dict[str, Any]:
        """Read metadata using a scope column selected by the public entry point.

        Args:
            scope: Code-defined SQL column and parameter value.
            table_id: Stable resource identity.

        Returns:
            The authorised metadata descriptor.

        Raises:
            ResourceNotFoundError: No visible resource matches the identity.
        """
        column, identity = scope
        row = await self._pool.fetchrow(
            _SELECT_RESOURCE + f" WHERE {column} = $1 AND r.resource_id = $2",
            identity,
            table_id,
        )
        if row is None:
            raise ResourceNotFoundError("Table not found")
        return _describe(row)

    async def search(self, workspace: str, query: ResourceSearch) -> dict[str, Any]:
        """Search indexed metadata with lexical expansion and hard scope filters.

        Args:
            workspace: Bound workbook; no other workbook can contribute a candidate.
            query: Intent, optional expanded concepts and business/schema filters.

        Returns:
            Bounded candidates and ranking evidence, never probability estimates.
        """
        return await self._search(("s.workspace", workspace), query)

    async def search_owned(self, user_id: int, query: ResourceSearch) -> dict[str, Any]:
        """Search all currently owned workbooks without enumerating them first.

        Args:
            user_id: User identity supplied by authentication, never model input.
            query: Bounded intent and metadata filters.

        Returns:
            Candidates ranked and counted only after the ownership filter.
        """
        return await self._search(("w.user_id", user_id), query)

    async def _search(
        self, scope: tuple[str, str | int], query: ResourceSearch
    ) -> dict[str, Any]:
        """Apply a code-selected scope before ranking indexed table metadata.

        Args:
            scope: Code-defined SQL column and parameter value.
            query: Validated search concepts and hard filters.

        Returns:
            Bounded candidates with evidence and schema previews.
        """
        column, identity = scope
        tokens = list(
            dict.fromkeys(
                re.findall(r"\w+", " ".join([query.intent, *query.concepts]).lower())
            )
        )[:64]
        tsquery = " | ".join(tokens)
        statement = (
            _SELECT_RESOURCE.replace(
                "SELECT r.*,",
                """
            SELECT lower($3::text) = ANY(r.alias_keys) AS exact_alias,
                   lower(r.name) = lower($3) AS exact_name,
                   r.search_vector @@ to_tsquery('simple', $2) AS lexical_match,
                   lower($3) <% r.search_text AS fuzzy_match,
                   word_similarity(lower($3), r.search_text) AS similarity,
                   r.*,
        """,
            )
            + f"""
            WHERE {column} = $1
              AND ($4::text[] <@ r.column_keys)
              AND ($5::text IS NULL OR lower(r.entity) = lower($5))
              AND ($6::date IS NULL OR (
                   (r.period_start IS NOT NULL OR r.period_end IS NOT NULL)
                   AND (r.period_start IS NULL OR r.period_start <= $6)
                   AND (r.period_end IS NULL OR r.period_end >= $6)))
              AND (r.search_vector @@ to_tsquery('simple', $2)
                   OR lower($3) <% r.search_text OR lower($3) = ANY(r.alias_keys)
                   OR lower(r.name) = lower($3))
            ORDER BY (CASE WHEN lower(r.name) = lower($3) THEN 100 ELSE 0 END
                    + CASE WHEN lower($3) = ANY(r.alias_keys) THEN 100 ELSE 0 END
                    + ts_rank_cd(r.search_vector, to_tsquery('simple', $2)) * 10
                    + word_similarity(lower($3), r.search_text)
                    - CASE WHEN r.source_revision <> s.revision THEN 1 ELSE 0 END) DESC,
                     r.resource_id
            LIMIT $7
        """
        )
        rows = await self._pool.fetch(
            statement,
            identity,
            tsquery,
            query.intent,
            [column.lower() for column in query.required_columns],
            query.entity,
            query.on_date,
            query.limit + 1,
        )
        candidates = []
        for row in rows[: query.limit]:
            reasons = []
            if row["exact_alias"]:
                reasons.append("exact alias")
            if row["exact_name"]:
                reasons.append("exact name")
            if row["lexical_match"]:
                reasons.append("matching metadata terms")
            if row["fuzzy_match"]:
                reasons.append("similar metadata text")
            if query.required_columns:
                reasons.append("required columns present")
            if query.entity:
                reasons.append("declared entity matches")
            if query.on_date:
                reasons.append("declared period covers date")
            descriptor = _describe(row)
            columns = descriptor["columns"]
            descriptor.update(
                columns=columns[:16],
                column_count=len(columns),
                has_more_columns=len(columns) > 16,
                reasons=reasons,
            )
            candidates.append(descriptor)
        return {
            "candidates": candidates,
            "has_more": len(rows) > query.limit,
            "coverage": "registered_tables_only",
        }
