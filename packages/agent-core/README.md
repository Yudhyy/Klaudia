# Agent core

The agent runs bounded model steps with supplied capabilities. It owns tool
schemas, run context and continuation payloads. Import `MainAgent` from
`agent_core.agent.agent` and `TaskContext` from `agent_core.agent.context`.

The runtime uses LangChain model and tool types, Pydantic contracts and
`agent-skills`. The application constructs provider models and supplies ledger,
document, memory and workflow capabilities. Core imports do not open connections
or start processes.

From the repo root, run `uv sync --all-packages --group dev`. The packaging
suite builds and installs the runtime with its declared dependencies outside
the checkout, then exercises scripted tools and packaged skills with provider
and application packages absent.
