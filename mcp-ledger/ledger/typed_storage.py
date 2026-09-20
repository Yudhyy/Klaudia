"""Bounded snapshots and exact grid projection for managed workbook cells."""

import hashlib
import json
from typing import Any

import asyncpg

from ledger.formula_engine import MAX_TYPED_CELLS
from ledger.resources import ResourceNotFoundError

MAX_WORKBOOK_BYTES = 1_048_576


async def read_snapshot(
    connection: asyncpg.Connection, user_id: int, workspace: str
) -> dict[str, Any]:
    """Read ownership, typed cells and revisions in one SQL snapshot.

    Args:
        connection: Standalone or caller-owned transaction connection.
        user_id: Authenticated owner.
        workspace: Selected workbook identity.

    Returns:
        Bounded source state with a deterministic fingerprint.

    Raises:
        ResourceNotFoundError: Workbook ownership is absent.
        ValueError: The workbook exceeds the synchronous calculation budget.
    """
    workbook = await connection.fetchrow(
        """SELECT
            (SELECT COALESCE(jsonb_agg(to_jsonb(s) ORDER BY s.sheet_id),'[]'::jsonb)
             FROM (SELECT sheet_id,title,revision,grid::text AS grid
                   FROM ledger_sheet WHERE workspace=w.spreadsheet_id
                   ORDER BY sheet_id LIMIT 9) s) AS sheets,
            (SELECT COALESCE(jsonb_agg(to_jsonb(c) ORDER BY c.cell_id),'[]'::jsonb)
             FROM (SELECT c.* FROM ledger_typed_cell c
                   JOIN ledger_sheet s ON s.sheet_id=c.sheet_id
                   WHERE s.workspace=w.spreadsheet_id
                   ORDER BY c.cell_id LIMIT 257) c) AS cells,
            (SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY r.resource_id),'[]'::jsonb)
             FROM (SELECT r.resource_id,r.sheet_id,r.table_range,r.revision
                   FROM ledger_resource r
                   JOIN ledger_sheet s ON s.sheet_id=r.sheet_id
                   WHERE s.workspace=w.spreadsheet_id
                   ORDER BY r.resource_id LIMIT 65) r) AS tables
        FROM ledger_spreadsheet w
        WHERE w.spreadsheet_id=$1 AND w.user_id=$2""",
        workspace,
        user_id,
    )
    if workbook is None:
        raise ResourceNotFoundError("Workbook not found")
    sheets = json.loads(workbook["sheets"])
    if (
        len(sheets) > 8
        or sum(len(sheet["grid"].encode()) for sheet in sheets) > MAX_WORKBOOK_BYTES
    ):
        raise ValueError(
            "Typed workbook exceeds eight sheets or the 1 MiB source budget"
        )
    cells = json.loads(workbook["cells"])
    if len(cells) > MAX_TYPED_CELLS:
        raise ValueError("Typed workbook exceeds 256 managed cells")
    tables = json.loads(workbook["tables"])
    if len(tables) > 64:
        raise ValueError("Typed workbook exceeds 64 registered tables")
    snapshot = {
        "workbook_id": workspace,
        "sheets": sheets,
        "cells": cells,
        "tables": tables,
    }
    snapshot["snapshot"] = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return snapshot


async def lock_workbook(
    connection: asyncpg.Connection, user_id: int, workspace: str
) -> dict[str, Any]:
    """Lock ownership then every source sheet in stable identity order.

    Args:
        connection: Active edit transaction.
        user_id: Authenticated owner.
        workspace: Selected workbook.

    Returns:
        Source state read after obtaining all write locks.

    Raises:
        ResourceNotFoundError: Current ownership is absent.
        ValueError: Source state exceeds execution limits.
    """
    owned = await connection.fetchval(
        "SELECT spreadsheet_id FROM ledger_spreadsheet WHERE spreadsheet_id=$1 AND user_id=$2 FOR UPDATE",
        workspace,
        user_id,
    )
    if owned is None:
        raise ResourceNotFoundError("Workbook not found")
    await connection.fetch(
        "SELECT sheet_id FROM ledger_sheet WHERE workspace=$1 ORDER BY sheet_id FOR UPDATE",
        workspace,
    )
    return await read_snapshot(connection, user_id, workspace)


