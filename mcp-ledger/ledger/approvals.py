"""Human decisions bound to the exact payload and revisions of stored appends."""

import hashlib
import json
from typing import Any

import asyncpg

from ledger.errors import ApprovalRequiredError, IdempotencyConflictError
from ledger.resources import ResourceNotFoundError


def approval_payload(operation: asyncpg.Record) -> dict[str, Any]:
    """Describe the persisted proposal without inferring accounting validation.

    Args:
        operation: Owned operation row with its approval state.

    Returns:
        Exact proposal fields and the approval identity shown to the user.
    """
    proposal = json.loads(operation["request_payload"])
    proposal.pop("idempotency_key", None)
    return {
        "approval_id": operation["approval_id"],
        "action": "checked_table_append",
        "operation_ref": operation["idempotency_key"],
        "proposal": proposal,
        "summary": f"Append {len(proposal['records'])} record(s) to the selected table",
        "rows_affected": len(proposal["records"]),
        "columns_affected": len(proposal["records"][0]),
        "expires_at": operation["approval_expires_at"].isoformat(),
    }


async def check_approval(
    connection: asyncpg.Connection, operation: asyncpg.Record
) -> None:
    """Enforce approval inside the same transaction that will change financial cells.

    Args:
        connection: Connection holding the operation row lock.
        operation: Exact locked request and current approval state.

    Raises:
        ApprovalRequiredError: A human decision is pending.
        ValueError: Approval was rejected or expired.
        IdempotencyConflictError: Approval belongs to another payload fingerprint.
    """
    if not operation["approval_required"]:
        return
    if operation["approval_status"] == "rejected":
        raise ValueError("Operation approval was rejected")
    current = await connection.fetchval("SELECT clock_timestamp()")
    if (
        operation["approval_expires_at"] is None
        or operation["approval_expires_at"] <= current
    ):
        raise ValueError("Operation approval expired; prepare a new approval")
    if operation["approval_status"] != "approved":
        raise ApprovalRequiredError(approval_payload(operation))
    if operation["approved_fingerprint"] != operation["fingerprint"]:
        raise IdempotencyConflictError("Approval does not match the stored proposal")


async def decide_approval(
    pool: asyncpg.Pool, user_id: int, approval_id: str, *, approve: bool
) -> dict[str, Any]:
    """Record a human decision after rechecking ownership and proposal revisions.

    Args:
        pool: Existing ledger database.
        user_id: Authenticated human decision maker.
        approval_id: Current approval identity, not a model-selected permission.
        approve: Explicit HTTP decision; unavailable to agent tools.

    Returns:
        Recorded decision and original operation reference, without claiming execution.

    Raises:
        ResourceNotFoundError: Approval is missing or no longer owned.
        ValueError: Approval expired or was already resolved differently.
        RevisionConflictError: Source revisions changed before approval.
    """
    from ledger.table_operations import TableAppend, _lock_target, _plan_append

    async with pool.acquire() as connection:
        async with connection.transaction():
            operation = await connection.fetchrow(
                "SELECT * FROM ledger_table_operation WHERE user_id = $1 AND approval_id = $2 FOR UPDATE",
                user_id,
                approval_id,
            )
            if operation is None:
                raise ResourceNotFoundError("Approval not found")
            owned = await connection.fetchval(
                "SELECT spreadsheet_id FROM ledger_spreadsheet WHERE spreadsheet_id = $1 AND user_id = $2 FOR SHARE",
                operation["workspace"],
                user_id,
            )
            if owned is None:
                raise ResourceNotFoundError("Approval not found")
            decision = "approved" if approve else "rejected"
            if operation["approval_status"] == decision:
                return {
                    "approval_id": approval_id,
                    "approved": approve,
                    "operation_ref": operation["idempotency_key"],
                }
            if operation["approval_status"] != "pending":
                raise ValueError("Approval was already resolved")
            current = await connection.fetchval("SELECT clock_timestamp()")
            if operation["approval_expires_at"] <= current:
                raise ValueError("Operation approval expired")
            if (
                hashlib.sha256(operation["request_payload"].encode()).hexdigest()
                != operation["fingerprint"]
            ):
                raise IdempotencyConflictError("Stored proposal fingerprint changed")
            if approve:
                request = TableAppend.model_validate_json(operation["request_payload"])
                target = await _lock_target(connection, user_id, request.table_id)
                _plan_append(target, request)
            await connection.execute(
                "UPDATE ledger_table_operation SET approval_status = $3, approved_fingerprint = $4 WHERE user_id = $1 AND approval_id = $2",
                user_id,
                approval_id,
                decision,
                operation["fingerprint"] if approve else None,
            )
            return {
                "approval_id": approval_id,
                "approved": approve,
                "operation_ref": operation["idempotency_key"],
            }
