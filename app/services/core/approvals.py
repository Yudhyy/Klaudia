"""Pending approvals for irreversible sheet operations (deterministic HITL).

The tool guard refuses destructive calls past the policy threshold and
parks them here. The client renders an approve/reject button from the
pending record; approving replays the STORED call verbatim through the
raw MCP tool, so the decision to execute never passes back through the
model. Rejecting simply drops it.

"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_approval (
    approval_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    session_id INTEGER,
    spreadsheet_id TEXT,
    tool_name TEXT NOT NULL,
    args TEXT NOT NULL,
    summary TEXT NOT NULL,
    rows_affected INTEGER NOT NULL DEFAULT 0,
    columns_affected INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
)
"""


class ApprovalNotFoundError(Exception):
    """Raised when an approval id is unknown, foreign, or already resolved."""


@dataclass(frozen=True)
class PendingApproval:
    approval_id: str
    tool_name: str
    args: dict[str, Any]
    summary: str
    rows_affected: int
    columns_affected: int

    def as_payload(self) -> dict[str, Any]:
        """Client-facing shape backing the approve/reject buttons."""
        return {
            "approval_id": self.approval_id,
            "action": self.tool_name,
            "sheet": self.args.get("sheet"),
            "summary": self.summary,
            "rows_affected": self.rows_affected,
            "columns_affected": self.columns_affected,
        }


class ApprovalService:
    """Store and resolve destructive-operation approvals."""

    def __init__(self, db: Any, sheets_registry: Any) -> None:
        self._db = db
        self._registry = sheets_registry
        self.checked: Any = None

    async def ensure_schema(self) -> None:
        await self._db.execute(_SCHEMA)

    async def create(
        self,
        user_id: int,
        session_id: int | None,
        spreadsheet_id: str | None,
        tool_name: str,
        args: dict[str, Any],
        summary: str,
        rows_affected: int,
        columns_affected: int,
    ) -> PendingApproval:
        approval_id = uuid.uuid4().hex
        await self._db.execute(
            "INSERT INTO pending_approval (approval_id, user_id, session_id, "
            "spreadsheet_id, tool_name, args, summary, rows_affected, "
            "columns_affected, status) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            (
                approval_id,
                user_id,
                session_id,
                spreadsheet_id,
                tool_name,
                json.dumps(args, ensure_ascii=False),
                summary,
                rows_affected,
                columns_affected,
                STATUS_PENDING,
            ),
        )
        return PendingApproval(
            approval_id=approval_id,
            tool_name=tool_name,
            args=args,
            summary=summary,
            rows_affected=rows_affected,
            columns_affected=columns_affected,
        )

    async def list_pending(self, user_id: int) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT approval_id, tool_name, args, summary, rows_affected, "
            "columns_affected FROM pending_approval "
            "WHERE user_id = $1 AND status = $2 ORDER BY created_at",
            (user_id, STATUS_PENDING),
        )
        pending = [self._to_approval(r).as_payload() for r in rows]
        if self.checked is not None:
            pending.extend(await self.checked.list_pending(user_id))
        return pending

    async def approve(self, user_id: int, approval_id: str) -> dict[str, Any]:
        """Execute a parked operation exactly as it was proposed.

        Args:
            user_id: Authenticated user; foreign ids are not found.
            approval_id: The parked operation.

        Returns:
            Dict with the executed action and the raw tool result.

        Raises:
            ApprovalNotFoundError: Unknown, foreign, or already resolved.
        """
        if approval_id.startswith("checked:") and self.checked is not None:
            return await self.checked.approve(user_id, approval_id)
        approval = await self._claim(user_id, approval_id, STATUS_APPROVED)
        tool = next(
            (t for t in self._registry.tools if t.name == approval.tool_name), None
        )
        if tool is None:
            raise ApprovalNotFoundError(f"Tool '{approval.tool_name}' unavailable")
        result = await tool.coroutine(**approval.args)
        logger.info(
            "Approved destructive op %s (%s) for user %s",
            approval.tool_name,
            approval.approval_id,
            user_id,
        )
        return {"approval_id": approval.approval_id, "executed": True, "result": result}

    async def reject(self, user_id: int, approval_id: str) -> dict[str, Any]:
        if approval_id.startswith("checked:") and self.checked is not None:
            return await self.checked.reject(user_id, approval_id)
        approval = await self._claim(user_id, approval_id, STATUS_REJECTED)
        return {"approval_id": approval.approval_id, "executed": False}

    async def _claim(
        self, user_id: int, approval_id: str, new_status: str
    ) -> PendingApproval:
        """Atomically move a pending approval to a terminal status.

        The status guard in the UPDATE is what makes double-approval
        impossible: the second request updates zero rows and 404s, so a
        double-clicked delete cannot run twice.
        """
        row = await self._db.fetchone(
            "SELECT approval_id, tool_name, args, summary, rows_affected, "
            "columns_affected FROM pending_approval "
            "WHERE approval_id = $1 AND user_id = $2 AND status = $3",
            (approval_id, user_id, STATUS_PENDING),
        )
        if row is None:
            raise ApprovalNotFoundError(f"Approval '{approval_id}' not found")
        changed = await self._db.execute(
            "UPDATE pending_approval SET status = $1, "
            "resolved_at = CURRENT_TIMESTAMP "
            "WHERE approval_id = $2 AND status = $3",
            (new_status, approval_id, STATUS_PENDING),
        )
        if not changed:
            raise ApprovalNotFoundError(f"Approval '{approval_id}' not found")
        return self._to_approval(row)

    @staticmethod
    def _to_approval(row: dict[str, Any]) -> PendingApproval:
        return PendingApproval(
            approval_id=row["approval_id"],
            tool_name=row["tool_name"],
            args=json.loads(row["args"]),
            summary=row["summary"],
            rows_affected=row["rows_affected"],
            columns_affected=row["columns_affected"],
        )
