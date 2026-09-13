"""Persist bounded agent checkpoints and serialize each task on one connection."""

from contextlib import asynccontextmanager
import hashlib
import json
from ledger.authoring import AuthoringProposal, prepare_authoring

from typing import Any, AsyncIterator
from uuid import uuid4

import asyncpg
from pydantic import ValidationError
from klaudia.core.agent.writes import ExecuteOperation

from ledger.resources import ResourceNotFoundError
from ledger.approvals import approval_payload
from ledger.table_operations import (
    TableAppendProposal,
    prepare_table_append,
    execute_prepared_operation,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_task (
    task_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL REFERENCES session(session_id) ON DELETE CASCADE,
    request_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    input_payload TEXT NOT NULL,
    checkpoint TEXT,
    status TEXT NOT NULL DEFAULT 'ready',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, session_id, request_key)
);
CREATE INDEX IF NOT EXISTS workflow_task_session ON workflow_task(user_id, session_id);
"""


class TaskBusyError(Exception):
    """Another worker currently owns this task or all task connections are busy."""


class TaskConflictError(Exception):
    """A retry key was reused with a changed user request."""


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


class TaskSession:
    """Checkpoint and execute one task while its database connection owns the lock."""

    def __init__(self, connection: asyncpg.Connection, record: dict[str, Any]) -> None:
        """Retain authenticated task state and its exclusive connection.

        Args:
            connection: Connection holding the task advisory lock.
            record: Current owned task row.
        """
        self.connection = connection
        self.record = record
        self.pool = TaskConnection(
            connection, (record["user_id"], record["session_id"])
        )

    async def load(self) -> dict[str, Any] | None:
        """Return the persisted continuation.

        Returns:
            Decoded checkpoint, or None before the first model step.
        """
        return (
            json.loads(self.record["checkpoint"]) if self.record["checkpoint"] else None
        )

    async def save(self, state: dict[str, Any]) -> None:
        """Persist the exact continuation before the next action can start.

        Args:
            state: Server-created agent messages and pending tool calls.

        Raises:
            ValueError: The continuation exceeds its one-MiB storage budget.
        """
        payload = json.dumps(state, ensure_ascii=False, allow_nan=False)
        if len(payload.encode()) > 1048576:
            raise ValueError("Task checkpoint exceeds its storage budget")
        await self.connection.execute(
            "UPDATE workflow_task SET checkpoint = $2, updated_at = CURRENT_TIMESTAMP WHERE task_id = $1",
            self.record["task_id"],
            payload,
        )
        self.record["checkpoint"] = payload

    async def set_status(self, status: str) -> None:
        """Record the observed stop reason independently of financial completion.

        Args:
            status: Agent outcome or infrastructure interruption status.
        """
        await self.connection.execute(
            "UPDATE workflow_task SET status = $2, updated_at = CURRENT_TIMESTAMP WHERE task_id = $1",
            self.record["task_id"],
            status,
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


class TaskStore:
    """Bound concurrent durable runs without holding transactions during model calls."""

    def __init__(self, database_url: str, *, require_approval: bool = False) -> None:
        """Keep task connections separate from catalogue and conversation pools.

        Args:
            database_url: Same PostgreSQL database as the app and ledger.
            require_approval: Current server policy for new main-chat appends.
        """
        self._url = database_url
        self._require_approval = require_approval
        self._pool: asyncpg.Pool | None = None
        self._reads: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Open up to four concurrent task connections and apply additive storage."""
        self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=4)
        self._reads = await asyncpg.create_pool(
            self._url, min_size=1, max_size=2, command_timeout=10
        )
        await self._reads.execute(_SCHEMA)

    async def close(self) -> None:
        """Close the task pool and release its database resources."""
        if self._pool is not None:
            await self._pool.close()
        if self._reads is not None:
            await self._reads.close()

    @asynccontextmanager
    async def _read_connection(self) -> AsyncIterator[asyncpg.Connection]:
        """Bound metadata requests separately from long-running model connections.

        Yields:
            A short-lived metadata connection.

        Raises:
            TaskBusyError: Metadata capacity is temporarily unavailable.
        """
        try:
            connection = await self._reads.acquire(timeout=5)
        except TimeoutError as exc:
            raise TaskBusyError("Task metadata is busy; retry later") from exc
        try:
            yield connection
        finally:
            await self._reads.release(connection)

    async def create(self, identity: tuple[int, int], payload: dict[str, Any]) -> str:
        """Allocate a task or return the same task for an unchanged request key.

        Args:
            identity: Authenticated user and owned session.
            payload: Original turn, model input and optional client retry key.

        Returns:
            Stable task identity.

        Raises:
            ResourceNotFoundError: Session is absent or foreign.
            TaskConflictError: Key identifies a different request.
            ValueError: Persisted input exceeds the bounded task budget.
        """
        user_id, session_id = identity
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 262144:
            raise ValueError("Task input exceeds the 256-KiB storage budget")
        request_key = payload["turn"].get("request_key") or uuid4().hex
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    key: payload["turn"][key]
                    for key in ("text", "active_workbook_id", "document_ids")
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        async with self._read_connection() as connection:
            async with connection.transaction():
                owner = await connection.fetchval(
                    "SELECT user_id FROM session WHERE session_id = $1 FOR SHARE",
                    session_id,
                )
                if owner != user_id:
                    raise ResourceNotFoundError("Task session not found")
                await connection.execute(
                    "INSERT INTO workflow_task(task_id, user_id, session_id, request_key, fingerprint, input_payload) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING",
                    "task:" + uuid4().hex,
                    user_id,
                    session_id,
                    request_key,
                    fingerprint,
                    encoded,
                )
                stored = await connection.fetchrow(
                    "SELECT task_id, fingerprint FROM workflow_task WHERE user_id = $1 AND session_id = $2 AND request_key = $3",
                    user_id,
                    session_id,
                    request_key,
                )
                if stored["fingerprint"] != fingerprint:
                    raise TaskConflictError(
                        "Request key belongs to a different task input"
                    )
                return stored["task_id"]

    async def get(self, user_id: int, task_id: str) -> dict[str, Any]:
        """Read a task only while the caller still owns its session.

        Args:
            user_id: Authenticated caller.
            task_id: Durable task identity.

        Returns:
            Internal task row for the application; never return raw checkpoints to clients.

        Raises:
            ResourceNotFoundError: Task or session ownership is absent.
        """
        async with self._read_connection() as connection:
            record = await connection.fetchrow(
                "SELECT t.* FROM workflow_task t JOIN session s ON s.session_id = t.session_id WHERE t.task_id = $1 AND t.user_id = $2 AND s.user_id = $2",
                task_id,
                user_id,
            )
        if record is None:
            raise ResourceNotFoundError("Task not found")
        return dict(record)

    @asynccontextmanager
    async def open(self, user_id: int, task_id: str) -> AsyncIterator[TaskSession]:
        """Acquire one task without waiting on a human or holding a transaction.

        Args:
            user_id: Authenticated task owner.
            task_id: Durable task identity.

        Yields:
            Checkpoint and operation access using the same locked connection.

        Raises:
            TaskBusyError: Another worker holds the task lock or pool capacity.
            ResourceNotFoundError: Task is missing or foreign.
        """
        await self.get(user_id, task_id)
        try:
            connection = await self._pool.acquire(timeout=5)
        except TimeoutError as exc:
            raise TaskBusyError("Task capacity is busy; retry later") from exc
        locked = False
        try:
            locked = await connection.fetchval(
                "SELECT pg_try_advisory_lock(hashtextextended($1, 0))", task_id
            )
            if not locked:
                raise TaskBusyError("Task is already running")
            record = await connection.fetchrow(
                "SELECT t.* FROM workflow_task t JOIN session s ON s.session_id = t.session_id WHERE t.task_id = $1 AND t.user_id = $2 AND s.user_id = $2",
                task_id,
                user_id,
            )
            if record is None:
                raise ResourceNotFoundError("Task not found")
            task = TaskSession(
                connection, {**dict(record), "require_approval": self._require_approval}
            )
            await self._check_saved_access(connection, dict(record))
            await task.set_status("running")
            yield task
        finally:
            if locked and not connection.is_closed():
                await connection.execute(
                    "SELECT pg_advisory_unlock(hashtextextended($1, 0))", task_id
                )
            await self._pool.release(connection)

    async def _check_saved_access(
        self, connection: asyncpg.Connection, record: dict[str, Any]
    ) -> None:
        """Recheck all saved tool sources before replaying their evidence.

        Args:
            connection: Connection holding the task lock.
            record: Persisted task and original input.

        Raises:
            ResourceNotFoundError: A saved source is no longer owned.
        """
        state = json.loads(record["checkpoint"]) if record["checkpoint"] else {}
        workbooks = set()
        documents = set(json.loads(record["input_payload"])["turn"]["document_ids"])
        for name, _, encoded in state.get("evidence", []):
            evidence = json.loads(encoded)
            if name == "list_authoring_sheets":
                workbooks.update(item["spreadsheet_id"] for item in evidence["sheets"])
            elif name == "inspect_sheet_region":
                workbooks.add(evidence["spreadsheet_id"])
            elif name == "search_resources":
                workbooks.update(
                    item["spreadsheet_id"] for item in evidence["candidates"]
                )
            elif name == "inspect_resource":
                workbooks.add(evidence["spreadsheet_id"])
            elif name == "financial_query":
                workbooks.update(
                    source["spreadsheet_id"] for source in evidence["sources"]
                )
            elif name == "execute_operation" and evidence.get("status") == "committed":
                workbooks.add(evidence["target"]["spreadsheet_id"])
            elif name == "search_documents":
                documents.update(item["file_id"] for item in evidence["documents"])
            elif name == "read_document_page":
                documents.add(evidence["file_id"])
        owned_workbooks = await connection.fetchval(
            "SELECT count(*) FROM ledger_spreadsheet WHERE user_id = $1 AND spreadsheet_id = ANY($2::text[])",
            record["user_id"],
            list(workbooks),
        )
        owned_documents = await connection.fetchval(
            "SELECT count(*) FROM metadata_file f JOIN session s ON s.session_id = f.session_id WHERE f.user_id = $1 AND s.user_id = $1 AND f.id = ANY($2::int[])",
            record["user_id"],
            list(documents),
        )
        if owned_workbooks != len(workbooks) or owned_documents != len(documents):
            raise ResourceNotFoundError("Saved task source is no longer available")

    async def view(self, user_id: int, task_id: str) -> dict[str, Any]:
        """Expose progress and current owned receipts without model checkpoint contents.

        Args:
            user_id: Authenticated task owner.
            task_id: Task selected by the client.

        Returns:
            Status, pending actions and authoritative committed receipts.
        """
        record = await self.get(user_id, task_id)
        state = json.loads(record["checkpoint"]) if record["checkpoint"] else {}
        references = list(state.get("operation_references", []))
        for call in state.get("pending_calls", []):
            if call["name"] == "execute_operation":
                try:
                    reference = ExecuteOperation.model_validate(
                        call["args"]
                    ).operation_ref
                except ValidationError:
                    continue
                references.append(reference)
        references = list(dict.fromkeys(references))
        async with self._read_connection() as connection:
            receipts = await connection.fetch(
                "SELECT o.* FROM ledger_table_operation o JOIN ledger_spreadsheet w ON w.spreadsheet_id = o.workspace WHERE o.user_id = $1 AND w.user_id = $1 AND o.idempotency_key = ANY($2::text[])",
                user_id,
                references,
            )
        return {
            "task_id": task_id,
            "session_id": record["session_id"],
            "status": record["status"],
            "tools_completed": state.get("calls", []),
            "pending_tools": [call["name"] for call in state.get("pending_calls", [])],
            "operation_references": references,
            "operation_receipts": [
                json.loads(row["receipt"])
                for row in receipts
                if row["receipt"] is not None
            ],
            "pending_approvals": [
                approval_payload(row)
                for row in receipts
                if row["approval_status"] == "pending" and row["receipt"] is None
            ],
        }

    async def list_session(
        self, user_id: int, session_id: int, *, offset: int = 0
    ) -> dict[str, Any]:
        """Find tasks after a lost response without requiring their original IDs.

        Args:
            user_id: Authenticated caller.
            session_id: Owned conversation.
            offset: Number of recent tasks already inspected.

        Returns:
            At most 20 task summaries and a continuation offset.

        Raises:
            ResourceNotFoundError: Session ownership is absent.
        """
        async with self._read_connection() as connection:
            owner = await connection.fetchval(
                "SELECT user_id FROM session WHERE session_id = $1", session_id
            )
            if owner != user_id:
                raise ResourceNotFoundError("Session not found")
            rows = await connection.fetch(
                """SELECT t.task_id, t.session_id, t.status, t.request_key,
                   left(t.input_payload::jsonb->'turn'->>'text', 256) AS request_summary
                   FROM workflow_task t JOIN session s ON s.session_id = t.session_id
                   WHERE t.user_id = $1 AND s.user_id = $1 AND t.session_id = $2
                   ORDER BY t.created_at DESC, t.task_id DESC LIMIT 21 OFFSET $3""",
                user_id,
                session_id,
                offset,
            )
        return {
            "tasks": [dict(row) for row in rows[:20]],
            "next_offset": offset + 20 if len(rows) > 20 else None,
        }
