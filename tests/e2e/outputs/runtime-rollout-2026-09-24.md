# Local main runtime qualification

Scope: local pre-release use. The maintainer confirmed on 2026-09-24 that there
are no deployed users or mem0 records to migrate. No deployment, external-memory
deletion or database restoration occurred. The contract is
[`docs/RUNTIME_ROLLOUT.md`](../../../docs/RUNTIME_ROLLOUT.md).

## Retained run history

| Report suffix | Runtime revision | Automatic workflow checks | Separate answer review | Outcome |
| --- | --- | --- | --- | --- |
| Setup invocation | b9e2121 | 18 setup errors | Not run | Wrong settings attribute; no provider calls |
| b3bdc477 | b9e2121 with report-field fix | 13/18 | Not fully reviewed | Guards disabled locally; label-format failures and one row/aggregate mix-up |
| ee1db94b | c7acf08 | 18/18 | 17/18 by separate review agent | Unsupported absence claim for a foreign workbook |
| 38ef2d0f | e502ae3 | 18/18 | 17/18 by primary assistant after review-agent quota exhaustion | Same absence error remained in one answer |
| 15a3e0fc | b8e9247 | 18/18 | 18/18 by separate review agent | One provider connection timeout leaves cost incomplete despite a successful retry |
| b8094869 | 51ee7fe with pending tests | 18/18 | Primary assistant found a disqualifying answer | Foreign trial 2 claims no Actuals table exists in an inaccessible workbook |
| 9d442e34 | 51ee7fe with authoring access-limit fix | 17/18 | All three foreign-resource answers respect access limits | Policy trial 1 gives correct differences but omits required labelled lines; cost and latency checks pass |
| 6802dbd6 | b6f522a | 18/18 | Primary assistant found a disqualifying answer | Approval trial 1 falsely says the target workbook contains another workbook; cost and latency checks pass |
| b8811625 | 5e41140 | 17/18 | Primary assistant: no further factual failures | Multi-intent trial 1 repeats a correct amount label; maintainer accepts this open issue for local cutover |

The final run remains failed under the strict contract. Its separate review file
records the 2026-09-25 maintainer exception and the next model investigation.
The repeated label did not cause a duplicate write. That case stopped before
replay checks; no replay pass is claimed for it.

Raw JSON outcomes remain unchanged. Review JSON files record verdicts separately
and retain the full scheduled denominator. The first setup failure log remains
in local test outputs as `runtime-rollout-20260924-setup-failure.log`.

Corrections followed observed failures: require enabled guards and record actual
settings; accept exact amount labels with explanatory suffixes; bind reconciliation
differences to record keys; state both in the prompt and discovery evidence that
owner-filtered results cannot establish foreign-resource existence or contents.
No failed run became a passing run after changing the grader.

## Other qualification evidence

- Eight real process-kill tests pass: three repeats before execution, three after
  committed execution, and ownership revocation at both boundaries.
- Two configuration rollback simulations pass for pending and committed tasks.
  They preserve task/approval records and resume the same operation without an
  extra ledger effect. They do not prove deployment bootstrap behaviour.
- A bounded storage profile passes and remains in
  `storage-profile-20260924T054832Z-39f6faf8.json`. It gives no reason to migrate
  storage within the current limits; it does not establish production capacity.
- Formula acceptance remains the earlier bounded four-scenario, three-repeat
  evidence. The rollout archive cases use synthetic saved extraction, not live OCR.

This sample does not establish a production reliability rate. Runtime selection,
source retirement, and a public release remain distinct changes.
