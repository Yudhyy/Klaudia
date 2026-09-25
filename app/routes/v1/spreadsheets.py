"""Per-user spreadsheet management (ledger backend only).

The Expo client lists/creates/renames/deletes spreadsheets here and passes
the chosen spreadsheet_id to /v1/chat. Foreign or absent ids 404 (non-
enumerating, same stance as sessions). Unavailable services return 503.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.helpers.auth import get_current_user
from app.helpers.ratelimit import chat_limit, limiter
from app.services.core.spreadsheets import SpreadsheetService
from ledger.store import SpreadsheetExistsError, SpreadsheetNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/spreadsheets", tags=["spreadsheets"])


class SpreadsheetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class SpreadsheetRename(BaseModel):
    name: str = Field(min_length=1, max_length=100)


def _service(request: Request) -> SpreadsheetService:
    service = request.app.state.container.spreadsheets
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Spreadsheet management is unavailable",
        )
    return service


@router.get("")
@limiter.limit(chat_limit)
async def list_spreadsheets(
    request: Request,
    user_id: int = Depends(get_current_user),
) -> list[dict]:
    return await _service(request).list_for_user(user_id)


@router.post("", status_code=201)
@limiter.limit(chat_limit)
async def create_spreadsheet(
    body: SpreadsheetCreate,
    request: Request,
    user_id: int = Depends(get_current_user),
) -> dict:
    try:
        return await _service(request).create(user_id, body.name)
    except SpreadsheetExistsError:
        raise HTTPException(status_code=409, detail="Spreadsheet name already exists")


@router.patch("/{spreadsheet_id}")
@limiter.limit(chat_limit)
async def rename_spreadsheet(
    spreadsheet_id: str,
    body: SpreadsheetRename,
    request: Request,
    user_id: int = Depends(get_current_user),
) -> dict:
    try:
        await _service(request).rename(user_id, spreadsheet_id, body.name)
    except SpreadsheetNotFoundError:
        raise HTTPException(status_code=404, detail="Spreadsheet not found")
    except SpreadsheetExistsError:
        raise HTTPException(status_code=409, detail="Spreadsheet name already exists")
    return {"spreadsheetId": spreadsheet_id, "name": body.name}


@router.delete("/{spreadsheet_id}", status_code=204)
@limiter.limit(chat_limit)
async def delete_spreadsheet(
    spreadsheet_id: str,
    request: Request,
    user_id: int = Depends(get_current_user),
) -> None:
    try:
        await _service(request).delete(user_id, spreadsheet_id)
    except SpreadsheetNotFoundError:
        raise HTTPException(status_code=404, detail="Spreadsheet not found")
