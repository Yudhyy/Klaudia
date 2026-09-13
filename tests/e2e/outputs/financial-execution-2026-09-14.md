# Financial execution smoke evidence

Run: 2026-09-13 22:29 UTC (2026-09-14 in Asia/Makassar).
Implementation revision: `8cd6521c0551dc7a3b2f291e6c11006f34a517ef`, clean.
Model: DeepSeek `deepseek-flash`, temperature 0.5, thinking disabled.
Main chat runtime, memory off, mock extraction, fixture version 1.

All six scheduled trials passed through the full main chat service path. Each
trial used a fresh synthetic owner and workbook, inspected registered tables,
called `financial_query`, and retained native evidence in a durable checkpoint.
The grader checked applied policies, source identities, labelled values and both
complete fixture grids. Every grid remained unchanged.

| Capability | Passed | Query latency |
|---|---:|---:|
| Record sorting and pagination | 1/1 | 11.402 s |
| Unique lookup | 1/1 | 14.523 s |
| Left join | 1/1 | 11.632 s |
| Reconciliation | 1/1 | 10.658 s |
| Aging | 1/1 | 10.708 s |
| Variance | 1/1 | 11.788 s |

Latencies measure the orchestrator call, excluding fixture setup and cleanup.
The whole pytest run passed six tests in 81.98 seconds. PostgreSQL, Redis and
MinIO service probes passed before the run. A separate read-only audit after
cleanup found zero fixture users, workbooks, tasks and table operations.

Selected checks:

- Records: exact descending numeric sort, two-record page, four total matches
  and continuation offset 2.
- Lookup: one exact ID match with amount 120 and the requested missing policy.
- Join: four left records, with two explicit missing right records.
- Reconciliation: differences 20 and 80, two left-only records and one right-only
  record, under absolute tolerance zero.
- Aging: future 50, due today 120, 1–30 days 80; the 31–60-day bucket retained a
  zero-balance record distinctly from empty older buckets.
- Variance: 20.00% for the comparable nonzero baseline; explicit zero-baseline
  status for the second key, with missing records retained.

This is one sample per capability, not a reliability estimate or a comparison
against the legacy architecture. The suite checks backend evidence and state;
it does not establish correct metric labels in final prose, natural-language
policy inference, broad accounting coverage or default rollout readiness.
Cross-workbook ownership, stale sources, revoked checkpoint access and exact
stored fractional values have separate scripted PostgreSQL coverage.

Offline validation before the live run: 663 combined unit, ledger and affected
API tests passed. Ruff and both code review axes passed after fixes.

Raw report: [financial-20260913T222924Z-7f12de98.json](financial-20260913T222924Z-7f12de98.json).
