"""Owner-scoped catalogue reads authenticated at the API boundary."""

from typing import Any, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Query, Response

from app.helpers.auth import get_current_user
from app.helpers.ratelimit import chat_limit, limiter
from app.services.catalogue.service import CatalogueService
from ledger.resources import ResourceInspection, ResourceNotFoundError, ResourceSearch

router = APIRouter(prefix="/resources", tags=["resources"])


def _service(request: Request) -> CatalogueService:
    """Resolve the catalogue service for the configured ledger backend.

    Args:
        request: Current authenticated HTTP request.

    Returns:
        The application-owned catalogue service.

    Raises:
        HTTPException: Catalogue reads are unavailable for this backend.
    """
    service = request.app.state.container.catalogue
    if service is None:
        raise HTTPException(status_code=503, detail="Resource discovery is unavailable")
    return service


@router.post("/search")
@limiter.limit(chat_limit)
async def search_resources(
    body: ResourceSearch,
    request: Request,
    response: Response,
    user_id: int = Depends(get_current_user),
) -> dict[str, Any]:
    """Discover registered tables across the authenticated user's workbooks.

    Args:
        body: Search intent and metadata filters; no ownership fields.
        request: HTTP request used for service resolution and rate limits.
        response: Response used to attach rate-limit headers.
        user_id: Identity from the verified bearer token.

    Returns:
        Bounded candidates with freshness and ranking evidence.

    Raises:
        HTTPException: The service is unavailable or the response exceeds its budget.
    """
    try:
        return await _service(request).search(user_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/{table_id}")
@limiter.limit(chat_limit)
async def inspect_resource(
    table_id: str,
    request: Request,
    response: Response,
    column_offset: Annotated[int, Query(ge=0)] = 0,
    column_limit: Annotated[int, Query(ge=1, le=64)] = 32,
    user_id: int = Depends(get_current_user),
) -> dict[str, Any]:
    """Inspect a table with an ownership check in the same database read.

    Args:
        table_id: Stable resource identity selected by the caller.
        request: HTTP request used for service resolution and rate limits.
        response: Response used to attach rate-limit headers.
        column_offset: Zero-based start of the schema page.
        column_limit: Maximum columns in this page.
        user_id: Identity from the verified bearer token.

    Returns:
        Bounded registered metadata and a page of column definitions.

    Raises:
        HTTPException: The table is absent/foreign, backend unavailable, or output too large.
    """
    try:
        return await _service(request).inspect(
            user_id,
            ResourceInspection(
                table_id=table_id,
                column_offset=column_offset,
                column_limit=column_limit,
            ),
        )
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Table not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
