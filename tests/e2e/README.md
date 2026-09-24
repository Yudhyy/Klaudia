# Klaudia Sandbox — agent evaluation suite

An isolated environment for measuring what the Klaudia agent actually does on
accounting work: which agent it routes to, which tools it calls with which
arguments, and whether the numbers it reports are right.

Two things make it a sandbox rather than a test folder:

1. **Its data stores are its own.** Separate Postgres (port 5433), separate
   Redis logical database, separate object-store bucket. A run cannot read or
   damage development data, and the whole store can be dropped and rebuilt.
2. **Its ground truth is computed, not written.** Every expected amount comes
   from a seeded generator that emits the data and its own totals together, both
   frozen by unit tests. Nobody hand-types an expected number, so the truth
   cannot drift away from the data it describes.

By default a behavioral miss is **recorded, not failed** — a run produces a full
results table rather than stopping at the first problem. `E2E_STRICT=1` turns
misses into failures when you want a gate.

## Strict native formula acceptance

The deterministic formula checks always fail on a mismatch, regardless of
`E2E_STRICT`. They reuse this harness's `Expect`, `ResponseView`, and `evaluate`
with scripted models, the HTTP chat routes, and real PostgreSQL:

```bash
uv run pytest tests/unit/test_formula_acceptance.py tests/integration/api/test_typed_formula_chat.py tests/integration/mcp-ledger/test_persistent_decimal_formulas.py -q
```

Use an isolated database through `PG_TEST_URL`; the local default is
`localhost:5433/klaudia_sandbox`. These tests need no model credentials.
CircleCI includes them in its unit and integration jobs and lints `mcp-ledger`.

`formula_receipts` requires an ordered list of distinct committed typed operations.
Each expectation declares `workbook_id`, `sheet_id`, `calculation_status`, and
the complete `calculation` object: engine version, invalidated/recalculated/failed
counts, and results with cell IDs, exact value strings, statuses, and errors.
Missing or extra receipts, stale results, wrong destinations, and numeric
coercion fail. An empty list requires no receipts; omitting the field disables
this check. Pair it with full workbook state checks and persisted typed-cell
checks, as the API tests do. Receipt evidence alone cannot prove stored state.

Capability labels describe attempts. `prepare_table_append` indicates an append
attempt; `prepare_typed_edit` indicates a typed edit attempt. The shared
`execute_operation` tool alone establishes neither. Historical reports remain
unchanged; account for this mapping correction when comparing new scores.

These checks cover deterministic execution and replay. Natural-language resource
discovery, intent fidelity, and how the model explains a failed calculation need
separate live-model trials with fixed fixtures and recorded failure denominators.

## Live formula acceptance

The opt-in HTTP suite runs four fixed workflows three times each with fresh
owners and workbooks: typed literal discovery, formula recalculation,
calculation failure and repair, and approval with repeated task resume.

```bash
E2E_FORMULA_CHAT=1 CHAT_RUNTIME=main MEMORY_MODE=off MOCK_KIE=true SHEETS_BACKEND=ledger uv run pytest tests/e2e/test_formula_chat_e2e.py -q
```

Set the intended provider/model and spending limit before running paid trials.
The suite uses the configured live model and guardrails. It retains all 12
scheduled outcomes, HTTP responses, exact persisted state, receipts, model
configuration, revision and elapsed time in a unique JSON report under `outputs/`.
It checks complete target grids and the unchanged distractor after replay.
Token usage and monetary cost are not yet collected by this runner.

A passing state check has status `state_passed_prose_pending`. Review the saved
answers separately for requested labels and truthful calculation failures before
accepting the feature. Twelve trials do not establish production reliability,
actual process-kill recovery or scale performance.

The runner itself has offline PostgreSQL checks:

```bash
uv run pytest tests/unit/test_live_formula_cases.py tests/integration/api/test_formula_trial_runner.py -q
```

## Quick start

```bash
docker compose --profile sandbox up -d postgres-sandbox redis minio
MOCK_KIE=true SHEETS_BACKEND=ledger uv run pytest tests/e2e -q
```

Every run prints which stores it used, as its first line:

```
e2e sandbox ON — DATABASE_URL=postgresql://...:5433/klaudia_sandbox, REDIS_URL=...
```

If that line says `sandbox OFF`, the results came from the development stores.

## The sandbox boundary

`sandbox.py` rebinds the datastore environment variables at import time, before
anything reads settings, because the settings object is cached on first build
and the MCP servers are spawned with a copy of this environment.

