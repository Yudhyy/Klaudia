import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from klaudia_api.interfaces.http.dependencies import get_current_user
from klaudia_api.adapters.capabilities.conversation import ConversationStore

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/sessions")
async def list_sessions(req: Request, user_id: int = Depends(get_current_user)):
    db: ConversationStore = req.app.state.container.conversations
    sessions = await db.get_sessions(user_id)
    return {"sessions": sessions}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: int,
    req: Request,
    user_id: int = Depends(get_current_user),
):
    db: ConversationStore = req.app.state.container.conversations
    owner = await db.get_session_owner(session_id)
    # 404 for both missing and foreign sessions: don't reveal which ids exist.
    if owner is None or owner != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = await db.get_session_messages(session_id)
    return {"session_id": session_id, "messages": messages}
