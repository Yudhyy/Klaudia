"""Revision-checked appends with receipts committed alongside their writes."""

import hashlib
import json
import uuid
from typing import Any

import asyncpg

from ledger_core import grid
from ledger_core.append_contracts import (
    AppendRow as AppendRow,
    AppendRows as AppendRows,
    CellValue as CellValue,
)
from ledger_core.errors import (
    IdempotencyConflictError,
    RevisionConflictError,
    SheetNotFoundError,
)


async def execute_append(
    pool: asyncpg.Pool, workspace: str, request: AppendRows
) -> dict[str, Any]:
    """Commit an append once, or return the original committed receipt.

    Args:
        pool: Ledger database connections.
        workspace: Workbook bound by the caller's authorisation layer.
        request: Validated target, source revision, key and literal rows.

    Returns:
        The receipt stored in the same transaction as the appended rows.

    Raises:
        IdempotencyConflictError: The key was used for different arguments.
        RevisionConflictError: The source revision is stale.
        SheetNotFoundError: The target is absent from this workbook.
    """
    # Freeze nested rows before yielding so the fingerprint matches the write.
    frozen_request = request.model_copy(deep=True)
    payload = frozen_request.model_dump_json()
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO ledger_operation (workspace, idempotency_key, fingerprint) "
                "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                workspace,
                request.idempotency_key,
                fingerprint,
            )
            operation = await connection.fetchrow(
                "SELECT fingerprint, receipt FROM ledger_operation "
                "WHERE workspace = $1 AND idempotency_key = $2 FOR UPDATE",
                workspace,
                request.idempotency_key,
            )
            if operation["fingerprint"] != fingerprint:
                raise IdempotencyConflictError(
                    "Idempotency key belongs to a different request"
                )
            if operation["receipt"] is not None:
                return json.loads(operation["receipt"])
            receipt = await _append_locked(connection, workspace, frozen_request)
            await connection.execute(
                "UPDATE ledger_operation SET receipt = $3 "
                "WHERE workspace = $1 AND idempotency_key = $2",
                workspace,
                request.idempotency_key,
                json.dumps(receipt),
            )
        return receipt


async def _append_locked(
    connection: asyncpg.Connection, workspace: str, request: AppendRows
) -> dict[str, Any]:
    """Lock the target and append only when its revision matches the request.

    Args:
        connection: Connection inside the operation transaction.
        workspace: Authorised workbook scope.
        request: Private copy of the validated append.

    Returns:
        An operation receipt, pending the caller's transaction commit.

    Raises:
        SheetNotFoundError: The sheet is outside the workbook or was deleted.
        RevisionConflictError: The sheet changed since it was read.
    """
    sheet = await connection.fetchrow(
        "SELECT grid, revision FROM ledger_sheet "
        "WHERE workspace = $1 AND sheet_id = $2 FOR UPDATE",
        workspace,
        request.sheet_id,
    )
    if sheet is None:
        raise SheetNotFoundError("Sheet not found in the active workbook")
    if sheet["revision"] != request.expected_revision:
        raise RevisionConflictError(
            f"Expected revision {request.expected_revision}, found {sheet['revision']}; "
            "read the sheet again before proposing another operation"
        )
    old_grid = json.loads(sheet["grid"])
    first_row = grid.last_data_row(old_grid) + 1
    new_grid = grid.write_range(old_grid, f"A{first_row}", request.rows)
    after_revision = await connection.fetchval(
        "UPDATE ledger_sheet SET grid = $2 WHERE sheet_id = $1 RETURNING revision",
        request.sheet_id,
        json.dumps(new_grid),
    )
    return {
        "operation_id": f"op_{uuid.uuid4().hex}",
        "status": "committed",
        "target": {"spreadsheet_id": workspace, "sheet_id": request.sheet_id},
        "before_revision": sheet["revision"],
        "after_revision": after_revision,
        "changes": {
            "rows_inserted": len(request.rows),
            "cells_written": sum(len(row) for row in request.rows),
            "first_row": first_row,
        },
        "validation": {"revision": "passed", "literal_values": "passed"},
        "calculation_status": "not_supported",
        "accounting_validation": "not_run",
    }