| Store | Development | Sandbox | Override |
|---|---|---|---|
| Postgres | `localhost:5432/klaudia` | `localhost:5433/klaudia_sandbox` | `E2E_DATABASE_URL` |
| Redis | `redis://localhost:6379/0` | `redis://localhost:6379/9` | `E2E_REDIS_URL` |
| Object store | `klaudia-blobs` | `klaudia-sandbox-blobs` | `E2E_MINIO_BUCKET` |

If any target resolves to the value it is supposed to replace, the run **raises
`SandboxNotIsolated` instead of starting**. A half-isolated run that still
claimed to be sandboxed would be worse than no isolation, because its results
would be trusted. Set `E2E_SANDBOX=0` to run against development on purpose.

Schemas need no migration step: the app and ledger tables are
`CREATE TABLE IF NOT EXISTS` statements run on first connect, so a fresh
database populates itself. `scripts/sandbox_init.sql` adds only the `vector`
extension, which the application role cannot create for itself.

Do not use `docker compose --profile sandbox down -v` as a sandbox-only reset.
It can remove other project volumes, including development data.

### Service checks without model calls

```bash
E2E_INFRA_CHECK=1 uv run pytest tests/e2e/test_sandbox_services.py -q
```

These opt-in checks require sandbox mode and fail when a service is unavailable.
They read PostgreSQL without changing tables and test Redis and MinIO through
the application adapters. Each write uses a unique probe key or object and
removes it afterwards. They do not reset tables or flush Redis. The MinIO check
creates the sandbox bucket if absent and leaves the bucket in place.

Passing these checks confirms service access, not extraction, queue processing
or agent behaviour. They make no model calls.

## Layout

```
tests/e2e/
  dataset/
    SCHEMA.md               # the case/turn/expect schema — read this to add cases
    cases/*.yaml            # the dataset (source of truth)
    fixtures/               # small support files
  sandbox.py                # datastore isolation + the not-isolated guard
  schema.py                 # pydantic models for the dataset
  loader.py                 # load + validate YAML, resolve attachments
  checks.py                 # ResponseView + evaluate() — shared scoring
  spy.py                    # in-process MCP tool-call spy
  report.py                 # results table, JSON, per-model markdown
  engine_inprocess.py       # runs a case against the orchestrator
  ledger_seeder.py          # deterministic grid writer (no LLM in the seed path)
  synthetic.py              # generators: expense ledgers, scale, branches
  synthetic_finance.py      # generators: P&L, tax, receivables, budget, journal
  synthetic_cases.py        # category -> scratch user + builder registry
  sheet_guard.py            # snapshot/restore for the docs/TABLE.md baseline
  conftest.py               # fixtures (container, orchestrator, spy, guard)
  test_e2e_dataset.py       # behavior suite runner
  test_synthetic_bench_e2e.py  # hard bench runner
  test_memory_e2e.py        # cross-session memory runner
  runner_http.py            # black-box POST /v1/chat layer
  gen_postman.py            # Postman collection generator
  outputs/                  # tables + JSON
```

## Three runners, one dataset

The dataset is one pile of YAML; which runner picks up a case is decided by its
`category`, so a case is never run twice or dropped between runners.

| Runner | Cases | Fixtures | Report |
|---|---|---|---|
| `test_e2e_dataset.py` | behavior suite | `docs/TABLE.md` baseline on the test user | `outputs/table-<model>.md` |
| `test_synthetic_bench_e2e.py` | hard bench | generated ledgers under scratch users 90100+ | `outputs/table-hard-bench.md` |
| `test_memory_e2e.py` | cross-session memory | isolated mem0 collection | asserts inline |

`synthetic_cases.py` holds the category registry that splits them. The behavior
suite skips exactly the categories that registry claims, so adding a hard-bench
category cannot leave it half-registered in one runner and missing from the
other.

### Behavior suite

Guardrails, routing, reads and writes, sheet operations, receipt extraction
(cache hit/miss), HITL clarification, multi-turn recall, attachment rejection,
answer-from-context. Content expectations come from the `docs/TABLE.md`
baseline, which `sheet_guard.py` seeds into the test user's spreadsheet and
restores after every mutating case.

### Hard bench

Deterministic accounting work with exact-match grading and no judge. Two
generator modules feed it:

- `synthetic.py` — expense ledgers, dirty number formats, distractor columns,
  missing values, reconciliation, prompt injection in cell data, hostile
  receipt-style schemas, 160-row and 1000-row scale, multi-spreadsheet branches.
