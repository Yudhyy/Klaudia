"""Inspect and resume authenticated durable main-agent tasks."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query

from app.helpers.auth import get_current_user
from app.helpers.ratelimit import chat_limit, limiter
from app.models.chat import KlaudiaResponse
from app.services.workflow.store import TaskBusyError, TaskStore
from ledger.resources import ResourceNotFoundError

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _store(request: Request) -> TaskStore:
    """Require the configured durable runtime.

    Args:
        request: Current authenticated HTTP request.

    Returns:
        Configured task storage.

    Raises:
        HTTPException: Main task execution is unavailable.
    """
    tasks = request.app.state.container.tasks
    if tasks is None:
        raise HTTPException(status_code=503, detail="Durable tasks unavailable")
    return tasks


@router.get("/{task_id}")
@limiter.limit(chat_limit)
async def get_task(
    task_id: str,
    request: Request,
    response: Response,
    user_id: int = Depends(get_current_user),
) -> dict:
    """Return owned task progress without exposing internal model messages.

    Args:
        task_id: Original task identity.
        request: HTTP service context.
        response: Response carrying rate-limit headers.
        user_id: JWT-derived identity.

    Returns:
        Recorded status and currently owned operation receipts.
    """
    try:
        return await _store(request).view(user_id, task_id)
    except TaskBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Task not found")


@router.post("/{task_id}/resume", response_model=KlaudiaResponse)
@limiter.limit(chat_limit)
async def resume_task(
    task_id: str,
    request: Request,
    response: Response,
    user_id: int = Depends(get_current_user),
) -> KlaudiaResponse:
    """Resume the exact stored task without accepting new instructions.

    Args:
        task_id: Original task identity.
        request: HTTP service context.
        response: Response carrying rate-limit headers.
        user_id: JWT-derived identity.

    Returns:
        Checked response with task status and committed operation evidence.
    """
    _store(request)
    try:
        return await request.app.state.orchestrator.resume_task(user_id, task_id)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Task not found")
    except TaskBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("")
@limiter.limit(chat_limit)
async def list_tasks(
    request: Request,
    response: Response,
    session_id: int = Query(gt=0),
    offset: int = Query(default=0, ge=0, le=100000),
    user_id: int = Depends(get_current_user),
) -> dict:
    """Find owned tasks when a previous response did not reach the client.

    Args:
        request: HTTP service context.
        response: Response carrying rate-limit headers.
        session_id: Owned conversation identity.
        offset: Number of tasks already inspected.
        user_id: JWT-derived identity.

    Returns:
        Bounded task summaries with original task IDs.
    """
    try:
        return await _store(request).list_session(user_id, session_id, offset=offset)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except TaskBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
