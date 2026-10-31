"""Contract tests for Klaudia's FastMCP client boundary."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from pydantic import SecretStr
from langchain_core.callbacks import BaseCallbackHandler

import pytest

from app.adapters.mcp_client.tool_registry import MCPToolRegistry
from app.adapters.mcp_client import tool_registry


def _write_server(tmp_path):
    """Write a small FastMCP server used by registry subprocess tests.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the generated server module.
    """
    server_path = tmp_path / "server.py"
    server_path.write_text(
        """
from fastmcp import FastMCP

mcp = FastMCP("registry-test", strict_input_validation=True)

@mcp.tool
def greet(name: str) -> dict[str, str]:
    return {"message": f"hello {name}"}

if __name__ == "__main__":
    mcp.run(transport="stdio")
""".strip()
    )
    return server_path


async def test_stdio_registry_discovers_and_calls_fastmcp_tool(tmp_path):
    """The registry discovers schemas and preserves JSON tool output."""
    server_path = _write_server(tmp_path)
    registry = MCPToolRegistry.from_stdio(
        "test",
        command=sys.executable,
        args=[str(server_path)],
    )

    await registry.connect()
    try:
        assert [tool.name for tool in registry.tools] == ["greet"]
        assert "ctx" not in registry.tools[0].args
        response = await registry.tools[0].ainvoke({"name": "Klaudia"})
        assert json.loads(response) == {"message": "hello Klaudia"}
    finally:
        await registry.disconnect()


async def test_registry_connection_failure_is_not_silenced(tmp_path):
    """A dead subprocess fails application startup instead of hiding the error."""
    registry = MCPToolRegistry.from_stdio(
        "broken",
        command=sys.executable,
        args=[str(tmp_path / "missing.py")],
    )

    with pytest.raises(RuntimeError, match="connection failed"):
        await registry.connect()


@pytest.mark.parametrize(
    "path", ["/sse", "/ledger/sse/", "/sse?tenant=one", "/SSE#old", "/%73se"]
)
def test_registry_rejects_legacy_endpoints_before_connecting(path):
    with pytest.raises(ValueError, match="removed.*Streamable HTTP"):
        MCPToolRegistry("ledger", url=f"https://mcp.example{path}")


@pytest.mark.parametrize("failure", ["HTTP 404", "HTTP 401", "unsupported protocol"])
async def test_http_connection_failure_does_not_try_another_transport(
    monkeypatch, failure
):
    transport = object()
    build_transport = Mock(return_value=transport)
    client = AsyncMock()
    client.__aenter__.side_effect = RuntimeError(failure)
    build_client = Mock(return_value=client)
    monkeypatch.setattr(tool_registry, "StreamableHttpTransport", build_transport)
    monkeypatch.setattr(tool_registry, "Client", build_client)
    registry = MCPToolRegistry(
        "ledger", url="https://mcp.example/custom", auth_token="token"
    )

    with pytest.raises(RuntimeError, match="connection failed") as error:
        await registry.connect()

    assert str(error.value.__cause__) == failure
    build_transport.assert_called_once_with("https://mcp.example/custom", auth="token")
    build_client.assert_called_once_with(transport, mode="auto")


async def test_registry_forwards_identity_outside_tool_arguments():
    """Server-supplied identity travels in metadata, outside model arguments."""
    registry = MCPToolRegistry("ledger", url="https://mcp.example/mcp")
    registry._client = SimpleNamespace(
        call_tool=AsyncMock(return_value=SimpleNamespace(content=[], is_error=False))
    )
    tool = registry._wrap_tool(
        SimpleNamespace(
            name="read",
            description="Read",
            input_schema={
                "type": "object",
                "properties": {"spreadsheet_id": {"type": "string"}},
            },
        )
    )
    await tool.ainvoke(
        {"spreadsheet_id": "owned"},
        config={
            "configurable": {"__mcp_authorization": SecretStr("signed-user-token")}
        },
    )
    call = registry._client.call_tool.await_args
    assert call.kwargs.get("meta") == {"authorization": "signed-user-token"}


async def test_registry_does_not_return_mcp_failure_as_success():
    """Rejected MCP calls propagate as failures to application callers."""
    registry = MCPToolRegistry("ledger", url="https://mcp.example/mcp")
    registry._client = SimpleNamespace(
        call_tool=AsyncMock(return_value=SimpleNamespace(content=[], is_error=True))
    )
    tool = registry._wrap_tool(
        SimpleNamespace(
            name="read",
            description="Read",
            input_schema={"type": "object", "properties": {}},
        )
    )
    with pytest.raises(RuntimeError, match="MCP tool read failed"):
        await tool.ainvoke({})


async def test_forwarded_identity_is_not_tracing_metadata():
    """Callback metadata and tool arguments do not contain the raw credential."""
    observations = []

    class CaptureToolStart(BaseCallbackHandler):
        """Record callback input without accessing private call configuration."""

        def on_tool_start(self, serialized, input_str, **kwargs):
            """Capture public callback arguments for credential checks."""
            observations.append((serialized, input_str, kwargs))

    registry = MCPToolRegistry("ledger", url="https://mcp.example/mcp")
    registry._client = SimpleNamespace(
        call_tool=AsyncMock(return_value=SimpleNamespace(content=[], is_error=False))
    )
    tool = registry._wrap_tool(
        SimpleNamespace(
            name="read",
            description="Read",
            input_schema={"type": "object", "properties": {}},
        )
    )
    await tool.ainvoke(
        {},
        config={
            "callbacks": [CaptureToolStart()],
            "configurable": {"__mcp_authorization": SecretStr("private-fixture-token")},
        },
    )
    assert observations and "private-fixture-token" not in json.dumps(
        observations, default=str
    )