- `synthetic_finance.py` — profit and loss across three statement sheets,
  PPN 11% and PPh 23 withholding, receivables aging against a fixed reference
  date, budget vs actual joined by department name with the rows deliberately
  reordered, double-entry journals with one unbalanced entry, and a ten-turn
  month close.

Each category gets its own scratch user, so its data collides with nothing.
Mutating cases are bracketed by a full template restore before **and** after, so
a destructive failure cannot score later cases against a damaged fixture.

## Running

```bash
# Everything
MOCK_KIE=true SHEETS_BACKEND=ledger uv run pytest tests/e2e -q

# Hard bench only, as a gate (~20 min for 51 turns)
MOCK_KIE=true SHEETS_BACKEND=ledger E2E_STRICT=1 \
  uv run pytest tests/e2e/test_synthetic_bench_e2e.py -q

# Behavior suite, skipping cases that write
uv run pytest tests/e2e/test_e2e_dataset.py -v -s -m "not mutating"

# One case
uv run pytest tests/e2e -k AGG01 -v -s

# Black-box over HTTP (needs ./startup.sh)
python -m tests.e2e.runner_http --filter guardrails

# Postman collection
python -m tests.e2e.gen_postman
```

Memory cases need `MEMORY_MODE=write` and the embedding service up; they skip
otherwise.

## Adding a case

1. **Generate the data and its truth together.** Add a builder to
   `synthetic.py` or `synthetic_finance.py` that returns the grids *and* the
   computed answers. Never write an expected amount by hand.
2. **Freeze it.** Add a golden test in `tests/unit/test_synthetic_ledger.py` or
   `test_synthetic_finance.py` that pins the constants, recomputes the truth
   independently from the grids, and checks that no two asserted amounts collide
   as digit substrings — `contains_amount` strips separators, so a nested amount
   would let a wrong answer pass.
3. **Register the category** in `synthetic_cases.py` with its own scratch user.
4. **Write the YAML**, quoting the frozen amounts and naming the golden test that
   guards them.
5. **Mark it `mutating: true`** if it can write, or a destructive failure will
   poison later cases.

Ambiguity is the thing to avoid hardest. If a question has two defensible
readings that produce different numbers, it cannot be graded exactly — either
rephrase it until one reading survives, or drop it. Two cases have already been
rewritten for this: the receivables ranking question now says "totalled per
customer" because the largest single invoice is a different number.

## Assertions available

Declared per turn in `expect:` (full list in `dataset/SCHEMA.md`):

- **Routing** — `route`, `route_any_of`, `forbid_agents`
- **Content** — `content_any`, `content_all`, `content_none`,
  `contains_amount` (digit-normalized), `excludes_amount` (the negative: a
  figure the agent had no legitimate way to reach), `is_rejection`,
  `is_clarification`, `pending_approvals_min`
- **Tools** — `mcp_tools_any`, `mcp_tools_all`, `mcp_tools_none`,
  `mcp_args_contains` (in-process only; skipped, not failed, over HTTP)
- **Extraction** — `cache_hits`, `cache_misses`
- **Latency** — `latency_ms_max`, `latency_hard`

Turn-level controls: `as_user` (run as another tenant), `new_session` (force a
cold session), `spreadsheet` (bind a named workspace for multi-spreadsheet
cases; an unmapped name raises rather than falling back to the default, because
a silent fallback would score a leak case against the wrong workspace).

## Notes for maintainers

- **Fixtures share an event loop.** Fixtures and tests must both use
  `loop_scope="module"`. MCP stdio sessions bind to the loop that created them;
  a function-scoped test loop deadlocks every MCP call.
- **The MCP spy is best-effort.** If a langgraph version invokes tools through a
  path it does not wrap, `mcp_tools_*` degrades to skipped rather than
  false-failing.
- `tools_used` is sub-agent level by design; granular tool assertions come from
  the spy.
- **Borderline arithmetic cases are not deterministic.** Several flip between
  runs. A single run is a measurement, not a release gate.
- Do not run `tests/integration/database` during a bench. It truncates app
  tables. With the sandbox it now hits a different database, but only while the
  sandbox is actually on.

## Candidate runtime evaluation

