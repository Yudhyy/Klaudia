<h1 align="center">
  <img src="https://readme-typing-svg.demolab.com?font=Chakra+Petch&weight=600&size=55&duration=1&pause=1000&color=000000&background=CCFF00&center=true&vCenter=true&repeat=false&width=1200&lines=Klaudia%3A+The+Agentic+Accountant" alt="Klaudia: The Agentic Accountant - Self-Hosted AI Bookkeeping Agent" />
</h1>

<div align="center">
  <img src="https://github.com/Laoode/agentic-data-entry/blob/development/docs/Klaudia-Cozy-Workspace.png" alt="Klaudia Workspace">
</div>

<p align="center">
  <b>Zero Error is the Baseline. Absolute Balance is the Goal.</b> <br>
  <i>An accountant that works in your ledger, on your hardware.</i>
</p>

<p align="center">
  <img src="https://readme-typing-svg.demolab.com?font=Chakra+Petch&pause=1800&color=CCFF00&center=true&vCenter=true&width=1000&lines=Agentic+Accounting+for+Enterprise+Finance+Teams;Multi-Tenant+Ledgers+with+Tool-Layer+Isolation;Deterministic+Number+Checking+on+Every+Answer;Human+Approval+Before+Anything+Irreversible;Self-Hosted%3A+Financial+Data+Never+Leaves+the+Box" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-Backend-07988a" />
  <img src="https://img.shields.io/badge/LangGraph-Agentic%20Workflow-white" />
  <img src="https://img.shields.io/badge/LangChain-Orchestration-7fc8ff" />
  <img src="https://img.shields.io/badge/PostgreSQL-Ledger%20%2B%20pgvector-336791" />
  <img src="https://img.shields.io/badge/NocoDB-Grid%20View-3c4be7" />
  <img src="https://img.shields.io/badge/MCP-Tool%20Boundary-black" />
  <img src="https://img.shields.io/badge/mem0-Long%20Term%20Memory-ff6f00" />
  <img src="https://img.shields.io/badge/Deepseekv4-pro-4e6bfe" />
  <img src="https://img.shields.io/badge/Qwen3.5-4B%20Fine%20Tuned-623ae7" />
  <img src="https://img.shields.io/badge/vLLM-Model%20Serving-1f4b99" />
  <img src="https://img.shields.io/badge/Redis-Queue-red" />
  <img src="https://img.shields.io/badge/Taskiq-Workers-yellow" />
  <img src="https://img.shields.io/badge/MinIO-Object%20Storage-darkred" />
  <img src="https://img.shields.io/badge/Langfuse-Observability-e11411" />
  <img src="https://img.shields.io/badge/CircleCI-Pipeline-343434" />
  <img src="https://img.shields.io/badge/React%20Native-Mobile%20App-cyan" />
  <img src="https://img.shields.io/badge/Docker-Self%20Hosted-1c63ed" />
</p>

---

## 🏵 What this is

Klaudia is an **agentic accountant** for finance teams. You talk to it the way you
would talk to a junior on your team, and it works directly in your ledgers:
reads them, reconciles them, posts entries, closes the month, and tells you what
it found.

Storage runs on your own infrastructure. Ledger data, memory, and documents stay
in your Postgres and object store. Hosted model options receive only the prompt
and image or text payload sent to them.

**What it does today**

| | |
|---|---|
| **Read and analyse** | totals, roll-ups by category or period, cross-sheet aggregation, filtered sums, ranking |
| **Financial models** | profit and loss across statement sheets, gross and net margin, budget against actual by department |
| **Tax** | PPN 11% and PPh 23 withholding, computed from the tax base when no tax column exists |
| **Receivables** | aging schedules bucketed by days past due, overdue balances per customer |
| **Reconciliation** | detail against summary, finding the entry that breaks a double-entry journal |
| **Data entry** | append, correct, restructure tabs, multi-step month close across many turns |
| **Document capture** | receipts and invoices to structured rows, including multi-page PDFs |

Document capture is **one input path, not the product**. Most of the work happens
after the data is in the ledger.

---

## 🔐 Why it is safe to point at real books

Four properties, each enforced in code rather than by asking the model nicely.

