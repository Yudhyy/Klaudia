"""Execute checked operations on the connection holding the durable task lock."""

import json
from typing import Any

import asyncpg

from ledger.authoring import prepare_authoring
from ledger.typed_cells import inspect_workbook, prepare_typed_edit
from ledger.table_operations import prepare_table_append, execute_prepared_operation
from ledger.approvals import approval_payload
from ledger_core.append_contracts import TableAppendProposal
from ledger_core.authoring_contracts import AuthoringProposal
from ledger_core.typed_contracts import TypedEditProposal
from ledger_core.resources import ResourceNotFoundError
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator


class TaskConnection:
    """Keep ledger transactions on the connection holding the task lock."""

    def __init__(
        self, connection: asyncpg.Connection, identity: tuple[int, int]
    ) -> None:
        """Bind the task-owned connection.

        Args:
            connection: Connection holding the session advisory lock.
            identity: Authenticated user and task session.
        """
        self.connection = connection
        self.identity = identity

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        """Yield the same connection without releasing its task lock.

        Yields:
            The active task connection for a short ledger transaction.
        """
        async with self.connection.transaction():
            user_id, session_id = self.identity
            owner = await self.connection.fetchval(
                "SELECT user_id FROM session WHERE session_id = $1 FOR SHARE",
                session_id,
            )
            if owner != user_id:
                raise ResourceNotFoundError("Task session not found")
            yield self.connection

    async def fetchval(self, query: str, *arguments: Any) -> Any:
        """Read operation payloads through the task-owned connection.

        Args:
            query: Parameterized ledger query.
            arguments: Bound SQL values.

        Returns:
            The scalar operation value.
        """
        return await self.connection.fetchval(query, *arguments)


class TaskOperations:
    """Bind ledger operations to the task's owner, policy and locked connection."""

    def __init__(self, connection: asyncpg.Connection, record: dict[str, Any]) -> None:
        """Reuse the acquired task connection without opening another pool.

        Args:
            connection: Connection holding the task advisory lock.
            record: Authenticated task identity and server approval policy.
        """
        self.record = record
        self.pool = TaskConnection(
            connection, (record["user_id"], record["session_id"])
        )

    async def prepare(
        self, user_id: int, request: TableAppendProposal
    ) -> dict[str, str]:
        """Prepare a checked append on the task-owned connection.

        Args:
            user_id: Server-authenticated task identity.
            request: Exact inspected proposal.

        Returns:
            Original durable operation reference.
        """
        if user_id != self.record["user_id"]:
            raise ResourceNotFoundError("Task not found")
        return await prepare_table_append(
            self.pool,
            user_id,
            request,
            require_approval=self.record["require_approval"],
        )

    async def prepare_authoring(
        self, user_id: int, request: AuthoringProposal
    ) -> dict[str, Any]:
        """Persist authoring on the task-owned connection under server approval policy.

        Args:
            user_id: Server-authenticated task owner.
            request: Exact authoring action and observed revisions.

        Returns:
            Original stored reference with optional approval identity.
        """
        if user_id != self.record["user_id"]:
            raise ResourceNotFoundError("Task not found")
        return await prepare_authoring(
            self.pool,
            user_id,
            request,
            require_approval=self.record["require_approval"],
        )

    async def inspect_typed(self, user_id: int, workbook_id: str) -> dict[str, Any]:
        """Read typed evidence on the durable task's connection.

        Args:
            user_id: Authenticated owner.
            workbook_id: Selected workbook identity.

        Returns:
            Bounded typed source evidence.
        """
        if user_id != self.record["user_id"]:
            raise ResourceNotFoundError("Task not found")
        return await inspect_workbook(self.pool, user_id, workbook_id)

    async def prepare_typed(
        self, user_id: int, request: TypedEditProposal
    ) -> dict[str, Any]:
        """Prepare typed edits under the task's current approval policy.

        Args:
            user_id: Authenticated owner.
            request: Exact typed edit intent.

        Returns:
            Stored operation reference with optional approval identity.
        """
        if user_id != self.record["user_id"]:
            raise ResourceNotFoundError("Task not found")
        return await prepare_typed_edit(
            self.pool,
            user_id,
            request,
            require_approval=self.record["require_approval"],
        )

    async def execute(self, user_id: int, operation_ref: str) -> dict[str, Any]:
        """Execute under the same connection and lock as the pending checkpoint.

        Args:
            user_id: Server-authenticated task identity.
            operation_ref: Stored append reference, including uncertain retries.

        Returns:
            Committed ledger receipt.
        """
        if user_id != self.record["user_id"]:
            raise ResourceNotFoundError("Task not found")
        return await execute_prepared_operation(self.pool, user_id, operation_ref)


async def operation_evidence(
    connection: asyncpg.Connection, user_id: int, references: list[str]
) -> dict[str, Any]:
    """Read authoritative receipts and approvals under current workbook ownership.

    Args:
        connection: Short-lived task metadata connection.
        user_id: Authenticated task owner.
        references: Original operation identities found in saved agent state.

    Returns:
        Committed receipts and exact pending approval payloads.
    """
    receipts = await connection.fetch(
        "SELECT o.* FROM ledger_table_operation o JOIN ledger_spreadsheet w ON w.spreadsheet_id = o.workspace WHERE o.user_id = $1 AND w.user_id = $1 AND o.idempotency_key = ANY($2::text[])",
        user_id,
        references,
    )
    return {
        "operation_receipts": [
            json.loads(row["receipt"]) for row in receipts if row["receipt"] is not None
        ],
        "pending_approvals": [
            approval_payload(row)
            for row in receipts
            if row["approval_status"] == "pending" and row["receipt"] is None
        ],
    }
