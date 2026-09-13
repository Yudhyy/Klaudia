"""Checked table authoring using existing catalogue and operation storage."""

from contextlib import asynccontextmanager
import hashlib
import json
from typing import Annotated, Any, AsyncIterator, Literal
from uuid import uuid4

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from ledger import grid
from ledger.approvals import check_approval, prepare_approval
from ledger.catalogue import CatalogueStore
from ledger.errors import IdempotencyConflictError, RevisionConflictError
from ledger.resources import Name, ResourceNotFoundError, TableRegistration, TableUpdate
from ledger.table_operations import _write_records


class AuthoringProposal(BaseModel):
    """One explicit table lifecycle change with observed revision preconditions."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal[
        "create_table",
        "register_table",
        "update_table",
        "refresh_table",
        "unregister_table",
    ]
    definition: TableRegistration | None = None
    table_id: Name | None = None
    expected_catalogue_revision: Annotated[StrictInt, Field(gt=0)] | None = None
    expected_sheet_revision: Annotated[StrictInt, Field(ge=0)] | None = None
    headers: Annotated[list[Name], Field(min_length=1, max_length=256)] | None = None
    column_ids: Annotated[list[Name | None], Field(max_length=256)] | None = None

    @model_validator(mode="after")
    def check_action_fields(self) -> "AuthoringProposal":
        """Reject missing or inapplicable action fields.

        Returns:
            The complete action contract.

        Raises:
            ValueError: The action's fields are incomplete or ambiguous.
        """
        needs_definition = self.action in (
            "create_table",
            "register_table",
            "update_table",
        )
        existing = self.action in ("update_table", "refresh_table", "unregister_table")
        if needs_definition != (self.definition is not None):
            raise ValueError("This action requires exactly its table definition")
        if existing != (self.table_id is not None) or existing != (
            self.expected_catalogue_revision is not None
        ):
            raise ValueError(
                "Existing-table actions require table ID and catalogue revision"
            )
        if (not needs_definition) != (self.expected_sheet_revision is not None):
            raise ValueError(
                "Supply the sheet revision in the definition or refresh/unregister request"
            )
        if (self.action == "create_table") != (self.headers is not None):
            raise ValueError("Only create_table requires headers")
        if self.column_ids is not None and self.action != "update_table":
            raise ValueError("Explicit column mapping belongs to update_table")
        return self


class _TransactionPool:
    """Keep catalogue calls inside the operation's existing transaction."""

    def __init__(self, connection: asyncpg.Connection) -> None:
        """Bind the connection already holding ownership and source locks."""
        self.connection = connection

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        """Yield the caller's connection without releasing or committing it."""
        yield self.connection


async def prepare_authoring(
    pool: asyncpg.Pool,
    user_id: int,
    request: AuthoringProposal,
    *,
    require_approval: bool = False,
) -> dict[str, Any]:
    """Persist an exact owned authoring proposal without changing tables.

    Args:
        pool: Connected operation store.
        user_id: Authenticated caller.
        request: Explicit action and observed revisions.
        require_approval: Server policy for human consent.

    Returns:
        Fresh proposal reference and optional approval identity; retries reuse this reference.

    Raises:
        ResourceNotFoundError: The target is missing or foreign.
        ValueError: The proposal exceeds its storage budget.
    """
    payload = json.dumps(
        request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False
    )
    if len(payload.encode()) > 65536:
        raise ValueError("Authoring proposal exceeds the 65536-byte budget")
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    reference = "authoring:" + uuid4().hex
    async with pool.acquire() as connection:
        async with connection.transaction():
            workspace = await _owned_workspace(connection, user_id, request)
            await connection.execute(
                "INSERT INTO ledger_table_operation(user_id,idempotency_key,fingerprint,workspace,request_payload) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING",
                user_id,
                reference,
                fingerprint,
                workspace,
                payload,
            )
            approval_id = await prepare_approval(
                connection,
                (user_id, reference),
                require_approval or request.action == "unregister_table",
            )

    prepared = {
        "operation_ref": reference,
        "status": "prepared",
        "action": request.action,
    }
    if approval_id:
        prepared["approval_id"] = approval_id
    return prepared


