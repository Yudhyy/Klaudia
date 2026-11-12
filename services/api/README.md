# Klaudia service

This distribution supplies the HTTP, extraction-worker and MCP adapters for the
core packages. Each process creates and closes its own pools and clients.

From the repository root:

```bash
uv sync --all-packages --group dev
uv run python -m klaudia_api.adapters.persistence upgrade
uv run uvicorn klaudia_api.main:app --host 0.0.0.0 --port 8000
```

Start extraction with `scripts/run_worker.sh`. Start a separate MCP host with
`uv run python -m klaudia_api.interfaces.mcp.host --transport stdio`, or select
`http` with the required token-verification settings.

The `api`, `worker` and `mcp` install extras declare process dependencies. The
root workspace and shared image select all three. The `mcp-ledger` distribution
selects only the MCP extra and retains its old imports and script launcher.
Service implementations never import those compatibility modules.

Application migration assets live in `migrations/` and install inside the wheel.
The release command runs ledger before application. Runtime startup checks
revisions; it does not replace the release step. Ledger storage and its history
still install independently through `ledger-postgres`.

Supply settings through the environment, or launch from the repository root
with its local `.env`. MCP reads dotenv from the launch directory. Relative
Google credential paths resolve against that environment file directory, or
the launch directory when no file exists. Mock OCR reads optional `sample-data/`
files from the same directory. Docker includes those files; an installation
without them retains the existing mock fallback.

Retained `app.*` and `config.*` forwarding modules ship in this wheel. New callers
use `klaudia_api`. Core packages remain independent of the service.
