"""Owned sheet inspection and checked catalogue authoring for clients and agents."""

import json
from typing import Any

import asyncpg

from ledger import grid
from ledger.authoring import AuthoringProposal, prepare_authoring
from ledger.evidence import bounded_evidence
from ledger.resources import ResourceNotFoundError
from ledger.table_operations import execute_prepared_operation


class AuthoringService:
    """Expose bounded placement evidence without granting model-selected scope."""

    def __init__(self, pool: asyncpg.Pool, *, require_approval: bool = False) -> None:
        """Bind ledger connections and server approval policy.

        Args:
            pool: Ledger connections with catalogue storage.
            require_approval: Human consent policy for authoring proposals.
        """
        self._pool = pool
        self._require_approval = require_approval

    async def sheets(
        self, user_id: int, *, workbook_id: str | None = None, offset: int = 0
    ) -> dict[str, Any]:
        """List a bounded page of owned sheet identities for explicit authoring.

        Args:
            user_id: Authenticated owner.
            workbook_id: Optional target filter, never an access grant.
            offset: Number of owned sheets already inspected.

        Returns:
            At most 20 sheet identities and a continuation offset.
        """
        rows = await self._pool.fetch(
            "SELECT s.sheet_id, s.title AS sheet_name, s.revision AS sheet_revision, w.spreadsheet_id, w.name AS workbook_name FROM ledger_sheet s JOIN ledger_spreadsheet w ON w.spreadsheet_id=s.workspace WHERE w.user_id=$1 AND ($2::text IS NULL OR w.spreadsheet_id=$2) ORDER BY s.sheet_id LIMIT 21 OFFSET $3",
            user_id,
            workbook_id,
            offset,
        )
        return bounded_evidence(
            {
                "sheets": [dict(row) for row in rows[:20]],
                "next_offset": offset + 20 if len(rows) > 20 else None,
            }
        )

    async def inspect(
        self, user_id: int, sheet_id: int, table_range: str
    ) -> dict[str, Any]:
        """Inspect a finite placement region and its overlapping registered tables.

        Args:
            user_id: Authenticated owner.
            sheet_id: Stable source identity.
            table_range: Finite region of at most 4096 cells.

        Returns:
            Observed sheet revision, exact selected JSON values and table identities.

        Raises:
            ResourceNotFoundError: The sheet is missing or foreign.
            ValueError: The requested region or returned evidence exceeds its budget.
        """
        grid.validate_bounded_range(table_range, 4096)
        first_row, first_column, last_row, last_column = grid.parse_range(table_range)
        async with self._pool.acquire() as connection:
            async with connection.transaction(
                isolation="repeatable_read", readonly=True
            ):
                sheet = await connection.fetchrow(
                    "SELECT s.sheet_id, s.title, s.grid, s.revision, s.workspace FROM ledger_sheet s JOIN ledger_spreadsheet w ON w.spreadsheet_id=s.workspace WHERE s.sheet_id=$1 AND w.user_id=$2",
                    sheet_id,
                    user_id,
                )
                if sheet is None:
                    raise ResourceNotFoundError("Sheet not found")
                tables = await connection.fetch(
                    "SELECT resource_id AS table_id, name, table_range, revision AS catalogue_revision FROM ledger_resource WHERE sheet_id=$1 AND first_row<=$2 AND last_row>=$3 AND first_column<=$4 AND last_column>=$5 ORDER BY resource_id LIMIT 21",
                    sheet_id,
                    last_row,
                    first_row,
                    last_column,
                    first_column,
                )
        if len(tables) > 20:
            raise ValueError("Too many overlapping tables; inspect a smaller region")
        return bounded_evidence(
            {
                "sheet_id": sheet_id,
                "spreadsheet_id": sheet["workspace"],
                "sheet_name": sheet["title"],
                "sheet_revision": sheet["revision"],
                "range": table_range,
                "fractional_numbers": "exact decimal strings",
                "values": grid.slice_range(
                    json.loads(sheet["grid"], parse_float=str), table_range
                ),
                "tables": [dict(row) for row in tables],
            }
        )

    async def prepare(
        self, user_id: int, proposal: AuthoringProposal
    ) -> dict[str, Any]:
        """Persist one owned proposal under the configured approval policy.

        Args:
            user_id: Authenticated owner.
            proposal: Exact requested authoring action.

        Returns:
            Stored reference with optional approval identity.
        """
        return await prepare_authoring(
            self._pool, user_id, proposal, require_approval=self._require_approval
        )

    async def execute(self, user_id: int, reference: str) -> dict[str, Any]:
        """Commit or replay the exact stored checked operation.

        Args:
            user_id: Authenticated owner.
            reference: Original prepared identity.

        Returns:
            The committed receipt.
        """
        return await execute_prepared_operation(self._pool, user_id, reference)
