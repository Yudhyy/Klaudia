"""Authenticated table authoring with explicit proposals and operation receipts."""

from typing import Any, Awaitable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.helpers.auth import get_current_user
from app.helpers.ratelimit import chat_limit, limiter
from app.services.catalogue.authoring import AuthoringService
from ledger.authoring import AuthoringProposal
from ledger.errors import (
    ApprovalRequiredError,
    IdempotencyConflictError,
    RevisionConflictError,
)
from ledger.resources import ResourceExistsError, ResourceNotFoundError

router = APIRouter(prefix="/catalogue", tags=["catalogue"])


def _service(request: Request) -> AuthoringService:
    """Resolve authoring only when the ledger backend is configured.

    Args:
        request: Current HTTP service context.

    Returns:
        Application-owned authoring service.

    Raises:
        HTTPException: The configured backend lacks catalogue authoring.
    """
    service = request.app.state.container.authoring
    if service is None:
        raise HTTPException(503, "Catalogue authoring requires SHEETS_BACKEND=ledger")
    return service


async def _respond(pending: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """Translate checked authoring outcomes to stable HTTP errors.

    Args:
        pending: One owned service operation to await.

    Returns:
        Bounded evidence or a committed receipt.

    Raises:
        HTTPException: Scope, revision, approval or validation rejected the operation.
    """
    try:
        return await pending
    except ResourceNotFoundError:
        raise HTTPException(404, "Resource not found") from None
    except ApprovalRequiredError as exc:
        raise HTTPException(
            409, {"status": "awaiting_approval", "approval": exc.approval}
        ) from None
    except (
        RevisionConflictError,
        IdempotencyConflictError,
        ResourceExistsError,
    ) as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/sheets")
@limiter.limit(chat_limit)
async def list_sheets(
    request: Request,
    response: Response,
    workbook_id: str | None = None,
    offset: int = Query(0, ge=0, le=100000),
    user_id: int = Depends(get_current_user),
) -> dict[str, Any]:
    """List owned sheet identities on demand for table placement.

    Args:
        request: HTTP service context.
        response: Response carrying rate-limit headers.
        workbook_id: Optional owned workbook filter.
        offset: Number of sheet records already read.
        user_id: JWT-derived owner identity.

    Returns:
        Bounded sheet page and continuation offset.
    """
    return await _respond(
        _service(request).sheets(user_id, workbook_id=workbook_id, offset=offset)
    )


@router.get("/sheets/{sheet_id}/region")
@limiter.limit(chat_limit)
async def inspect_region(
    sheet_id: int,
    request: Request,
    response: Response,
    table_range: str = Query(min_length=1, max_length=64),
    user_id: int = Depends(get_current_user),
) -> dict[str, Any]:
    """Inspect bounded placement evidence under current ownership.

    Args:
        sheet_id: Stable source sheet identity.
        request: HTTP service context.
        response: Response carrying rate-limit headers.
        table_range: Finite A1 region to inspect.
        user_id: JWT-derived owner identity.

    Returns:
        Source revision, selected values and overlapping table identities.
    """
    return await _respond(_service(request).inspect(user_id, sheet_id, table_range))


@router.post("/proposals")
@limiter.limit(chat_limit)
async def prepare_table(
    body: AuthoringProposal,
    request: Request,
    response: Response,
    user_id: int = Depends(get_current_user),
) -> dict[str, Any]:
    """Persist an exact authoring proposal without changing the grid or catalogue.

    Args:
        body: Validated action and observed source revisions.
        request: HTTP service context.
        response: Response carrying rate-limit headers.
        user_id: JWT-derived owner identity.

    Returns:
        Fresh operation reference and optional human approval identity.
    """
    return await _respond(_service(request).prepare(user_id, body))


@router.post("/operations/{operation_ref}/execute")
@limiter.limit(chat_limit)
async def execute_table(
    operation_ref: str,
    request: Request,
    response: Response,
    user_id: int = Depends(get_current_user),
) -> dict[str, Any]:
    """Execute or replay an original reference under fresh ledger checks.

    Args:
        operation_ref: Original prepared operation identity.
        request: HTTP service context.
        response: Response carrying rate-limit headers.
        user_id: JWT-derived owner identity.

    Returns:
        Committed operation receipt.
    """
    return await _respond(_service(request).execute(user_id, operation_ref))
