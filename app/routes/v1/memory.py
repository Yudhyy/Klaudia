"""Explicit owner-authenticated editing of bounded context documents."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.helpers.auth import get_current_user
from app.helpers.ratelimit import chat_limit, limiter
from app.services.memory.contracts import (
    MAX_EXPECTED_REVISION,
    DocumentEdit,
    DocumentPath,
    MemoryDocument,
)
from app.services.memory.store import DocumentConflict, MemoryDocumentStore

router = APIRouter(prefix="/memory", tags=["memory"])


def document_path(path: str) -> DocumentPath:
    """Convert a route segment to an allowlisted document path.

    Args:
        path: File name from the URL.

    Returns:
        The supported absolute document path.

    Raises:
        HTTPException: The requested document is unsupported.
    """
    try:
        return DocumentPath(f"/{path}")
    except ValueError:
        raise HTTPException(
            status_code=422, detail="Unsupported memory document path"
        ) from None


def document_store(request: Request) -> MemoryDocumentStore:
    """Resolve the store without silently replacing unavailable context.

    Args:
        request: Current application request.

    Returns:
        The application-owned document store.

    Raises:
        HTTPException: Context storage is unavailable.
    """
    store = request.app.state.container.memory_documents
    if store is None:
        raise HTTPException(status_code=503, detail="Context storage is unavailable")
    return store


@router.get("/{path}")
@limiter.limit(chat_limit)
async def read_document(
    request: Request,
    response: Response,
    user_id: int = Depends(get_current_user),
    path: DocumentPath = Depends(document_path),
    store: MemoryDocumentStore = Depends(document_store),
) -> MemoryDocument:
    """Read a current document or its explicit missing/deleted status.

    Args:
        request: Request used for rate limits.
        response: Response used for rate-limit headers.
        user_id: Verified token identity.
        path: Allowlisted context path.
        store: Application-owned context store.

    Returns:
        The owner's document and revision.
    """
    return await store.read(user_id, path)


@router.put("/{path}")
@limiter.limit(chat_limit)
async def write_document(
    body: DocumentEdit,
    request: Request,
    response: Response,
    user_id: int = Depends(get_current_user),
    path: DocumentPath = Depends(document_path),
    store: MemoryDocumentStore = Depends(document_store),
) -> MemoryDocument:
    """Apply an explicit human edit at its observed revision.

    Args:
        body: Bounded text and expected revision, without authority fields.
        request: Request used for rate limits.
        response: Response used for rate-limit headers.
        user_id: Verified token identity.
        path: Allowlisted context path.
        store: Application-owned context store.

    Returns:
        The committed document and new revision.

    Raises:
        HTTPException: The expected revision is stale.
    """
    if body.policy is not None and path != DocumentPath.ACCOUNTING_POLICY:
        raise HTTPException(
            status_code=422,
            detail="Structured policy belongs only to /accounting-policy.md",
        )
    try:
        return await store.write(user_id, path, body)
    except DocumentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.delete("/{path}")
@limiter.limit(chat_limit)
async def delete_document(
    request: Request,
    response: Response,
    expected_revision: Annotated[int, Query(ge=1, le=MAX_EXPECTED_REVISION)],
    user_id: int = Depends(get_current_user),
    path: DocumentPath = Depends(document_path),
    store: MemoryDocumentStore = Depends(document_store),
) -> MemoryDocument:
    """Hide the current content while keeping its revision history.

    Args:
        request: Request used for rate limits.
        response: Response used for rate-limit headers.
        expected_revision: Revision read by the editor.
        user_id: Verified token identity.
        path: Allowlisted context path.
        store: Application-owned context store.

    Returns:
        The committed tombstone.

    Raises:
        HTTPException: The document is absent or its revision is stale.
    """
    try:
        return await store.delete(user_id, path, expected_revision)
    except DocumentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