async def _owned_workspace(
    connection: asyncpg.Connection, user_id: int, request: AuthoringProposal
) -> str:
    """Lock current ownership before exposing or executing a target.

    Args:
        connection: Active short transaction.
        user_id: Authenticated caller.
        request: Requested table or source sheet.

    Returns:
        Currently owned workbook identity.

    Raises:
        ResourceNotFoundError: No owned target exists.
    """
    if request.table_id is not None:
        workspace = await connection.fetchval(
            "SELECT w.spreadsheet_id FROM ledger_resource r JOIN ledger_sheet s ON s.sheet_id=r.sheet_id JOIN ledger_spreadsheet w ON w.spreadsheet_id=s.workspace WHERE r.resource_id=$1 AND w.user_id=$2 FOR SHARE OF w",
            request.table_id,
            user_id,
        )
    else:
        workspace = await connection.fetchval(
            "SELECT w.spreadsheet_id FROM ledger_sheet s JOIN ledger_spreadsheet w ON w.spreadsheet_id=s.workspace WHERE s.sheet_id=$1 AND w.user_id=$2 FOR SHARE OF w",
            request.definition.sheet_id,
            user_id,
        )
    if workspace is None:
        raise ResourceNotFoundError("Authoring target not found")
    return workspace


async def lock_authoring(
    connection: asyncpg.Connection, user_id: int, request: AuthoringProposal
) -> tuple[asyncpg.Record, asyncpg.Record | None]:
    """Lock authoring sources in workbook, sheet, then table order.

    Args:
        connection: Transaction holding the operation row lock.
        user_id: Authenticated owner.
        request: Stored checked action.

    Returns:
        Source sheet and optional existing table, with revisions checked.

    Raises:
        ResourceNotFoundError: Target ownership is absent.
        RevisionConflictError: Source or catalogue revision changed.
        ValueError: An update attempts to move identity across sheets.
    """
    workspace = await _owned_workspace(connection, user_id, request)
    sheet_id = (
        request.definition.sheet_id
        if request.definition
        else await connection.fetchval(
            "SELECT sheet_id FROM ledger_resource WHERE resource_id=$1",
            request.table_id,
        )
    )
    sheet = await connection.fetchrow(
        "SELECT * FROM ledger_sheet WHERE sheet_id=$1 AND workspace=$2 FOR UPDATE",
        sheet_id,
        workspace,
    )
    if sheet is None:
        raise ResourceNotFoundError("Authoring target not found")
    expected = (
        request.definition.expected_sheet_revision
        if request.definition
        else request.expected_sheet_revision
    )
    if sheet["revision"] != expected:
        raise RevisionConflictError("Source sheet changed; inspect it again")
    current = None
    if request.table_id:
        current = await connection.fetchrow(
            "SELECT * FROM ledger_resource WHERE resource_id=$1 FOR UPDATE",
            request.table_id,
        )
        if current is None:
            raise ResourceNotFoundError("Authoring target not found")
        if current["sheet_id"] != sheet_id:
            raise ValueError("Table identity cannot move between sheets")
        if current["revision"] != request.expected_catalogue_revision:
            raise RevisionConflictError("Catalogue changed; inspect it again")
    return sheet, current


async def execute_authoring(
    pool: asyncpg.Pool, user_id: int, reference: str
) -> dict[str, Any]:
    """Commit table metadata, optional headers and the receipt exactly once.

    Args:
        pool: Connected operation store or task-owned connection adapter.
        user_id: Authenticated caller.
        reference: Original prepared authoring identity.

    Returns:
        Committed receipt, including unchanged exact replay.

    Raises:
        ResourceNotFoundError: The operation or current ownership is absent.
        RevisionConflictError: The stored proposal has stale revisions.
        ValueError: Layout, headers or mapping are invalid.
    """
    async with pool.acquire() as connection:
        async with connection.transaction():
            operation = await connection.fetchrow(
                "SELECT * FROM ledger_table_operation WHERE user_id=$1 AND idempotency_key=$2 FOR UPDATE",
                user_id,
                reference,
            )
            if operation is None:
                raise ResourceNotFoundError("Operation not found")
            owned = await connection.fetchval(
                "SELECT spreadsheet_id FROM ledger_spreadsheet WHERE spreadsheet_id=$1 AND user_id=$2 FOR SHARE",
                operation["workspace"],
                user_id,
            )
            if owned is None:
                raise ResourceNotFoundError("Operation not found")
            if operation["receipt"] is not None:
                return json.loads(operation["receipt"])
            if (
                hashlib.sha256(operation["request_payload"].encode()).hexdigest()
                != operation["fingerprint"]
            ):
                raise IdempotencyConflictError("Stored authoring proposal changed")
            request = AuthoringProposal.model_validate_json(
                operation["request_payload"]
            )
            source = await lock_authoring(connection, user_id, request)
            await check_approval(connection, operation)
            receipt = await _apply_authoring(connection, request, source)
            await connection.execute(
                "UPDATE ledger_table_operation SET receipt=$3::jsonb WHERE user_id=$1 AND idempotency_key=$2",
                user_id,
                reference,
                json.dumps(receipt),
            )
            return receipt


