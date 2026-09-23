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

The initial local thresholds are a proposal pending product constraints. They may
be tightened before a run, but a failed run must retain its original thresholds.
A changed contract requires a new version and a new report.

- Zero wrong-owner or wrong-destination writes; zero duplicate committed effects.
- Every scheduled deterministic case must pass. Setup failures and unrun cases
  cannot count as passing or disappear from the denominator.
- Three fresh trials for each selected live workflow. Every scheduled trial must
  pass exact state, receipts, intended task completion and separate answer-label
  review. Retain failed runs; corrections start a new run.
- Per-turn observed p95 at or below 60 seconds, using nearest-rank p95 and including
  every completed turn. This small sample does not estimate production tail latency.
- Model inference cost at or below USD 0.10 for each completed turn, including
  main-agent and guardrail calls. Missing usage makes cost qualification incomplete.
  Use a recorded conservative upper bound when provider cache details are absent;
  do not call that amount an invoiced charge. Infrastructure and OCR costs remain
  outside the text/mock-extraction fixture's inference total.
- Actual process-kill recovery must preserve one operation identity and exact state
  across two resumes. Run three trials at each write boundary and test revocation
  at both boundaries.

Live configuration: DeepSeek `deepseek-flash`, temperature 0.5, thinking disabled.
Guardrails use the same provider/model unless a report declares otherwise. Store
model identifiers, settings, source revision, fixture version, prompt digest,
usage completeness, time, state, receipts and all final answers in each report.

For the initial cost upper bound, count all input tokens at USD 0.30 per million
and all output tokens at USD 1.20 per million. These are the documented peak
cache-miss rates checked on 2026-09-24 in the
[DeepSeek pricing contract](https://api-docs.deepseek.com/quick_start/pricing/).
Cache discounts and off-peak pricing can only lower that bound; recheck prices
before reusing it. Provider failures with no usage must remain unknown costs.

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
