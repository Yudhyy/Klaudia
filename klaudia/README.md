# Klaudia agent

This distribution keeps the `klaudia.core.agent` and `klaudia.core.skills.registry`
imports as re-exports of `agent-core` and `agent-skills`. Both paths return the
same classes, errors and functions. Canonical runtime code lives in
`packages/agent-core/src/agent_core/agent/`. The registry and procedure files live
in `packages/agent-skills/src/agent_skills/`.

The application owns the MCP client at
`app.adapters.mcp_client.tool_registry.MCPToolRegistry`. The agent wheel does
not include that adapter or depend on MCP/FastMCP. Application callers of the
removed `klaudia.interfaces.MCPToolRegistry` and
`klaudia.interfaces.tool_registry` imports must use the application path.
Model construction now lives in `app.adapters.llm.model_factory.build_chat_llm`.
Callers of the removed `klaudia.core.agent.llm` path must use that application
path. The compatibility distribution cannot import application code. The runtime
still uses LangChain model and tool types; concrete provider libraries belong
to the root application manifest.

The wheel maps the existing flat source layout into the `klaudia` namespace.
It contains import shims; `agent-skills` owns the single copy of each procedure.
From the repo root, run `uv sync --all-packages --group dev`. Standalone builds
must install the `agent-core` and `agent-skills` wheels with this wheel. The
packaging suite checks installed import identity outside the checkout.
