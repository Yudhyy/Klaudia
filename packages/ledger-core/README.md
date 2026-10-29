# Ledger core

`ledger-core` contains pure request/value contracts, resource and error types,
bounded evidence, grid aggregation and native exact-decimal formulas. Its only
runtime dependency is Pydantic. Importing it opens no connections and requires
no PostgreSQL or MCP installation.

Use the canonical `ledger_core` namespace:

```python
from ledger_core.append_contracts import TableAppendProposal
from ledger_core.formula_engine import Formula, evaluate_graph
```

The existing `ledger.*` modules in `mcp-ledger` re-export the same definitions,
including the original execution-module proposal imports. New consumers should
import the core directly. Persistence, checked operation execution, approval
storage and the owned-read MCP host remain in `mcp-ledger`.

From the repository root, install the workspace and run the compatibility and
standalone wheel checks:

```bash
uv sync --all-packages --group dev
uv run pytest tests/unit/test_ledger_core_compatibility.py tests/unit/test_ledger_proposal_contracts.py tests/unit/test_architecture_imports.py -q
uv run pytest tests/packaging -q
```

The packaging checks build a wheel, install only that wheel and its declared
dependencies into a fresh environment outside the checkout, and exercise imports,
contract fixtures, decimal aggregation and formula dependencies using isolated
Python. The build reuses uv's offline cache populated by workspace installation;
dependency installation uses the workspace Pydantic version and may download a
missing cached wheel. No other workspace package is installed in that environment.
The suite also checks the agent wheel's namespace and bundled runtime skills,
which are required by installed API, worker and MCP entrypoint verification.

To build the wheel independently:

```bash
uv build packages/ledger-core --wheel --no-sources
```

This is the first ledger-core slice. Agent-owned capability adapters, durable
workflow extraction and the larger package layout remain later work under
[issue #3](https://github.com/Yudhyy/Klaudia/issues/3).