`sut.py` defines `LegacySUT` and `MainAgentSUT`. The shared in-process runner
defaults to the legacy adapter, preserving its sessions, extraction and bound
workbook. An explicit main-agent adapter runs supported single-turn text cases
with `resource_scope: owned_workbooks`. It rejects legacy routing/MCP assertions,
bound-workbook isolation, attachments, cache expectations and multi-turn sessions
as unsupported. These cases remain non-passing report rows, not successful skips.

New expectations can require capability attempts, exact labelled calculation
evidence, distinct committed receipts and full fixture workbook state. Attempts
are not proof of execution. Calculation checks match table, column, operation,
group keys and signed decimal value; they do not validate every claim in the
final prose. Write checks use receipts and database state to detect duplicates,
wrong destinations and unexpected sheets. Missing or malformed required evidence
fails. State-read errors and runner timeouts retain observed write receipts.

Main-agent reports also retain `append_attempts`: snapshots of submitted
`prepare_table_append` arguments and any returned operation reference. These are
attempts, not proof of preparation, commitment or intent fidelity. Diagnostics
keep at most 24 attempts and 65,536 UTF-8 bytes of JSON arguments per run, plus
bounded references and report metadata. Missing, oversized or non-JSON inputs
increment `append_attempts_omitted`. `append_attempts_observable` is false for
legacy views and outer cancellations where these diagnostics are unavailable;
an empty list then does not prove no attempt occurred. Normal main-agent outcomes
include loaded skill versions. These fields do not change the grading checks.

`tests/integration/mcp-ledger/test_append_field_fidelity.py` adds 14 offline
scripted cases for punctuation, whitespace, Unicode forms, leading-zero category
codes, signed fractions and numeric/string/boolean/null distinctions. It compares
tool arguments, persisted proposals and final cells, then replays the original
operation reference. Six cases preserve the requested values; eight deliberately
change them and must fail the unchanged exact-state grader even after commit.
These test the deterministic path and grader, not live-model interpretation or
natural-language conversion. They do not add scores to the live comparison.

The first candidate cases reuse seeded financial records: a 1,000-row Amount sum
and a checked append. `tests/integration/mcp-ledger/test_capability_runner.py`
exercises the same runner with a scripted model and real Postgres. To measure the
configured live model explicitly:

```bash
E2E_MAIN_AGENT_BENCH=1 MOCK_KIE=true SHEETS_BACKEND=ledger \
  uv run pytest tests/e2e/test_capability_e2e.py -q
```

This suite requires the isolated sandbox and fails on unmet expectations. The
append case carries the `mutating` marker. Each run writes a unique
`outputs/capability-main-<UTC time>-<suffix>.json` file with model settings, source
revision, fixture seed, runtime, observations and state. It does not overwrite the
historical hard-bench or behavior reports. Without the explicit flag, these live
candidate tests skip before service fixtures start.

This is a candidate capability suite, not a same-toolset architecture comparison.
Further case migration and production chat integration remain pending. Keep the historical isolation cases;
do not rename their bound workbook into an active-resource hint.

### Repeated runtime comparison

The [first live comparison](outputs/comparison-2026-09-12.md), at revision
`152bc5e`, measured main at 3/3 sums and 2/3 appends; legacy passed neither strict
case. One main append changed an explicit merchant value. Redis and MinIO were
unavailable during this text-only run. This is not a production cutover gate pass.

The [append-v2 rerun](outputs/comparison-2026-09-12-append-v2.md), at `204c51f`,
recorded main at 3/3 sums and 3/3 appends. All main append trials loaded v2 and
preserved the exact proposed values. Legacy remained at 0/3 for both strict cases.
Three trials do not establish reliability; Redis and MinIO remained unavailable.

The shared sum and append cases grade the same prompt, initial records, exact
labelled answer lines and final workbook state across both runtimes. Each trial
gets a new owner and workbook. Fixture fingerprints must match before execution;
scope checks require that owner to have exactly one workbook before and after it.

```bash
E2E_RUNTIME_COMPARISON=1 E2E_COMPARISON_REPEATS=3 MEMORY_MODE=off \
  MOCK_KIE=true SHEETS_BACKEND=ledger \
  uv run pytest tests/e2e/test_runtime_comparison_e2e.py -q
```

This explicitly calls the configured live model. It requires sandbox services and
allows one to ten repeats per runtime. Three repeats mean twelve trials across
both scenarios. Add `-m 'not mutating'` to select only the sum scenario. Even this
read scenario creates and deletes its own fixture records. Legacy tools, prompts,
guardrails and sessions differ from the main agent's path, so the result is not
an orchestration-only comparison. Reports include both thinking settings and
runtime limits; the main agent's default deadline is shorter than the outer turn
deadline. Provider caching and nondeterminism remain uncontrolled.

