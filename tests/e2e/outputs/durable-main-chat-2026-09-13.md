# Durable main chat smoke run

Run: 2026-09-13 01:05 UTC. Source: clean commit `2b6f4f0`.
Runtime: main; DeepSeek `deepseek-flash`, temperature 0.5, thinking disabled,
memory off. Fixture seed: 1013. Checked append approval uses its default, off.

| Case | Result | Chat processing time | Evidence |
|---|---|---|---|
| Sum over 1,000 rows | Passed, 1/1 | 5.912 s | Exact total and unchanged workbook state |
| Checked append | Passed, 1/1 | 7.995 s | Exact final state and committed operation receipt |

Both responses returned a durable task ID and `answered` status. The tests ran
through the configured chat orchestrator, guardrails, session storage and main
agent. A read-only cleanup audit found zero remaining task rows for the two
fixture owners and zero fixture workbooks.

Separately, 612 combined unit, ledger and affected API tests passed, followed by
15 focused checkpoint/task/approval tests. The latter include a two-step task
whose first append commits and whose second approval is rejected, plus access
revocation, concurrent resume, saturated execution capacity, keyed retries,
streamed approval events and recovery after a lost receipt checkpoint.

These are two live text-path samples, not a reliability or latency comparison.
Human approval and restart behaviour have scripted-model PostgreSQL coverage;
this run does not exercise them with the live model. Extraction, queues and
live streaming remain outside this smoke test. Formula calculation remains
unsupported. Legacy remains the default runtime.

The first review pass identified issues that were fixed. Both review agents
reached their usage limit during follow-up; the final fixes have local test
coverage but no completed independent re-review.

[Raw observations and fixture state](main-chat-20260913T010507Z-c9b6c9fd.json)
