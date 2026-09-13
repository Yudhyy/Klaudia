"""Owner-checked appends that commit table bounds and receipts with cell changes."""

import hashlib
from dataclasses import dataclass
import json
import uuid
from typing import Annotated, Any, Mapping

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from ledger import grid
from ledger.approvals import check_approval, prepare_approval
from ledger.errors import IdempotencyConflictError, RevisionConflictError
from ledger.operations import CellValue
from ledger.resources import ResourceNotFoundError

TABLE_OPERATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_table_operation (
    user_id INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    workspace TEXT,
    receipt JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, idempotency_key)
);
ALTER TABLE ledger_table_operation ADD COLUMN IF NOT EXISTS request_payload TEXT;
ALTER TABLE ledger_table_operation ADD COLUMN IF NOT EXISTS approval_required BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE ledger_table_operation ADD COLUMN IF NOT EXISTS approval_id TEXT;
ALTER TABLE ledger_table_operation ADD COLUMN IF NOT EXISTS approval_status TEXT;
ALTER TABLE ledger_table_operation ADD COLUMN IF NOT EXISTS approval_expires_at TIMESTAMPTZ;
ALTER TABLE ledger_table_operation ADD COLUMN IF NOT EXISTS approved_fingerprint TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS ledger_table_approval_id ON ledger_table_operation(approval_id) WHERE approval_id IS NOT NULL;
"""

RecordField = Annotated[StrictStr, Field(min_length=1, max_length=256)]
TableRecord = Annotated[
    dict[RecordField, CellValue], Field(min_length=1, max_length=256)
]


class TableAppendProposal(BaseModel):
    """Named records bound to server-observed table revisions before key assignment."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    table_id: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    expected_sheet_revision: Annotated[StrictInt, Field(ge=0)]
    expected_catalogue_revision: Annotated[StrictInt, Field(gt=0)]
    records: Annotated[list[TableRecord], Field(min_length=1, max_length=100)]


class TableAppend(TableAppendProposal):
    """An exact checked append carrying its execution retry identity."""

    idempotency_key: Annotated[
        StrictStr, Field(min_length=1, max_length=128, pattern=r"\S")
    ]


async def prepare_table_append(
    pool: asyncpg.Pool,
    user_id: int,
    request: TableAppendProposal,
    *,
    require_approval: bool = False,
) -> dict[str, str]:
    """Persist an exact request before execution without changing ledger cells.

    Args:
        pool: Ledger connections with operation storage installed.
        user_id: Authenticated caller identity.
        request: Records and server-observed revisions before key assignment.
        require_approval: Server policy requiring a human decision before execution.

    Returns:
        A stable reference for identical records at identical observed revisions.

    Raises:
        ResourceNotFoundError: The table is absent or foreign.
        ValueError: The request exceeds the byte budget.
    """
    payload = json.dumps(
        request.model_dump(exclude={"idempotency_key"}),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )
    operation_ref = "prepared:" + hashlib.sha256(payload.encode()).hexdigest()
    checked = TableAppend(**json.loads(payload), idempotency_key=operation_ref)
    payload = _request_payload(checked)
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    async with pool.acquire() as connection:
        async with connection.transaction():
            workspace = await connection.fetchval(
                "SELECT w.spreadsheet_id FROM ledger_resource r JOIN ledger_sheet s ON s.sheet_id = r.sheet_id JOIN ledger_spreadsheet w ON w.spreadsheet_id = s.workspace WHERE r.resource_id = $1 AND w.user_id = $2 FOR SHARE OF w",
                checked.table_id,
                user_id,
            )
            if workspace is None:
                raise ResourceNotFoundError("Table not found")
            await connection.execute(
                "INSERT INTO ledger_table_operation (user_id, idempotency_key, fingerprint, workspace, request_payload) VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING",
                user_id,
                operation_ref,
                fingerprint,
                workspace,
                payload,
            )
            approval_id = await prepare_approval(
                connection, (user_id, operation_ref), require_approval
            )

    prepared = {
        "operation_ref": operation_ref,
        "status": "prepared",
        "table_id": checked.table_id,
    }
    if approval_id is not None:
        prepared["approval_id"] = approval_id
    return prepared


async def execute_prepared_operation(
    pool: asyncpg.Pool, user_id: int, operation_ref: str
) -> dict[str, Any]:
    """Dispatch a stored operation without accepting replacement arguments.

    Args:
        pool: Existing ledger operation storage.
        user_id: Authenticated owner.
        operation_ref: Original prepared append or authoring identity.

    Returns:
        Committed receipt after the matching operation's checks.
    """
    if operation_ref.startswith("authoring:"):
        from ledger.authoring import execute_authoring

        return await execute_authoring(pool, user_id, operation_ref)
    return await execute_prepared_append(pool, user_id, operation_ref)


