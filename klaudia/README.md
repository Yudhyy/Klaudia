# Klaudia agent

One main agent loads versioned skills and calls bounded tools for owned resources.
The application supplies authenticated identity, durable tasks and checked ledger
operations. Provider model construction lives in `core/agent/llm.py`.

The application owns the MCP client at
`app.adapters.mcp_client.tool_registry.MCPToolRegistry`. The agent wheel does
not include that adapter or depend on MCP/FastMCP. Application callers of the
removed `klaudia.interfaces.MCPToolRegistry` and
`klaudia.interfaces.tool_registry` imports must use the application path.
The agent still depends on ledger-core, LangChain and model-provider libraries.

The wheel maps the existing flat source layout into the `klaudia` namespace and
includes runtime skill Markdown files. Editable installs use the checkout's
parent directory to resolve the same namespace. The packaging suite checks the
wheel paths so a source checkout cannot hide an incomplete installed package.
