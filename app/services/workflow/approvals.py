"""Expose checked ledger approvals through the existing human-decision API."""

from typing import Any

import asyncpg

from ledger.approvals import approval_payload, decide_approval
from ledger.table_operations import execute_prepared_append


class CheckedApprovals:
    """Resolve exact persisted appends without asking a model to recreate them."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Reuse the ledger operation store.

        Args:
            pool: Connected ledger database with approval fields installed.
        """
        self._pool = pool

    async def list_pending(self, user_id: int) -> list[dict[str, Any]]:
        """List up to 100 current owned append approvals.

        Args:
            user_id: JWT-derived identity.

        Returns:
            Exact proposals with their approval and operation identities.
        """
        rows = await self._pool.fetch(
            """SELECT o.* FROM ledger_table_operation o JOIN ledger_spreadsheet w
               ON w.spreadsheet_id = o.workspace WHERE o.user_id = $1 AND w.user_id = $1
               AND o.approval_status = 'pending' AND o.receipt IS NULL
               AND o.approval_expires_at > clock_timestamp()
               ORDER BY o.created_at LIMIT 100""",
            user_id,
        )
        return [approval_payload(row) for row in rows]

    async def approve(self, user_id: int, approval_id: str) -> dict[str, Any]:
        """Record consent and execute the same proposal under fresh ledger checks.

        Args:
            user_id: Authenticated human decision maker.
            approval_id: Exact proposal identity shown by the client.

        Returns:
            Committed receipt, including idempotent decision retries.

        Raises:
            RevisionConflictError: Revisions changed; no stale operation commits.
            ResourceNotFoundError: Current ownership no longer permits the action.
        """
        decision = await decide_approval(self._pool, user_id, approval_id, approve=True)
        receipt = await execute_prepared_append(
            self._pool, user_id, decision["operation_ref"]
        )
        return {**decision, "executed": True, "result": receipt}

    async def reject(self, user_id: int, approval_id: str) -> dict[str, Any]:
        """Reject the proposal without changing financial cells.

        Args:
            user_id: Authenticated human decision maker.
            approval_id: Current proposal identity.

        Returns:
            Durable rejection; no execution was attempted.
        """
        decision = await decide_approval(
            self._pool, user_id, approval_id, approve=False
        )
        return {**decision, "executed": False}