async def execute_prepared_append(
    pool: asyncpg.Pool, user_id: int, operation_ref: str
) -> dict[str, Any]:
    """Execute or replay the stored request with current ownership checks.

    Args:
        pool: Ledger operation storage.
        user_id: Authenticated caller, never supplied by the model.
        operation_ref: Reference returned by preparation.

    Returns:
        The atomically committed receipt, including exact retries.

    Raises:
        ResourceNotFoundError: The proposal or current ownership is absent.
        RevisionConflictError: An uncommitted proposal has stale revisions.
        ValueError: The stored append fails execution validation.
    """
    payload = await pool.fetchval(
        "SELECT request_payload FROM ledger_table_operation WHERE user_id = $1 AND idempotency_key = $2",
        user_id,
        operation_ref,
    )
    if payload is None:
        raise ResourceNotFoundError("Operation not found")
    return await execute_table_append(
        pool, user_id, TableAppend.model_validate_json(payload)
    )


def _request_payload(request: TableAppend) -> str:
    """Serialize an exact bounded request for persistence and fingerprinting.

    Args:
        request: Validated immutable append contract.

    Returns:
        Canonical JSON preserving record order.

    Raises:
        ValueError: The request exceeds the byte budget.
    """
    payload = json.dumps(
        request.model_dump(), sort_keys=True, ensure_ascii=False, allow_nan=False
    )
    if len(payload.encode("utf-8")) > 65536:
        raise ValueError("Append exceeds the 65536-byte request budget")
    return payload


async def execute_table_append(
    pool: asyncpg.Pool, user_id: int, request: TableAppend
) -> dict[str, Any]:
    """Commit an owned append once while retaining exact replay evidence.

    Args:
        pool: Ledger connections with table-operation storage installed.
        user_id: Identity supplied by the authenticated application.
        request: Named records, observed revisions and stable retry key.

    Returns:
        The receipt committed with cells and catalogue metadata.

    Raises:
        ResourceNotFoundError: Current workbook ownership does not permit access.
        IdempotencyConflictError: The key belongs to a different checked request.
        RevisionConflictError: The observed table or sheet is stale.
        ValueError: Records, formula cells or destination bounds are unsafe.
    """
    request = request.model_copy(deep=True)
    payload = _request_payload(request)
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO ledger_table_operation (user_id, idempotency_key, fingerprint) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                user_id,
                request.idempotency_key,
                fingerprint,
            )
            operation = await connection.fetchrow(
                "SELECT * FROM ledger_table_operation WHERE user_id = $1 AND idempotency_key = $2 FOR UPDATE",
                user_id,
                request.idempotency_key,
            )
            if operation["fingerprint"] != fingerprint:
                raise IdempotencyConflictError(
                    "Idempotency key belongs to a different checked request"
                )
            if operation["receipt"] is not None:
                owned = await connection.fetchval(
                    "SELECT spreadsheet_id FROM ledger_spreadsheet WHERE spreadsheet_id = $1 AND user_id = $2 FOR SHARE",
                    operation["workspace"],
                    user_id,
                )
                if owned is None:
                    raise ResourceNotFoundError("Table not found")
                return json.loads(operation["receipt"])
            target = await _lock_target(connection, user_id, request.table_id)
            layout = _plan_append(target, request)
            await check_approval(connection, operation)
            receipt = await _append_table(connection, target, (request, layout))
            await connection.execute(
                "UPDATE ledger_table_operation SET workspace = $3, receipt = $4 WHERE user_id = $1 AND idempotency_key = $2",
                user_id,
                request.idempotency_key,
                target["workspace"],
                json.dumps(receipt),
            )
        return receipt


