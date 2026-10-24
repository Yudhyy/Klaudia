"""Model-free FastMCP contract checks for the ledger server."""

import datetime

import jwt
import pytest

from ledger.server import _build_auth, mcp


EXPECTED_TOOLS = {
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


async def test_ledger_tool_surface_and_context_injection():
    """The supported read surface hides injected context and disabled writes."""
    tools = await mcp.list_tools()

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert all("ctx" not in tool.parameters.get("properties", {}) for tool in tools)


async def test_ledger_tools_publish_safety_annotations():
    """Every advertised operation identifies itself as read-only."""
    tools = {tool.name: tool for tool in await mcp.list_tools()}

    assert tools["tool_get_sheet_data"].annotations.read_only_hint is True
    assert tools["tool_get_sheet_data"].annotations.open_world_hint is False
    assert tools["tool_get_sheet_snapshot"].annotations.read_only_hint is True
    assert all(tool.annotations.read_only_hint is True for tool in tools.values())


def test_http_auth_rejects_short_shared_secret(monkeypatch):
    """Weak HMAC keys fail when the server configuration loads."""
    monkeypatch.setenv("MCP_JWT_SECRET", "too-short")

    with pytest.raises(RuntimeError, match="at least 32"):
        _build_auth()


async def test_http_auth_validates_hs256_bearer_token(monkeypatch):
    """The HTTP verifier accepts valid tokens and rejects bad signatures."""
    secret = "unit-test-mcp-secret-with-at-least-32-bytes"
    monkeypatch.setenv("MCP_JWT_SECRET", secret)
    verifier = _build_auth()
    now = datetime.datetime.now(datetime.timezone.utc)
    token = jwt.encode(
        {
            "sub": "klaudia-service",
            "iat": now,
            "exp": now + datetime.timedelta(minutes=5),
        },
        secret,
        algorithm="HS256",
    )

    assert await verifier.verify_token(token) is not None
    assert await verifier.verify_token(f"{token}broken") is None