Unique `outputs/comparison-<scenario>-<UTC time>-<suffix>.json` files record the
selected schedule before service setup, then update around each trial. Runtime
order alternates between repeats. Setup failures count as errors; interruption
preserves interrupted/unrun entries. Failed service setup leaves the schedule
incomplete. `scheduled_summary` retains the full per-runtime denominator;
`observed_summary` reports pass counts and p50/nearest-rank p95 only for returned
turns, with latency sample counts. Do not treat an incomplete run as a final score.
At three repeats, p95 is the maximum, not a stable tail estimate.

Cleanup removes only the fixture workbook, operation rows and newly created
account/session records. Unexpected dependent records make cleanup fail instead
of cascading deletion. Reports retain observations if cleanup fails. A forced
process kill may leave a `running` entry and fixture records; their exact IDs
appear in the report once recorded. Historical reports remain untouched.

## What this suite does not yet do

- **No LLM judge.** Everything is exact-match, so qualitative properties
  (was the clarifying question the *right* one, is the explanation faithful)
  are not graded at all. Design in `docs/PLAN.md` Part 3, section 2.
- **No tiering.** Cases are flat; there is no difficulty ladder separating a
  one-sheet lookup from a multi-entity consolidation.
- **Historical cases still name agents.** Their existing routing assertions remain
  legacy-specific. New capability cases avoid those names; full migration remains.


## Main chat integration smoke checks

The financial execution smoke suite covers record sorting and pagination,
unique lookup, left join, reconciliation, aging and variance through main chat:

```bash
E2E_FINANCIAL_EXECUTION=1 CHAT_RUNTIME=main MEMORY_MODE=off MOCK_KIE=true \
SHEETS_BACKEND=ledger uv run pytest tests/e2e/test_financial_execution_e2e.py -q
```

It uses fresh synthetic identities and workbooks in the isolated sandbox. Each
case checks saved native financial evidence and both complete grids, then cleans
up its fixtures. Reports retain scheduled, failed and unrun cases, model settings,
revision and latency. One trial per capability is smoke evidence, not a reliability
estimate. The suite does not grade final prose for correct metric labels.

```bash
E2E_MAIN_CHAT=1 CHAT_RUNTIME=main MEMORY_MODE=off MOCK_KIE=true \
  SHEETS_BACKEND=ledger uv run pytest tests/e2e/test_main_chat_e2e.py -q
```

This opt-in calls the configured live model through the real chat orchestrator,
including input/output guardrails, session persistence and application wiring.
It uses fresh disabled owners and seeded sum/append fixtures, grades exact
workbook state, and stores a separate `outputs/main-chat-*.json` report with model
settings, source revision, dirty status and incomplete-case status. Cleanup targets
only fixture records. These two text cases do not test document extraction,
queue workers, streaming latency or the full historical benchmark. Offline HTTP
coverage lives in `tests/integration/api/test_main_chat_routes.py`; recovery,
context budgets and extraction handoff checks live in `tests/unit/test_main_chat.py`.

## Measured main runtime qualification

```bash
E2E_RUNTIME_ROLLOUT=1 CHAT_RUNTIME=main MEMORY_MODE=off MOCK_KIE=true \
SHEETS_BACKEND=ledger MODEL_PROVIDER=deepseek LLM_MODEL=deepseek-flash \
LLM_TEMPERATURE=0.5 LLM_DISABLE_THINKING=true \
uv run pytest tests/e2e/test_runtime_rollout_e2e.py -q --tb=short
```

This schedules three fresh trials for each of six workflows: read then append,
streaming, approval and resume, saved-policy reconciliation, archived extraction
handoff, and foreign-workbook denial. It uses real model and guardrail calls.
Archive facts are synthetic; this is not a live OCR accuracy test.

Reports retain full fixture grids, tool evidence, receipts, labelled answers,
provider usage and timing, including failed requests. Exact automatic checks leave
passing trials at `state_passed_prose_pending`; review the full answers separately
before qualifying a copy of the report. Never rewrite historical raw outcomes.
The fixed limits and pricing sources are in `docs/RUNTIME_ROLLOUT.md`. Interaction
and completed-task replay latency use separate p95 samples. Missing usage leaves
cost unknown. Offline runner checks live in `tests/integration/api/test_rollout_runner.py`.