async def save_cell(connection: asyncpg.Connection, cell: dict[str, Any]) -> None:
    """Persist one typed declaration and its actual calculated state.

    Args:
        connection: Active edit transaction.
        cell: Validated cell state.
    """
    await connection.execute(
        """INSERT INTO ledger_typed_cell
        (cell_id,sheet_id,row_number,column_number,kind,raw_value,unit,display_format,expression,dependencies,calculated_value,calculation_status,calculation_error,engine_version)
        VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9::jsonb,$10,$11,$12,$13,$14)
        ON CONFLICT(cell_id) DO UPDATE SET kind=EXCLUDED.kind,raw_value=EXCLUDED.raw_value,
        unit=EXCLUDED.unit,display_format=EXCLUDED.display_format,expression=EXCLUDED.expression,
        dependencies=EXCLUDED.dependencies,calculated_value=EXCLUDED.calculated_value,
        calculation_status=EXCLUDED.calculation_status,calculation_error=EXCLUDED.calculation_error,
        engine_version=EXCLUDED.engine_version""",
        cell["cell_id"],
        cell["sheet_id"],
        cell["row_number"],
        cell["column_number"],
        cell["kind"],
        json.dumps(cell["raw_value"]),
        cell["unit"],
        cell["display_format"],
        json.dumps(cell["expression"]) if cell["expression"] is not None else None,
        cell["dependencies"],
        cell["calculated_value"],
        cell["calculation_status"],
        cell["calculation_error"],
        cell["engine_version"],
    )


def grid_literal(cell: dict[str, Any]) -> str:
    """Encode a managed value for JSONB without passing decimals through floats.

    Args:
        cell: Current managed cell state.

    Returns:
        Exact JSON scalar text for the cached grid projection.
    """
    if cell["expression"] is not None:
        return (
            cell["calculated_value"]
            if cell["calculation_status"] == "current"
            else "null"
        )
    if cell["kind"] == "decimal":
        from decimal import Decimal

        return format(Decimal(cell["raw_value"]), "f")
    return json.dumps(cell["raw_value"], ensure_ascii=False)


async def project_cells(
    connection: asyncpg.Connection, cells: list[dict[str, Any]]
) -> dict[int, int]:
    """Patch only managed positions, preserving every other stored JSON number.

    Args:
        connection: Transaction with all sheet locks and the typed-write guard set.
        cells: Inputs and recalculated outputs changed by this operation.

    Returns:
        Changed sheet identities and final revisions.
    """
    patches: dict[int, dict[str, dict[str, str]]] = {}
    for cell in cells:
        rows = patches.setdefault(cell["sheet_id"], {})
        rows.setdefault(str(cell["row_number"] - 1), {})[
            str(cell["column_number"] - 1)
        ] = grid_literal(cell)
    revisions = {}
    for sheet_id, rows in patches.items():
        revisions[sheet_id] = await connection.fetchval(
            """UPDATE ledger_sheet SET grid=(
                SELECT jsonb_agg(CASE WHEN $2::jsonb ? r::text THEN (
                    SELECT jsonb_agg(CASE WHEN ($2::jsonb -> r::text) ? c::text
                        THEN (($2::jsonb -> r::text ->> c::text)::jsonb)
                        ELSE COALESCE(grid -> r -> c,'null'::jsonb) END ORDER BY c)
                    FROM generate_series(0,GREATEST(COALESCE(jsonb_array_length(grid -> r),0),
                        (SELECT max(key::int)+1 FROM jsonb_object_keys($2::jsonb -> r::text) key))-1) c
                ) ELSE COALESCE(grid -> r,'[]'::jsonb) END ORDER BY r)
                FROM generate_series(0,GREATEST(jsonb_array_length(grid),$3::int)-1) r
            ) WHERE sheet_id=$1 RETURNING revision""",
            sheet_id,
            json.dumps(rows),
            max(int(row) for row in rows) + 1,
        )
    return revisions
