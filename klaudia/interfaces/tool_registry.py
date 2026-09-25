import asyncio
import logging
from contextlib import suppress
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import (
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)
from langchain_core.tools import BaseTool, StructuredTool

logger = logging.getLogger(__name__)


_ANY_ITEM_SCHEMA: dict[str, Any] = {"type": "string"}


def _normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON schema that Gemini's function-declaration parser accepts.

    Gemini (via langchain-google-genai) requires every `type: array` to carry a
    non-empty `items` schema. FastMCP emits `items: {}` for `list[Any]`, and
    langchain-google-genai drops empty dicts (see `_dict_to_genai_schema`
    truthiness check). We replace missing/empty `items` with a permissive
    `{"type": "string"}` so tools like `tool_append_rows(data: list[list[Any]])`
    survive the Gemini round-trip.
    """
    if not isinstance(schema, dict):
        return schema

    out = dict(schema)
    if out.get("type") == "array":
        items = out.get("items")
        if not isinstance(items, dict) or not items:
            out["items"] = dict(_ANY_ITEM_SCHEMA)

    for key in ("items", "additionalProperties"):
        value = out.get(key)
        if isinstance(value, dict):
            out[key] = _normalize_schema(value)

    for key in ("properties", "definitions", "$defs"):
        nested = out.get(key)
        if isinstance(nested, dict):
            out[key] = {k: _normalize_schema(v) for k, v in nested.items()}

    for key in ("anyOf", "oneOf", "allOf"):
        values = out.get(key)
        if isinstance(values, list):
            out[key] = [_normalize_schema(v) for v in values]

    return out


class MCPToolRegistry:
    """Connect to an MCP server and expose its tools as LangChain tools.

    A URL uses Streamable HTTP unless it ends in ``/sse``, which keeps the old
    transport available during the FastMCP 4 rollout. Use ``from_stdio`` for a
    child process owned by Klaudia. The public ``tools`` property stays stable
    so the agent layer does not depend on an MCP client implementation.
    """

    def __init__(
        self,
        name: str,
        url: str | None = None,
        *,
        stdio_command: str | None = None,
        stdio_args: list[str] | None = None,
        stdio_cwd: str | None = None,
        stdio_env: dict[str, str] | None = None,
        auth_token: str | None = None,
    ) -> None:
        if not url and not stdio_command:
            raise ValueError(
                "MCPToolRegistry needs either url (HTTP) or stdio_command (stdio)"
            )
        self._name = name
        self._url = url
        self._stdio_command = stdio_command
        self._stdio_args = stdio_args or []
        self._stdio_cwd = stdio_cwd
        self._stdio_env = stdio_env
        self._auth_token = auth_token
        self._client: Client | None = None
        self._tools: list[BaseTool] = []
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._shutdown = asyncio.Event()
        self._connection_error: Exception | None = None

    @classmethod
    def from_stdio(
        cls,
        name: str,
        command: str,
        args: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> "MCPToolRegistry":
        """Build a registry that spawns the MCP server via stdio subprocess."""
        return cls(
            name,
            stdio_command=command,
            stdio_args=args,
            stdio_cwd=cwd,
            stdio_env=env,
        )

    async def connect(self) -> None:
        """Connect to MCP server and discover tools via a background task."""
        if self._task and not self._task.done():
            return
        self._ready.clear()
        self._shutdown.clear()
        self._tools.clear()
        self._connection_error = None
        self._task = asyncio.create_task(self._run())
        await self._ready.wait()
        if self._connection_error is not None:
            raise RuntimeError(
                f"MCP {self._name}: connection failed"
            ) from self._connection_error

    async def _run(self) -> None:
        """Own one FastMCP client connection until registry shutdown."""
        try:
            if self._url:
                if self._url.rstrip("/").endswith("/sse"):
                    transport = SSETransport(self._url, auth=self._auth_token)
                    transport_label = f"sse {self._url}"
                else:
                    transport = StreamableHttpTransport(
                        self._url,
                        auth=self._auth_token,
                    )
                    transport_label = f"http {self._url}"
            else:
                transport = StdioTransport(
                    command=self._stdio_command,
                    args=self._stdio_args,
                    cwd=self._stdio_cwd,
                    env=self._stdio_env,
                )
                transport_label = (
                    f"stdio {self._stdio_command} {' '.join(self._stdio_args)}"
                )

            client = Client(transport, mode="auto")
            async with client:
                self._client = client
                tool_infos = await client.list_tools()
                self._tools = [self._wrap_tool(tool_info) for tool_info in tool_infos]
                logger.info(
                    "MCP %s: connected via %s, protocol=%s, tools=%d",
                    self._name,
                    transport_label,
                    client.protocol_version,
                    len(self._tools),
                )
                self._ready.set()
                await self._shutdown.wait()

            logger.info("MCP %s: disconnected (%s)", self._name, transport_label)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._connection_error = exc
            logger.exception("MCP %s: connection failed", self._name)
        finally:
            self._client = None
            self._ready.set()

    def _wrap_tool(self, tool_info: Any) -> BaseTool:
        """Wrap an MCP tool as a LangChain StructuredTool with a real args schema."""
        tool_name = tool_info.name
        input_schema = tool_info.input_schema or {}
        # Pass the MCP JSON schema directly — langchain ArgsSchema accepts dict.
        # This preserves nested `items` that Pydantic drops for bare `list`.
        args_schema = _normalize_schema(input_schema)

        async def _call(**kwargs: Any) -> str:
            client = self._client
            if client is None:
                raise RuntimeError(f"MCP {self._name} is not connected")
            # Strip None values so MCP tools see only explicit args.
            clean = {k: v for k, v in kwargs.items() if v is not None}
            result = await client.call_tool(
                tool_name,
                clean,
                raise_on_error=False,
            )
            if result.content:
                # Concatenate all text content blocks (some tools emit one per row).
                return "\n".join(c.text for c in result.content if hasattr(c, "text"))
            return ""

        return StructuredTool.from_function(
            coroutine=_call,
            name=tool_name,
            description=tool_info.description or f"MCP tool: {tool_name}",
            args_schema=args_schema,
        )

    @property
    def tools(self) -> list[BaseTool]:
        return self._tools

    async def disconnect(self) -> None:
        """Close the MCP client and its child process or HTTP connection."""
        self._shutdown.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
        logger.info("MCP %s: shutdown complete", self._name)