**Numbers are checked, not trusted.** Every monetary figure in a reply is
compared against a grounded set computed from that turn's actual tool results:
cell values, column and row sums, group-by sums, cross-grid totals. An amount the
evidence cannot produce is flagged. `NUMERIC_VERIFY_MODE` selects whether that
logs or blocks.

**Irreversible operations stop for a human.** Impact is measured in code before
anything runs. Deleting more than a handful of rows, clearing a whole column, or
dropping a non-empty sheet parks the operation for approval and returns
approve/reject to the client. Approval replays the stored call verbatim, so the
model never re-plans a destructive action. This exists because prompting alone
was not enough: an early evaluation run wiped seven sheets.

**Tenants are separated at the tool layer.** Spreadsheet parameters are stripped
from every tool schema the model can see, and forced from a request-scoped value
the model cannot reach. An agent cannot name another tenant's workspace even
under prompt injection, because the parameter is not in its vocabulary.

**Data locality is configurable.** Long-term memory, embeddings, and storage run
locally. Self-hosted inference keeps model input local; hosted DeepSeek or Gemini
backends receive the requests routed to them.

---

## 🪆 Architecture

<div align="center">
  <img src="https://github.com/Laoode/agentic-data-entry/blob/development/docs/LLMOps.png" alt="LLM Ops Pipeline">
</div>

```text
Mobile / API client ── JWT ──> FastAPI /v1 (per-user rate limits)
                                   │
                                   ▼
                            Orchestrator ──> Langfuse traces
                                   │
   ┌───────────────────────────────┼───────────────────────────────┐
   │                               │                               │
Guardrails IN              Extraction Agent                Long-term memory
prompt-injection           dedup by content hash           continuity state +
+ scope checks             KIE model, sync or queued       semantic recall
   │                               │                               │
   └───────────────────────────────┼───────────────────────────────┘
                                   ▼
                          Supervisor (LangGraph)
                                   │
                 ┌─────────────────┴─────────────────┐
                 ▼                                   ▼
            SQL Agent                          Data Entry Team
        document archive                  read / structure / write
                 │                                   │
                 ▼                                   ▼
           MCP archive                          MCP ledger
                                   │
                                   ▼
                    Numeric verifier + destructive-op guard
                                   ▼
                             Guardrails OUT
```

Storage is one Postgres (ledger grids, document registry, tenancy, and the
memory vectors), Redis for dedup and queues, MinIO for document blobs. Tools are
reached over MCP, so the tool boundary stays explicit and auditable.

The ledger now also exposes bounded snapshot reads, revision-checked appends
with stored operation receipts, and deterministic labelled sums/counts over
selected table regions. These are backend capabilities; the current chat workers
retain their existing tools until the agent migration. They do not yet add formula
evaluation or change the published live-agent benchmark scores.

**Tenancy model:** user → spreadsheets → sheets. Chat and its existing MCP tools
remain bound to one spreadsheet per request. Authenticated catalogue reads can
search all workbooks currently owned by the user:

- `POST /v1/resources/search`: search registered table metadata with intent and
  optional concepts, required columns, entity and date filters.
- `GET /v1/resources/{table_id}`: inspect metadata and paginate its columns with
  `column_offset` and `column_limit` (32 by default, at most 64).

These endpoints derive identity from the bearer JWT and check ownership within
each database read. Foreign tables do not affect candidates or continuation
counts; foreign and missing IDs return the same 404. Reads have a 65,536-byte
output budget and require the ledger backend. Discovery covers registered tables
only; stale metadata remains marked. Shared-workspace roles, agent-selected
workbooks and multi-workbook writes are not enabled by this change.

The alternative agent components in `klaudia/core/agent/` expose task-bound
`search_resources`, `inspect_resource`, `release_resource` and `calculate` tools. The server
supplies immutable user identity and an optional active-workbook hint. Inspection
records up to 20 resource references with observed revisions; search alone does
not select a target. Each inspection rechecks ownership, and failed reinspection
discards the previous reference. These observations grant no write permission.
The working set can be restored from a bounded durable checkpoint in main chat.
The legacy chat runtime is still the default.