async def _lock_target(
    connection: asyncpg.Connection, user_id: int, table_id: str
) -> asyncpg.Record:
    """Lock ownership before the source sheet and its catalogue metadata.

    Args:
        connection: Connection inside the operation transaction.
        user_id: Authenticated identity.
        table_id: Stable resource identity.

    Returns:
        Locked metadata with its grid and current sheet revision.

    Raises:
        ResourceNotFoundError: The table is absent or foreign.
    """
    owned = await connection.fetchrow(
        """
        SELECT s.workspace, s.sheet_id FROM ledger_resource r
        JOIN ledger_sheet s ON s.sheet_id = r.sheet_id
        JOIN ledger_spreadsheet w ON w.spreadsheet_id = s.workspace
        WHERE r.resource_id = $1 AND w.user_id = $2 FOR SHARE OF w
        """,
        table_id,
        user_id,
    )
    if owned is None:
        raise ResourceNotFoundError("Table not found")
    sheet = await connection.fetchrow(
        "SELECT grid, revision FROM ledger_sheet WHERE sheet_id = $1 AND workspace = $2 FOR UPDATE",
        owned["sheet_id"],
        owned["workspace"],
    )
    if sheet is None:
        raise ResourceNotFoundError("Table not found")
    target = await connection.fetchrow(
        "SELECT r.*, $2::text AS workspace, $3::jsonb AS grid, $4::bigint AS sheet_revision FROM ledger_resource r WHERE resource_id = $1 AND sheet_id = $5 FOR UPDATE",
        table_id,
        owned["workspace"],
        sheet["grid"],
        sheet["revision"],
        owned["sheet_id"],
    )
    if target is None:
        raise ResourceNotFoundError("Table not found")
    return target


def _records_for_columns(
    request: TableAppend, columns: list[dict[str, Any]]
) -> list[list[CellValue]]:
    """Resolve complete named records into the registered column order.

    Args:
        request: Checked append with named records.
        columns: Registered columns retaining their stable IDs.

    Returns:
        Literal rows in source order.

    Raises:
        ValueError: Field names differ or a value starts a formula expression.
    """
    names = [column["name"] for column in columns]
    rows = []
    for record in request.records:
        if set(record) != set(names):
            raise ValueError(
                "Each record must name every registered column exactly; use null for an explicitly blank field"
            )
        row = [record[name] for name in names]
        if all(value is None or value == "" for value in row):
            raise ValueError(
                "An appended record must contain at least one nonblank value"
            )
        if any(type(value) is str and value.lstrip().startswith("=") for value in row):
            raise ValueError(
                "Formula expressions are not supported in literal table appends"
            )
        rows.append(row)
    return rows


@dataclass(frozen=True)
class _AppendLayout:
    """Validated row values and the destination bounds for one append."""

    rows: list[list[CellValue]]
    first_row: int
    last_row: int
    table_range: str
    written_range: str


def _plan_append(target: asyncpg.Record, request: TableAppend) -> _AppendLayout:
    """Validate observed revisions and plan literal rows into empty table cells.

    Args:
        target: Locked source snapshot and registered metadata.
        request: Named records and expected source revisions.

    Returns:
        Mapped rows, insertion point and expanded bounds.

    Raises:
        RevisionConflictError: The source or catalogue is stale.
        ValueError: Fields, formula content, occupied cells or bounds are invalid.
    """
    if (
        target["sheet_revision"] != request.expected_sheet_revision
        or target["revision"] != request.expected_catalogue_revision
        or target["source_revision"] != target["sheet_revision"]
    ):
        raise RevisionConflictError(
            "Source or catalogue changed; inspect and refresh stale metadata before appending"
        )
    rows = _records_for_columns(request, json.loads(target["columns"]))
    values = json.loads(target["grid"])
    region = grid.slice_range(values, target["table_range"])
    if any(
        type(value) is str and value.lstrip().startswith("=")
        for row in region
        for value in row
    ):
        raise ValueError(
            "Tables containing formula expressions require formula propagation before appending"
        )
    first_row = target["first_row"] + 1
    for index, row in enumerate(region[1:], first_row):
        if any(value is not None and value != "" for value in row):
            first_row = index + 1
    last_row = first_row + len(rows) - 1
    new_last_row = max(last_row, target["last_row"])
    table_range = f"{grid.index_to_col(target['first_column'])}{target['first_row'] + 1}:{grid.index_to_col(target['last_column'])}{new_last_row + 1}"
    grid.validate_bounded_range(table_range, 1_000_000)
    written_range = f"{grid.index_to_col(target['first_column'])}{first_row + 1}:{grid.index_to_col(target['last_column'])}{last_row + 1}"
    if any(
        value is not None and value != ""
        for row in grid.slice_range(values, written_range)
        for value in row
    ):
        raise ValueError("Append would overwrite occupied cells")
    return _AppendLayout(rows, first_row, new_last_row, table_range, written_range)


