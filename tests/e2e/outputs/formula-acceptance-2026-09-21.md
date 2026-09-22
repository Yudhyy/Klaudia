# Native formula live acceptance

Configuration: DeepSeek `deepseek-flash`, temperature 0.5, thinking disabled.
The user authorised paid trials without a spending cap. Fixtures use fresh
synthetic owners, a named target workbook and an active distractor in isolated
PostgreSQL. Guardrails and application HTTP/task/approval services are real.

## Initial measurement

Report: `formula-chat-20260921T124520Z-0cc26c4d.json`. Revision `102d866`.
All 12 scheduled trials failed: the model selected the active distractor instead
of discovering the explicitly named target workbook. The target-state check
failed and both final workbook snapshots retain evidence of the incorrect write.
These are wrong-destination writes within the authorised owner's resources, not
evidence of access to another owner's workbook.

Correction `50f320b` requires named-workbook discovery and comparison against
returned workbook names before choosing an ID. The decimal formula procedure
version changed from 1 to 2. This is a model instruction, not a new backend
authorisation boundary.

## Limits

These trials measure a bounded fixture set, not production reliability. Paid
model outcomes remain separate from deterministic integration checks. A state
pass alone does not certify final prose. No actual process-kill or scale test is
implied. Reports retain original outcomes; reruns do not erase failures.

## Named-destination correction measurement

Report: `formula-chat-20260921T124732Z-580d7214.json`. Started at `102d866`
with the prompt/skill changes subsequently committed as `50f320b`; the report
correctly records a dirty worktree. State checks: 11/12 passed. One trial
incorrectly treated the action verb in "Set Inputs!A2" as part of a sheet name
and asked for clarification without performing the requested write.

Separate answer review found that failure_repair-0 offered unsupported formula
removal, though its failed calculation and repaired value were correct. Two
recalculation answers said other cells were unchanged even while reporting the
dependent total's change. These responses do not pass truthful-prose acceptance.

## Formula-effect correction measurement

Report: `formula-chat-20260921T125230Z-204fc3d3.json`. Runtime revision
`ab9c3bc`. The raw grader recorded 11/12 passes. The failed creation used
`sum(input, literal)` instead of `add(input, literal)`, with the correct source,
unit, rounding and exact value. This is a grading false negative: both operations
implement the requested binary addition. The trial stopped before its later
input edit, so it is not retrospectively counted as a completed passing trial.
Correction `b84007e` accepts either binary addition form and preserves the exact
existing definition and cell identity during an input-only edit.

Answer review found two incorrect optional descriptions: failure_repair-2 called
the numerator a divisor, and recalculation-2 described the cached result as
belonging to a cell distinct from its formula target. Expressions and calculated
values were correct. These answers still fail truthful-prose acceptance.

## Accepted bounded measurement, 2026-09-22

Report: `formula-chat-20260922T000731Z-c3ccfd36.json`. Runtime revision
`c293500`, decimal-formulas version4, corrected addition grader from `b84007e`.
The worktree flag includes untracked evidence and internal notes. All 12 scheduled
trials passed strict state/receipt checks in 189.19 seconds: each of discovery,
recalculation, failure/repair and approval/resume passed three independent trials.
Each completed task was resumed twice without duplicate effects or changed
distractor state. All final answers passed separate factual semantic review.

Across the four recorded runs, 48 trials were scheduled: raw pytest state results
were 0/12, 11/12, 11/12 and 12/12. One third-run failure was the addition grader's
false negative and stopped that workflow before its final edit. Earlier state
passes include prose failures described above; they are not retroactively upgraded.
The final sample alone is not a reliability estimate or default-runtime rollout.

PostgreSQL, Redis and MinIO probes passed (three checks). Offline runner tests
cover its HTTP sequence and failure evidence. A later helper-only extraction
`f726c9c` prevents offline test imports from rebinding database configuration;
its workflow functions are unchanged and a subprocess regression verifies imports.

## Local regression checks

At `0abc985`, the unit and selected real-service integration suite passed
824 tests, with one optional IronCalc skip and one existing wrong-secret JWT
warning (105.26 seconds). Ruff check and format passed across 269 Python files.
The first broad run had three rate-limit failures because a local setting disabled
the limiter; the fixture now enables it explicitly. Application defaults did not
change. These are local results; no remote CI run is claimed.