Table authoring operates on existing owned sheets, through main chat or these
JWT-authenticated ledger endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /v1/catalogue/sheets` | Page through owned sheet IDs; optional `workbook_id` and `offset` filters |
| `GET /v1/catalogue/sheets/{sheet_id}/region?table_range=H1:I3` | Inspect up to 4096 cells and overlapping table identities |
| `POST /v1/catalogue/proposals` | Prepare an exact lifecycle action without changing cells or metadata |
| `POST /v1/catalogue/operations/{operation_ref}/execute` | Execute or replay the original checked reference |

Actions are `create_table`, `register_table`, `update_table`, `refresh_table` and
`unregister_table`. Create writes supplied headers into a wholly blank region;
register uses existing cells. Update replaces names, aliases, business metadata
and same-sheet bounds without moving cells or editing stored headers. For changed
headers, `column_ids` maps each current column to its old ID or null for a new ID.
Refresh rebuilds counts and search metadata while preserving unchanged schemas.
Unregister always requires human approval and preserves cells. Multiple disjoint
tables on one sheet retain separate identities.

Create/register/update take a `definition` using the existing registration fields,
including `sheet_id`, `expected_sheet_revision`, `table_range` and `name`. Create
also requires `headers`. Update adds `table_id` and `expected_catalogue_revision`.
Refresh/unregister use `table_id` and both expected revisions without a definition.
The optional authoring tools load the `table-authoring` procedure and placement
evidence on demand. The model never supplies the authenticated owner.

Each authoring preparation creates a fresh reference. Save it for execution retries;
preparing again creates another proposal. A later intentional re-registration gets
a new table ID. Current ownership, revisions, overlap and header checks guard each
commit. `MAIN_CHAT_REQUIRE_APPROVAL=true` gates other authoring actions as well as
appends; unregister requires approval under either setting. Use the existing
approval endpoints, then resume the durable task when applicable. Receipts state
metadata and cell changes separately and do not claim formula recalculation.

Region inspection returns fractional JSON numbers as exact decimal strings with
an explicit representation label. Financial arithmetic still belongs to the
checked calculation tools. Automatic region inference, formula relationships and
physical grid schema edits remain separate work.


`MainAgent` adds a programmatic alternative loop, read-only by default. It accepts a
tool-capable chat model from the existing provider factory and an ownership-checked
`CatalogueService`. Each `run(message, TaskContext(...))` creates fresh tools and
state. The stable system prefix lists skill summaries; `load_skill` retrieves
allowlisted, versioned discovery, schema-inspection, calculation and append procedures on demand.
Caller-supplied `RunnableConfig` callbacks flow to model and tool calls.

The append procedure now instructs the agent to preserve complete supplied text
values, check proposed fields against the request, retain numeric types and
disclose any mismatch found after commit. This is procedural guidance, not a
backend check of natural-language intent. A small live rerun passed three of three
exact append trials with v2 loaded, up from two of three in the first sample.
Broader fidelity and full-service validation remain pending; see the
[comparison report](tests/e2e/outputs/comparison-2026-09-12-append-v2.md).

Default limits are 12 model steps, 24 tool calls, 131,072 bytes of serialized
messages and 120 seconds per run. The byte limit excludes tool schemas and
provider framing and is not a token estimate. Results distinguish answers,
exhausted budgets and invalid model output. Unexpected infrastructure failures
propagate. `answered` records a model response, not proof of financial completion.
The agent's `calculate` tool sums or counts an inspected registered table. The
model supplies its ID, metrics and filters; the task reference supplies observed
revisions. PostgreSQL resolves ownership, bounds, grid and revisions in one read.
Changed revisions or stale catalogue metadata fail explicitly. Results retain
metric column IDs, source revisions, units and the applied query. Stored fractions
remain Decimal operands through calculation; sums reject results beyond the
64-digit exact precision budget. Fractional group labels that cannot round-trip
through the current JSON numeric format also fail instead of rounding.

The legacy runtime remains the default. Set `CHAT_RUNTIME=main` to use the main
agent through the existing chat endpoints. Returning transaction rows and formula
evaluation remain separate work.
The backend still reads a whole JSONB sheet before selecting the registered region.
This adds a checked calculation path, not a row-level SQL query engine.

The backend also provides `LedgerStore.append_table_owned` for named records.
Ownership stays locked through the cell, catalogue and receipt transaction.
It checks observed revisions, consumes blank table rows and rejects collisions
or formula-bearing tables. Exact retries return the committed receipt after
rechecking ownership.

Explicitly supplying `operations=OperationService(ledger_store)` to `MainAgent`
enables `prepare_table_append` and `execute_operation`. Preparation takes a table
ID and complete named records; inspected references supply revisions. It stores
the exact request as text in the existing ledger operation row before execution.
Identical records at identical revisions share a server-generated reference.
Execution accepts only that reference, rechecks current ownership and commits
through the existing append transaction. Preparation changes no cells and is not
human approval. Formula-bearing tables remain unsupported for appends.

Retries use the original reference and return the original committed receipt.
`RunOutcome` includes observed operation references and deduplicated receipts;
committed writes invalidate task references on the changed sheet. A failed run
with operation references raises `AgentExecutionError` with its recovery outcome
and original cause. External cancellation carries the same evidence through
`AgentRunCancelled`, a cancellation exception. A runtime deadline returns a
timeout outcome, including observed references. Callers must retain these references
and retry them rather than start a new append when the outcome is unknown.
Main chat persists checkpoints and exposes explicit task resume; it does not retry automatically.
Standalone checked-append preparation over HTTP/MCP and default runtime cutover remain pending.

With `CHAT_RUNTIME=main` and `SHEETS_BACKEND=ledger`, both `POST /v1/chat` and
`POST /v1/chat/stream` use the main agent. JWT identity controls access across
currently owned workbooks; `spreadsheet_id` supplies an ownership-checked active
hint. The main path does not load the legacy prompt or fetch a sheet inventory.
It retains input/output guardrails, extraction handoff, session history and
Langfuse callbacks. Streaming buffers the reply until output checks finish.

Main chat exposes checked append, table authoring, discovery and calculation, plus bounded
`search_documents` and `read_document_page` tools over the existing archive.
Document reads check both file and session ownership. Search returns at most 20
records; page reads return at most 8,192 characters with continuation offsets.
Memory, extraction context and recent history have separate 8 KiB context budgets;
omissions are explicit and archived pages remain retrievable. The full agent
message budget still applies, including to an oversized current request.

Responses and streaming `done` events add `runtime`, `run_status`,
`operation_references` and `operation_receipts`. Before execution starts, the
service saves the original operation reference as session recovery evidence.
It also saves observed receipts and retains them in failed outcomes when receipt
journaling fails. `GET /v1/sessions/{session_id}` exposes saved evidence under the
existing session-owner check. Recovery reuses the original reference; chat does
not automatically retry or resume a task. Main chat also returns `task_id` and
persists the original input, model messages and pending calls in PostgreSQL.
Checkpoints have a 1 MiB limit; stored inputs have a 256 KiB limit. Model/tool
step counts survive resume, while the wall-clock deadline applies to each run.
Changed prompts, tool contracts or limits reject incompatible checkpoints.

Use `GET /v1/tasks?session_id=...` to find recent tasks, `GET /v1/tasks/{task_id}`
to inspect progress, and `POST /v1/tasks/{task_id}/resume` without a body to
continue the saved task. All routes require current session ownership. Resume
rechecks ownership of saved workbook and document tool sources, including search
candidates. Losing access to any saved source blocks replay. One database
connection holds the task lock and executes its writes; concurrent resume returns
409. Metadata reads use separate bounded capacity. A lost receipt checkpoint
replays the original operation reference rather than creating another append.

For main chat text requests in an existing session, send a `request_key` of up to
128 characters to share task identity across retries. Reusing the key with changed
text, active workbook or document IDs returns 409. Keys require `session_id` and
do not support uploads. Unkeyed requests create separate tasks.

Set `MAIN_CHAT_REQUIRE_APPROVAL=true` to pause checked appends before execution;
it defaults to false. `awaiting_approval` responses include the exact proposal,
operation reference and expiry. Streaming also emits `approval_required`. The
existing `/v1/approvals` routes list, approve or reject these proposals. Approval
executes the stored operation only after checking ownership, fingerprint and
source revisions again. Decisions expire after 24 hours; rejection and expiry
cannot authorize execution. Resume the task after the decision to continue its
saved pending call. Earlier committed receipts remain visible when a later step
waits or fails. Each operation is atomic; the full task is not one transaction.

Main tools do not delete financial cells, edit formulas or execute arbitrary SQL.
Existing grid-deletion approvals remain on the legacy path. The revision-bound
approval flow covers checked appends and table authoring. `NUMERIC_VERIFY_MODE=enforce` blocks ungrounded
main-agent prose without a supervisor rewrite and keeps operation evidence in the
response. This check still does not prove metric-label or financial correctness.
Switch `CHAT_RUNTIME` back to `legacy` to restore the existing route; stored
operation references and receipts remain in the database.

A [live chat smoke run](tests/e2e/outputs/main-chat-2026-09-12.md) at clean
commit `0118cfc` passed the 1,000-row sum and exact append cases once each,
with PostgreSQL, Redis and MinIO available. This does not establish default
rollout readiness or replace the historical benchmark.
A [durable-task smoke run](tests/e2e/outputs/durable-main-chat-2026-09-13.md)
at clean commit `2b6f4f0` also passed both cases once with task IDs. Restart,
concurrency and human approval have separate scripted-model PostgreSQL coverage.

---

## 🧪 The Sandbox: how correctness is measured

Claims about an accounting agent are worth what their evaluation is worth, so the
evaluation is a first-class part of this repo, not an afterthought.

The sandbox runs against **its own Postgres, Redis database, and object store**.
It refuses to start if any of those resolve to the development ones. Ground truth
is **computed, never hand-written**: seeded generators emit a ledger and its own
totals together, and unit tests freeze both, so an expected number cannot drift
away from the data it describes.

Two suites, one dataset:

| Suite | Measures | Result |
|---|---|---|
| Behavior | routing, tool calls and arguments, guardrails, clarification, extraction cache | 61/62 |
| Hard bench | deterministic accounting work, exact-match graded | 38/51 |

The hard bench is organised as a difficulty ladder, and the pass rate falls
across it the way a useful benchmark should:

| Tier | Environment | Current |
|---|---|---|
| Intern | 1 spreadsheet, a few tabs, single-step read | ~100% |
| Clerk | writes, dirty number formats, approval gating | 100% |
| Bookkeeper | multi-sheet, reconciliation, journal balance | 86% |
| Analyst | P&L, tax, aging, variance, 1000+ rows | 74% |
| Controller | several spreadsheets, ten-step month close | 65% |
| Auditor | injection in data, hostile schema, refusal traps | in progress |

**What it currently gets wrong is published, not hidden.** Exact arithmetic over
many rows drifts, and gets worse with volume. Long compound workflows drop tool
calls. Given a journal that does not balance, it has reported one that does. Each
of these has an open engineering lead. See `tests/e2e/README.md`.

The sandbox now also has runtime adapters and an opt-in candidate suite for the
main agent's labelled 1,000-row sum and checked append. It grades native tool
evidence and full fixture workbook state, keeps unsupported contracts visible,
and writes separate reports. Scripted Postgres tests cover this path; the saved
scores above remain historical. See the candidate evaluation instructions in
`tests/e2e/README.md` before running a live model comparison.

---

## 📄 Document intelligence

One component of the platform: turning a photographed receipt or a multi-page
invoice PDF into structured rows. Content-addressed by hash, so the same document
is never extracted twice.

The extraction model is a fine-tuned Qwen 3.5 4B, evaluated with KIEVal, ANLS*,
digit accuracy, and JSON validity.

<div align="center">
  <img src="https://github.com/Laoode/agentic-data-entry/blob/development/docs/Benchmarks.png" alt="Benchmark Results">
</div>

| Model | Entity F1 | Group F1 | Aligned | ANLS* | Digit Accuracy | JSON Validity |
|--------|----------:|----------:|----------:|----------:|----------:|----------:|
| Gemma 4 E2B-it | 49.29 | 11.04 | 39.72 | 36.22 | 50.83 | 99 |
| Gemma 4 E4B-it | 58.61 | 18.46 | 51.17 | 73.49 | 60.38 | 100 |
| Qwen 3.5 2B | 58.88 | 18.40 | 50.36 | 68.96 | 71.15 | 98 |
| Qwen 3.5 4B | 69.93 | 28.17 | 63.05 | 77.22 | 77.87 | 100 |
| **Klaudia (Qwen 3.5 4B Fine-Tuned)** | **87.02** | **71.88** | **84.90** | **93.99** | **94.58** | **100** |

Against the base model: +17.09 Entity F1, +43.71 Group F1, +21.85 Aligned,
+16.77 ANLS*, +16.71 digit accuracy, JSON validity held at 100.

DeepSeek V4 Flash Vision is the current extraction default. The self-hosted Qwen
fine-tune, Gemini, and an offline fixture mock remain selectable through
`KIE_MODEL` and `MOCK_KIE`.

Extraction agent; TOON used 2,212 tokens versus 4,487 for the original indented JSON, a 50.7% reduction. In the full runtime context,
TOON used 2,428 tokens versus 2,929 for compact JSON, saving 501 tokens or 17.1%.
The full agent-quality JSON/TOON comparison remains part of the later sandbox
evaluation.

---

## 🔋 Memory

Two tiers, because they fail differently.

**Continuity** is deterministic. It reads the ledger's own modification times to
answer "where did we leave off", uses no model and no embeddings, and therefore
cannot invent a past that did not happen.

**Semantic memory** is self-hosted mem0 over pgvector, holding preferences and
facts worth carrying between sessions, with conflict-aware updates so a newer
statement supersedes an older one. Hard-partitioned by user, and cross-user
recall is graded as a security bug with a required leak rate of zero.

Both fail soft: if memory is unavailable, the agent answers without it rather
than erroring.

---

## 🍏 Stack

| Layer | Technology |
|---|---|
| API | FastAPI, JWT auth, per-user rate limits |
| Agents | LangGraph supervisor with worker sub-agents |
| Tool boundary | MCP (ledger server, document archive server) |
| Ledger | PostgreSQL, sheet-semantics grids, 23-tool API |
| Memory | mem0 + PostgreSQL history + pgvector + local embeddings |
| Reasoning model | DeepSeek v4 pro today, swappable by configuration |
| Extraction model | DeepSeek V4 Flash Vision; Qwen 3.5 4B fine-tune and Gemini selectable |
| Queue and cache | Redis, Taskiq workers |
| Object storage | MinIO, BLAKE3 content addressing |
| Observability | Langfuse (fail-open tracing) |
| CI | CircleCI: lint, unit, integration with real service containers |
| Client | React Native, Expo (separate repository) |

Model serving lives on a separate inference platform (LiteLLM, vLLM, LMCache,
autoscaling). This repository consumes an endpoint; it does not serve models.

---

## 📟 Running it

```bash
docker compose up -d                  # Postgres, Redis, MinIO
cp .env.template .env                 # add model credentials
./startup.sh                          # API + MCP servers

