# Table authoring smoke run

Run: 2026-09-13 06:33 UTC. Source: clean commit `9d2feec`.
Runtime: main; DeepSeek `deepseek-flash`, temperature 0.5, thinking disabled,
memory off. Fixture seed: 1013. Optional approval policy: default off.

| Case | Result | Chat processing time | Evidence |
|---|---|---|---|
| Sum over 1,000 rows | Passed, 1/1 | 6.100 s | Exact total and unchanged workbook state |
| Checked append | Passed, 1/1 | 9.853 s | Exact record and committed receipt |
| Create a second table | Passed, 1/1 | 7.098 s | Budgets registered at H1:I3 with Category and Budget headers; existing claims unchanged |

The authoring case runs through the configured chat orchestrator, guardrails,
durable task storage and main-agent tools. The test checks the full final sheet,
registered name/range/columns and a committed create_table receipt reporting two
changed cells. A read-only cleanup audit found zero remaining fixture tasks,
workbooks or operation rows.

Offline validation passed 627 combined unit, ledger and affected API tests.
The final focused suite passed 12 tests and skipped the three live cases by
default. The API tests include main-agent unregister, mandatory human approval,
execution through the existing approval endpoint and durable resume with the same
receipt. Core tests cover source revisions, current ownership, concurrent retries,
refresh, explicit column identity mapping, preservation of unrelated exact JSONB
numbers and intentional re-registration after unregistering.

Both code-review axes approved after fixes. Standards findings led to shared
approval preparation, explicit connection/write-target contracts and interface
documentation. Spec findings led to repairable overlap errors and operation-neutral
approval text. A checkpoint regression verifies that overlap cannot trap resume.

This run supplies one live sample per case. It does not establish reliability,
latency improvement or default-rollout readiness. Live human decisions, extraction,
queues and streaming remain outside this smoke test. Formula evaluation, inferred
registration and physical grid schema edits remain separate capabilities.

[Raw observations and fixture state](main-chat-20260913T063303Z-3f9ae184.json)