async def _append_table(
    connection: asyncpg.Connection,
    target: asyncpg.Record,
    planned: tuple[TableAppend, _AppendLayout],
) -> dict[str, Any]:
    """Commit a validated layout and refresh its catalogue metadata together.

    Args:
        connection: Transaction holding ownership and source locks.
        target: Locked source grid and metadata.
        planned: Checked named-record append and its validated layout.

    Returns:
        A compact receipt pending transaction commit.

    Raises:
        ValueError: Another registered region overlaps the expanded bounds.
        RevisionConflictError: Source or catalogue changed before execution.
    """
    request, layout = planned
    overlap = await connection.fetchval(
        "SELECT resource_id FROM ledger_resource WHERE sheet_id = $1 AND resource_id <> $2 AND first_row <= $3 AND last_row >= $4 AND first_column <= $5 AND last_column >= $6 LIMIT 1",
        target["sheet_id"],
        request.table_id,
        layout.last_row,
        target["first_row"],
        target["last_column"],
        target["first_column"],
    )
    if overlap:
        raise ValueError("Append would overlap another registered table")
    after_revision = await _write_records(
        connection, target, (layout.first_row, layout.rows)
    )
    after_catalogue_revision = await connection.fetchval(
        "UPDATE ledger_resource SET table_range = $2, last_row = $3, record_count = record_count + $4, source_revision = $5, revision = revision + 1, updated_at = CURRENT_TIMESTAMP WHERE resource_id = $1 RETURNING revision",
        request.table_id,
        layout.table_range,
        layout.last_row,
        len(layout.rows),
        after_revision,
    )
    return _append_receipt(target, layout, (after_revision, after_catalogue_revision))


def _append_receipt(
    target: asyncpg.Record, layout: _AppendLayout, revisions: tuple[int, int]
) -> dict[str, Any]:
    """Describe verified changes without claiming formula or accounting checks.

    Args:
        target: Source identities and revisions before the write.
        layout: Validated rows and destination bounds.
        revisions: Sheet and catalogue revisions after their updates.

    Returns:
        Receipt to persist within the operation transaction.
    """
    after_revision, after_catalogue_revision = revisions
    return {
        "operation_id": f"op_{uuid.uuid4().hex}",
        "status": "committed",
        "target": {
            "table_id": target["resource_id"],
            "spreadsheet_id": target["workspace"],
            "sheet_id": target["sheet_id"],
        },
        "before_revision": target["sheet_revision"],
        "after_revision": after_revision,
        "before_catalogue_revision": target["revision"],
        "after_catalogue_revision": after_catalogue_revision,
        "changes": {
            "records_appended": len(layout.rows),
            "cells_written": len(layout.rows) * len(layout.rows[0]),
            "range": layout.written_range,
            "table_range": layout.table_range,
        },
        "validation": {
            "ownership": "passed",
            "revisions": "passed",
            "column_mapping": "passed",
            "empty_destination": "passed",
            "stored_values": "passed",
        },
        "calculation_status": "not_supported",
        "accounting_validation": "not_run",
    }


async def _write_records(
    connection: asyncpg.Connection,
    target: Mapping[str, Any],
    insertion: tuple[int, list[list[CellValue]]],
) -> int:
    """Patch JSONB rows in PostgreSQL without decoding unaffected numeric values.

    Args:
        connection: Locked operation transaction.
        target: Source sheet and registered column bounds.
        insertion: First zero-based row and mapped literal records.

    Returns:
        The new sheet revision after verifying stored cells.

    Raises:
        RuntimeError: The database did not store the intended values.
    """
    first_row, rows = insertion
    encoded = json.dumps(rows, allow_nan=False)
    parameters = (
        target["sheet_id"],
        first_row,
        target["first_column"],
        encoded,
        len(rows[0]),
        len(rows),
    )
    revision = await connection.fetchval(
        """
        UPDATE ledger_sheet SET grid = (
            SELECT jsonb_agg(CASE WHEN r >= $2::int AND r < $2::int + $6::int THEN (
                SELECT jsonb_agg(CASE WHEN c >= $3::int AND c < $3::int + $5::int
                    THEN $4::jsonb -> (r - $2::int) -> (c - $3::int)
                    ELSE COALESCE(grid -> r -> c, 'null'::jsonb) END ORDER BY c)
                FROM generate_series(0, GREATEST(COALESCE(jsonb_array_length(grid -> r), 0), $3::int + $5::int) - 1) c
            ) ELSE COALESCE(grid -> r, '[]'::jsonb) END ORDER BY r)
            FROM generate_series(0, GREATEST(jsonb_array_length(grid), $2::int + $6::int) - 1) r
        ) WHERE sheet_id = $1 RETURNING revision
        """,
        *parameters,
    )
    verified = await connection.fetchval(
        """
        SELECT bool_and((grid -> ($2::int + r) -> ($3::int + c)) IS NOT DISTINCT FROM ($4::jsonb -> r -> c))
        FROM ledger_sheet, generate_series(0, $6::int - 1) r, generate_series(0, $5::int - 1) c
        WHERE sheet_id = $1
        """,
        *parameters,
    )
    if not verified:
        raise RuntimeError("Stored append does not match the requested records")
    return revision