# optional
docker compose --profile ui up -d        # NocoDB grid view over the ledger
docker compose --profile sandbox up -d   # isolated store for the evaluation suite
```

```bash
uv run pytest tests/unit -q                                    # fast, hermetic
MOCK_KIE=true SHEETS_BACKEND=ledger uv run pytest tests/e2e -q # full evaluation
```

---

## 🗂️ Repository

```text
app/            FastAPI routes, orchestrator, guardrails, extraction,
                memory, verifier, approvals
klaudia/        agent graph: supervisor, sql_agent, data_entry_team, prompts
mcp-ledger/     Postgres ledger MCP server (the default sheets backend)
mcp-archive/    document registry MCP server
mcp-gsheets/    Google Sheets adapter (export mirror, optional backend)
services/embed/ local embedding service for memory
tests/e2e/      the sandbox: dataset, generators, runners, reports
scripts/        migration, mirroring, tenancy and sandbox utilities
```

---

## 📗 Research

Model work runs as a separate track and is not part of this codebase: domain
adaptation for financial document understanding, and post-training to replace the
current reasoning model with one tuned for accounting tool use.

This platform began as an undergraduate research thesis on agentic AI for
financial data entry, and is now being built toward production use by finance
teams.

> [!WARNING]
> The receipt dataset and the fine-tuned extraction model will be released after
> the research paper is published.

---

## 🗺️ Direction

Working toward a system a finance department can run in production:

- deterministic aggregation pushed into the ledger, so arithmetic stops being
  something a model has to reason about
- column-aware number grounding, so a correct value reported under the wrong
  label cannot pass
- scheduled work: period-close reminders, recurring reports
- release gates driven by the sandbox, so a regression blocks a merge

---

## 🍀 Mission

> Where financial records lose balance, Klaudia restores order.

**Entering financial data is not typing numbers. It is preserving the financial
truth of an organisation.**

---

## 🔰 Author

**Yudhy McCodey**

Building agentic systems for real-world financial automation.