async def _apply_authoring(
    connection: asyncpg.Connection,
    request: AuthoringProposal,
    source: tuple[asyncpg.Record, asyncpg.Record | None],
) -> dict[str, Any]:
    """Apply one validated action and describe only changes that actually occurred.

    Args:
        connection: Transaction holding all checked source locks.
        request: Exact stored action.
        source: Locked source sheet and optional existing table.

    Returns:
        Compact receipt ready to commit with the operation.
    """
    sheet, current = source
    catalogue = CatalogueStore(_TransactionPool(connection))
    definition = request.definition
    cells_changed = 0
    if request.action == "create_table":
        grid.validate_bounded_range(definition.table_range, 1_000_000)
        _, first_column, _, last_column = grid.parse_range(definition.table_range)
        if len(request.headers) != last_column - first_column + 1:
            raise ValueError("Header count must match the table width")
        rows = json.loads(sheet["grid"])
        if any(
            value is not None and value != ""
            for row in grid.slice_range(rows, definition.table_range)
            for value in row
        ):
            raise ValueError("A new table requires a blank destination region")
        first_row = grid.parse_range(definition.table_range)[0]
        if first_row + first_column + len(request.headers) > 1_000_000:
            raise ValueError("New header placement exceeds the grid growth budget")
        if any(header.startswith("=") for header in request.headers):
            raise ValueError("Table headers must be literal text, not formulas")
        revision = await _write_records(
            connection,
            {"sheet_id": sheet["sheet_id"], "first_column": first_column},
            (first_row, [request.headers]),
        )
        definition = definition.model_copy(update={"expected_sheet_revision": revision})
        cells_changed = len(request.headers)
    if request.action in ("create_table", "register_table"):
        descriptor = await catalogue.register(sheet["workspace"], definition)
    elif request.action in ("update_table", "refresh_table"):
        if request.action == "refresh_table":
            definition = TableRegistration(
                **{
                    key: current[key]
                    for key in (
                        "sheet_id",
                        "table_range",
                        "name",
                        "description",
                        "grain",
                        "aliases",
                        "entity",
                        "period_start",
                        "period_end",
                    )
                },
                expected_sheet_revision=sheet["revision"],
            )
        descriptor = await catalogue.update(
            sheet["workspace"],
            TableUpdate(
                table_id=request.table_id,
                expected_catalogue_revision=request.expected_catalogue_revision,
                definition=definition,
                column_ids=request.column_ids,
            ),
        )
    else:
        await connection.execute(
            "DELETE FROM ledger_resource WHERE resource_id=$1", request.table_id
        )
        descriptor = {
            "table_id": request.table_id,
            "catalogue_revision": None,
            "current_sheet_revision": sheet["revision"],
        }
    return {
        "operation_id": "op_" + uuid4().hex,
        "status": "committed",
        "operation_type": request.action,
        "target": {
            "spreadsheet_id": sheet["workspace"],
            "sheet_id": sheet["sheet_id"],
            "table_id": descriptor["table_id"],
        },
        "before_revision": sheet["revision"],
        "after_revision": descriptor["current_sheet_revision"],
        "before_catalogue_revision": current["revision"] if current else None,
        "after_catalogue_revision": descriptor["catalogue_revision"],
        "changes": {
            "cells_changed": cells_changed,
            "tables_created": int(request.action in ("create_table", "register_table")),
            "tables_unregistered": int(request.action == "unregister_table"),
        },
        "calculation_status": "not_requested",
        "validation": {
            "ownership": "passed",
            "revisions": "passed",
            "accounting_invariants": "not_run",
        },
    }
