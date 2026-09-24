# Main runtime qualification

This contract defines the local pre-release checks before changing the default
runtime. It does not certify an existing deployment or migrate external user data.
`CHAT_RUNTIME=main` selects the candidate; `legacy` remains the default until the
checks pass. Runtime and storage changes must remain separate.

## Declared capability matrix

| Capability | Deterministic evidence required | Live evidence required |
| --- | --- | --- |
| Owned resource discovery and exact append | Full target and distractor state, checked original reference | Named destination and literal-value fidelity |
| Multiple intents in one turn | Read plus write, exact state and labelled metrics | Complete both intents without changing requested values |
| Formulas | Typed input, dependency, failure and recalculation contracts | Existing four-scenario acceptance remains bounded evidence |
| Approval and continuation | No write before approval; original proposal once after resume | Explain pending approval and completion truthfully |
| Extraction handoff | Synthetic document ingestion/archive facts reach chat | Use extracted values faithfully; OCR provider accuracy is separate |
| Streaming | Same final state and receipts as normal HTTP | Complete streamed response with correct labels |
| Restart | SIGKILL before execution and after commit; recover original reference | No model call is needed to prove database replay |
| Scope revocation | Deny resumed access before and after committed writes | Foreign resources remain unavailable |
| Human context and reconciliation | Scope, revision, policy applicability, import and rollback | Use saved policy and explain its bounded checks |
| Configuration rollback | Explicit runtime selection and retained ledger/task state | No live traffic cutover in local qualification |

Existing unregistered sheets require registration or supported table authoring.
Cross-workbook formulas, automatic formula extension on append, shared workspace
roles, arbitrary workbook scale, unrestricted Excel syntax and full accounting
compliance remain unsupported. Imported preferences are human-reviewed context,
not ledger facts. Raw historic benchmark scores remain unchanged.

## Acceptance thresholds declared before trials

Version 2 fixes the following local qualification thresholds. Deployment limits
still need product input. A failed run must retain its original thresholds.
A changed contract requires a new version and a new report.

- Zero wrong-owner or wrong-destination writes; zero duplicate committed effects.
- Every scheduled deterministic case must pass. Setup failures and unrun cases
  cannot count as passing or disappear from the denominator.
- Three fresh trials for each selected live workflow. Every scheduled trial must
  pass exact state, receipts, intended task completion and separate answer-label
  review. Retain failed runs; corrections start a new run.
- Per-turn observed p95 at or below 60 seconds, using nearest-rank p95 and including
  every completed interaction. Measure completed-task replays as a separate group
  with the same limit; fast replays cannot dilute interaction latency. This small
  sample does not estimate production tail latency.
- Model inference cost at or below USD 0.10 for each completed turn, including
  main-agent and guardrail calls. Missing usage makes cost qualification incomplete.
  Use a recorded conservative upper bound when provider cache details are absent;
  do not call that amount an invoiced charge. Infrastructure and OCR costs remain
  outside the text/mock-extraction fixture's inference total.
- Actual process-kill recovery must preserve one operation identity and exact state
  across two resumes. Run three trials at each write boundary and test revocation
  at both boundaries.

Live configuration: DeepSeek `deepseek-flash`, temperature 0.5, thinking disabled.
Text guardrails use the same provider/model; injection screening uses Groq
`meta-llama/Llama-Prompt-Guard-2-86M`. Store
model identifiers, settings, source revision, fixture version, prompt digest,
usage completeness, time, state, receipts and all final answers in each report.
The runner requires guards to be enabled and records that setting. Version 1
allowed local disabled guards and rejected some valid explanatory label suffixes;
its diagnostic outcomes cannot qualify this contract. Version 2 accepts a dash
clause or parenthetical explanation after an exact labelled amount. Full-answer
semantic review must still reject contradictions or unsupported claims.

For the initial cost upper bound, count all input tokens at USD 0.30 per million
and all output tokens at USD 1.20 per million. These are the documented peak
cache-miss rates checked on 2026-09-24 in the
[DeepSeek pricing contract](https://api-docs.deepseek.com/quick_start/pricing/).
Cache discounts and off-peak pricing can only lower that bound; recheck prices
before reusing it. Provider failures with no usage must remain unknown costs.
Groq injection screening uses USD 0.04 per million input and output tokens,
checked on the same date in the
[Groq model contract](https://console.groq.com/docs/model/meta-llama/llama-prompt-guard-2-86m).

## Cutover and rollback

The local check changes runtime configuration only. It does not publish a release
or deploy to users. Before changing an existing deployment, inventory actual users,
registered tables, active tasks and mem0 records. Complete reviewed preference
migration and resolve unsupported workflows; an empty local fixture is not proof
that production migration is unnecessary.

Record the prior revision and configuration. Finish or pause active workflows
before switching; legacy cannot resume main-agent tasks. Keep the original main
revision available to resume them with current ownership checks. Rollback changes
configuration/revision, never restores an old database snapshot over new writes.
Do not delete external memory, ledger data, checkpoints or approvals during rollback.

Before storage changes, measure whole-grid reads, workbook lock contention,
full-graph recalculation and connection occupancy at declared fixture sizes. Keep
the existing storage unless those results justify a separately reviewed migration.
No folder layout or agent-count comparison alone establishes a storage need.

## Local storage observations

The 2026-09-24 profile used one sheet per workbook, ten reads at each size, and
independent chain graphs with 31, 127 and 255 formulas. Median whole-grid snapshot
reads were 1.24, 2.38 and 8.75 ms for 3,597, 58,181 and 472,949 source bytes.
Median full-graph evaluation took 0.27, 1.16 and 2.48 ms. Graph timing excludes
database persistence and grid projection; these numbers do not measure a full
typed-edit transaction or production concurrency.

A held workbook lock blocked a second writer to that workbook until its deliberate
50 ms lock timeout; another workbook remained available. Lock acquisition worked
after release. Holding all five ledger connections exhausted that pool; all five
returned to idle after release. Task execution has a separate four-connection
pool and two metadata-read connections. The task saturation integration test checks
that progress remains readable while execution capacity is full.

These bounded observations do not justify a storage migration. Retain the current
storage and its size limits. Repeat profiling with deployment workload sizes before
raising limits or choosing a row/cell storage model. The raw profile is
`tests/e2e/outputs/storage-profile-20260924T054832Z-39f6faf8.json`.

The rollback integration checks disable main services, reopen their task store and
recover pending or committed work without changing persisted approval/task records
or duplicating writes. They simulate the service transition; they do not test a
deployment restart or real application bootstrap under changed configuration.
