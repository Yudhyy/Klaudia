import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.helpers.auth import get_current_user
from klaudia.interfaces.tool_registry import MCPToolRegistry
from ledger.store import SpreadsheetNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sheets", tags=["sheets"])


async def _resolve_scope(
    request: Request, user_id: int, spreadsheet_id: Optional[str]
) -> Optional[str]:
    """Resolve the authenticated user's spreadsheet scope."""
    service = request.app.state.container.spreadsheets
    if service is None:
        return None
    try:
        return await service.resolve_scope(user_id, spreadsheet_id)
    except SpreadsheetNotFoundError:
        raise HTTPException(status_code=404, detail="Spreadsheet not found")


def _get_tool(registry: MCPToolRegistry, name: str):
    tool = next((t for t in registry.tools if t.name == name), None)
    if tool is None:
        raise HTTPException(status_code=503, detail=f"MCP tool '{name}' unavailable")
    return tool


async def _invoke(tool, args: dict[str, Any]) -> Any:
    """Call a LangChain StructuredTool and parse its JSON string response."""
    # Strip None values — MCP tools rely on their own defaults for optional params.
    clean_args = {k: v for k, v in args.items() if v is not None}
    raw: str = await tool.ainvoke(clean_args)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Tool returned a plain string (unlikely for read ops, but be safe).
        return {"raw": raw}


@router.get("/info")
async def get_spreadsheet_info(
    request: Request,
    spreadsheet_id: Optional[str] = Query(None, description="Spreadsheet to read"),
    user_id: int = Depends(get_current_user),
) -> JSONResponse:
    """
    Return spreadsheet title and all sheet tab names for the user's
    spreadsheet (default one when spreadsheet_id is omitted).
    """
    registry: MCPToolRegistry = request.app.state.container.mcp_ledger
    tool = _get_tool(registry, "tool_get_spreadsheet_info")
    scope = await _resolve_scope(request, user_id, spreadsheet_id)
    try:
        data = await _invoke(tool, {"spreadsheet_id": scope})
        return JSONResponse(content=data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"sheets/info failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/data")
async def get_sheet_data(
    request: Request,
    sheet: str = Query(..., description="Sheet tab name, e.g. 'Sheet1'"),
    range: Optional[str] = Query(None, description="A1 notation range, e.g. 'A1:F50'"),
    spreadsheet_id: Optional[str] = Query(None, description="Spreadsheet to read"),
    user_id: int = Depends(get_current_user),
) -> JSONResponse:
    """
    Return cell values from a sheet tab in the user's spreadsheet
    (default one when spreadsheet_id is omitted).
    """
    registry: MCPToolRegistry = request.app.state.container.mcp_ledger
    tool = _get_tool(registry, "tool_get_sheet_data")
    scope = await _resolve_scope(request, user_id, spreadsheet_id)
    try:
        data = await _invoke(
            tool, {"sheet": sheet, "range": range, "spreadsheet_id": scope}
        )
        return JSONResponse(content=data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"sheets/data failed (sheet={sheet!r}): {e}")
        raise HTTPException(status_code=500, detail=str(e))
