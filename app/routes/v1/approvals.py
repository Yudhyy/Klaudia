"""Approve or reject irreversible sheet operations (deterministic HITL).

The tool guard parks destructive calls instead of running them; the client
renders a button per pending record and posts the decision here. Approval
replays the stored call verbatim — the model is not consulted again, so a
confirmed delete does exactly what was shown to the user and nothing else.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.helpers.auth import get_current_user
from app.helpers.ratelimit import chat_limit, limiter
from app.services.core.approvals import ApprovalNotFoundError, ApprovalService
from ledger.resources import ResourceNotFoundError
from ledger.errors import RevisionConflictError, IdempotencyConflictError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")


def _service(request: Request) -> ApprovalService:
    service = request.app.state.container.approvals
    if service is None:
        raise HTTPException(status_code=503, detail="Approvals unavailable")
    return service


@router.get("")
@limiter.limit(chat_limit)
async def list_approvals(
    request: Request,
    response: Response,
    user_id: int = Depends(get_current_user),
) -> list[dict]:
    """Pending destructive operations awaiting this user's decision."""
    return await _service(request).list_pending(user_id)


@router.post("/{approval_id}")
@limiter.limit(chat_limit)
async def resolve_approval(
    approval_id: str,
    body: ApprovalDecision,
    request: Request,
    response: Response,
    user_id: int = Depends(get_current_user),
) -> dict:
    """Execute or discard a parked operation."""
    service = _service(request)
    try:
        if body.decision == "approve":
            return await service.approve(user_id, approval_id)
        return await service.reject(user_id, approval_id)
    except (ApprovalNotFoundError, ResourceNotFoundError):
        raise HTTPException(status_code=404, detail="Approval not found")
    except (RevisionConflictError, IdempotencyConflictError, ValueError) as exc:
        if approval_id.startswith("checked:"):
            raise HTTPException(status_code=409, detail=str(exc))
        raise HTTPException(status_code=500, detail="Approval execution failed")
    except Exception as exc:
        logger.error("Approval %s failed: %s", approval_id, exc)
        raise HTTPException(status_code=500, detail="Approval execution failed")
