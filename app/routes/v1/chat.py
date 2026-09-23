import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app.helpers.auth import get_current_user
from app.helpers.ratelimit import chat_limit, limiter
from app.models.chat import KlaudiaRequest, KlaudiaResponse
from ledger.store import SpreadsheetNotFoundError
from ledger.resources import ResourceNotFoundError
from ledger.errors import RevisionConflictError
from app.services.workflow.store import TaskBusyError, TaskConflictError

logger = logging.getLogger(__name__)

router = APIRouter()

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def _require_session_owner(
    request: Request, user_id: int, session_id: int | None
) -> None:
    """Reject a missing or foreign session without exposing ownership.

    Args:
        request: Current FastAPI request.
        user_id: Authenticated user ID.
        session_id: Optional session selected by the client.

    Raises:
        HTTPException: If the selected session is missing or foreign.
    """
    if session_id is None:
        return
    owner = await request.app.state.container.db_client.get_session_owner(session_id)
    if owner != user_id:
        raise HTTPException(status_code=404, detail="Session not found")


@router.post("/chat", response_model=KlaudiaResponse)
@limiter.limit(chat_limit)
async def chat(
    body: KlaudiaRequest,
    request: Request,
    response: Response,
    user_id: int = Depends(get_current_user),
) -> KlaudiaResponse:
    """Process a chat message through the Klaudia pipeline (non-streaming)."""
    await _require_session_owner(request, user_id, body.session_id)
    orchestrator = request.app.state.orchestrator
    try:
        return await orchestrator.process(
            messages=body.messages,
            session_id=body.session_id,
            user_id=user_id,
            user_name=body.user_name,
            spreadsheet_id=body.spreadsheet_id,
            **(
                {"request_key": body.request_key}
                if body.request_key is not None
                else {}
            ),
        )
    except (SpreadsheetNotFoundError, ResourceNotFoundError):
        raise HTTPException(status_code=404, detail="Spreadsheet not found")
    except (TaskBusyError, TaskConflictError, RevisionConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))


def _format_sse(event: dict) -> str:
    """Encode a dict event as an SSE frame."""
    name = event.get("type", "message")
    payload = json.dumps(event.get("data", {}), ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n"


@router.post("/chat/stream")
@limiter.limit(chat_limit)
async def chat_stream(
    body: KlaudiaRequest,
    request: Request,
    user_id: int = Depends(get_current_user),
) -> StreamingResponse:
    """Stream the Klaudia pipeline as Server-Sent Events."""
    await _require_session_owner(request, user_id, body.session_id)
    orchestrator = request.app.state.orchestrator

    # Pre-validate an explicit spreadsheet selection so absent/foreign ids
    # get a clean 404 before any SSE bytes are sent. Default resolution
    # (spreadsheet_id=None) happens inside the stream and cannot 404.
    spreadsheets = request.app.state.container.spreadsheets
    if body.spreadsheet_id and spreadsheets is not None:
        try:
            await spreadsheets.resolve_scope(user_id, body.spreadsheet_id)
        except SpreadsheetNotFoundError:
            raise HTTPException(status_code=404, detail="Spreadsheet not found")

    async def event_source() -> AsyncIterator[str]:
        try:
            async for event in orchestrator.stream(
                messages=body.messages,
                session_id=body.session_id,
                user_id=user_id,
                user_name=body.user_name,
                spreadsheet_id=body.spreadsheet_id,
                **(
                    {"request_key": body.request_key}
                    if body.request_key is not None
                    else {}
                ),
            ):
                if await request.is_disconnected():
                    logger.info("Client disconnected; stopping stream")
                    return
                yield _format_sse(event)
        except Exception:
            logger.exception("chat_stream failure")
            yield _format_sse(
                {"type": "error", "data": {"message": "Internal server error"}}
            )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
