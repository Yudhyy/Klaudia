"""Limit MCP reads to the authenticated owner's current workbooks."""

import os

from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_access_token, get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext
from fastmcp.tools import Tool, ToolResult
from mcp.types import CallToolRequestParams, ListToolsRequest
from pydantic import TypeAdapter, ValidationError

from ledger.store import DEFAULT_WORKSPACE

READ_TOOLS = frozenset(
    {
        "tool_get_sheet_data",
        "tool_get_sheet_formulas",
        "tool_list_sheets",
        "tool_get_spreadsheet_info",
        "tool_get_multiple_sheet_data",
        "tool_get_sheet_snapshot",
        "tool_aggregate_sheet",
        "tool_search_resources",
        "tool_inspect_resource",
    }
)


def _token_owner(token: AccessToken | None) -> int:
    """Read a positive owner ID from an expiring, verified token.

    Args:
        token: Token verified by the configured JWT verifier.

    Returns:
        Authenticated application user ID.

    Raises:
        ToolError: The token has no usable owner or expiry.
    """
    if token is not None and token.expires_at is not None:
        subject = token.subject
        if subject and subject.isascii() and subject.isdecimal() and int(subject) > 0:
            return int(subject)
    raise ToolError("Owner identity required")


async def _owner(ctx: Context) -> int:
    """Resolve signed request identity or an explicit local process owner.

    Args:
        ctx: Current MCP context with original wire metadata.

    Returns:
        Verified token owner or configured stdio owner.

    Raises:
        ToolError: Identity is missing, invalid, or untrusted.
    """
    request = ctx.request_context
    metadata = request.meta if request is not None else None
    signed_token = metadata.get("authorization") if metadata else None
    if signed_token is not None:
        secret = os.environ.get("JWT_SECRET", "")
        if not isinstance(signed_token, str) or len(secret) < 32:
            raise ToolError("Owner identity required")
        verifier = JWTVerifier(public_key=secret, algorithm="HS256")
        return _token_owner(await verifier.verify_token(signed_token))
    token = get_access_token()
    if token is not None:
        return _token_owner(token)
    try:
        get_http_request()
    except RuntimeError:
        configured = os.environ.get("MCP_USER_ID", "")
        if configured and configured.isascii() and configured.isdecimal():
            return int(configured)
    raise ToolError("Owner identity required")


def _workspaces(request: CallToolRequestParams) -> list[str]:
    """Collect every workbook selected by a single or batch read.

    Args:
        request: Unvalidated MCP tool request.

    Returns:
        Distinct workbook IDs in stable lock order.

    Raises:
        ToolError: Workbook selections have invalid types.
    """
    arguments = request.arguments or {}
    try:
        if request.name == "tool_get_multiple_sheet_data":
            queries = TypeAdapter(list[dict[str, str]]).validate_python(
                arguments.get("queries", []), strict=True
            )
            return sorted(
                {query.get("spreadsheet_id") or DEFAULT_WORKSPACE for query in queries}
            )
        workspace = TypeAdapter(str | None).validate_python(
            arguments.get("spreadsheet_id"), strict=True
        )
        return [workspace or DEFAULT_WORKSPACE]
    except ValidationError as exc:
        raise ToolError("Invalid workbook selection") from exc


class LedgerAuthorization(Middleware):
    """Filter tool discovery and hold ownership through each allowed read."""

    async def on_list_tools(
        self, context: MiddlewareContext[ListToolsRequest], call_next: CallNext
    ) -> list[Tool]:
        """Advertise only the explicit read surface.

        Args:
            context: Tool-list request.
            call_next: Next middleware or handler.

        Returns:
            Supported read tools with their existing schemas.
        """
        return [tool for tool in await call_next(context) if tool.name in READ_TOOLS]

    async def on_call_tool(
        self, context: MiddlewareContext[CallToolRequestParams], call_next: CallNext
    ) -> ToolResult:
        """Reject mutations and lock the owned workbook before executing a read.

        Args:
            context: Tool request with server-injected lifespan context.
            call_next: Next middleware or tool handler.

        Returns:
            Read output under current workbook ownership.

        Raises:
            ToolError: The tool, caller, or workbook is not authorized.
        """
        if context.message.name not in READ_TOOLS:
            raise ToolError("MCP tool is not available")
        ctx = context.fastmcp_context
        if ctx is None:
            raise ToolError("Owner identity required")
        user_id = await _owner(ctx)
        workspaces = _workspaces(context.message)
        store = ctx.lifespan_context
        async with store.pool.acquire() as connection, connection.transaction():
            for workspace in workspaces:
                owned = await connection.fetchval(
                    "SELECT spreadsheet_id FROM ledger_spreadsheet "
                    "WHERE spreadsheet_id = $1 AND user_id = $2 FOR SHARE",
                    workspace,
                    user_id,
                )
                if owned is None:
                    raise ToolError("Workbook not found")
            with store.bind_connection(connection):
                return await call_next(context)
