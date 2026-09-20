# Typed values and native formulas

Status: bounded native formula implementation, with opt-in main-agent integration.
This document defines the supported behavior; it does not claim release readiness,
Excel compatibility, or a default-runtime change. The engine is `native-decimal-v1`.
IronCalc remains a separate [capability evaluation](IRONCALC_CAPABILITIES.md).

## Values and calculation semantics

| Kind | Input representation | Meaning |
| --- | --- | --- |
| `decimal` | Plain decimal string, such as `"+001.2300"` | Preserve the original literal; calculate numerically |
| `text` | String | Preserve text, including numeric-looking codes |
| `boolean` | JSON boolean | No string or integer coercion |
| `date` | Valid `YYYY-MM-DD` string | Calendar date without a timezone |

There is no typed blank/null input or clear operation in this subset. Do not infer
locale, currency, or type from a cell's display. Units and display formats are
optional stored metadata; the engine does not render formats, convert currencies,
or enforce dimensional compatibility. Existing unmanaged cells remain unchanged
unless an explicit edit replaces them. There is no automatic legacy backfill.

Decimal input literals allow a sign and decimal point, but no exponent notation,
thousands separators, non-finite values, or binary floats. Inputs and evaluated
operands have a 64-digit and bounded-exponent contract. Formula literals remain
text in the definition. Arithmetic uses exact rational intermediates within each
operation; unsupported magnitude or arithmetic reports failure.

A formula contains one native operation and explicit operands:

```json
{
  "operation": "add",
  "operands": [{"cell_id": "cell_observed_input"}, {"literal": "0.2"}],
  "rounding": {"places": 2, "mode": "ROUND_HALF_EVEN"}
}
```

The cell ID above is illustrative; callers must use IDs returned by inspection.
Dependencies can reference decimal inputs or computed cells on other sheets in
the same owned workbook. Text, boolean, date, unknown, cyclic, and cross-workbook
dependencies reject. References do not grant access.

| Operation | Operands | Result policy |
| --- | --- | --- |
| `add`, `subtract`, `multiply`, `divide` | Exactly two | Half-even or half-up |
| `sum` | One to 64 | Half-even or half-up |
| `round` | One | Half-even or half-up |
| `roundup` | One | `ROUND_UP`, away from zero |
| `rounddown` | One | `ROUND_DOWN`, toward zero |

Every persisted computed cell requires an explicit scale of two or four places.
The pure engine also has an exact unrounded mode, but persistence does not expose
that mode. These scales define this subset, not every currency or accounting rule.
If the required policy is absent, clarify it before preparing a financial edit.

**Rounding occurs once per computed cell, before a dependent cell reads it.**
At two places, a cell computing `1 / 3` stores `"0.33"`; a dependent cell multiplying
that value by three stores `"0.99"`. This does not implement a single unrounded
expression `(1 / 3) * 3`. Rates and intermediate values needing another scale are
outside the persisted formula contract.

## Storage and exact reads

`ledger_typed_cell` owns the managed cell's stable identity, position, kind, raw
value, formula, dependencies, result/status/error, and engine version. A grid
scalar is a derived projection at that position. Unmanaged grid positions remain
ordinary ledger values. Definitions and raw inputs survive process restart;
computed results can be recalculated from those definitions and dependencies.
There is no separate persisted IronCalc workbook and no automatic repair worker.

Input edits, derived grid values, changed sheet revisions, and receipts commit in
one PostgreSQL transaction. Numeric projection avoids conversion through Python
floats. Original decimal spelling and display metadata remain in typed storage;
the numeric grid projection does not preserve their lexical representation.

| Read path | Numeric contract |
| --- | --- |
| `inspect_typed_workbook` | Decimal `raw_value` and `calculated_value` are strings; kind/status accompany them |
| Typed operation receipts | Calculation result `value` is decimal text or null on failure |
| Chat response, stream, task and approval receipt transport | Retain the receipt's strings; consumers must not coerce them to binary numbers |
| Registered `calculate` / `financial_query` | Decode grid fractions as Decimal; return numeric amounts as strings under their own query contracts |
| Ordinary grid/snapshot reads | Legacy JSON-number representation; fractions decode through Python floats and clients may also lose large-integer precision |

Typed inspection and receipt values are the exact read interface for this feature.
Do not use a legacy grid round-trip to recover an exact managed input. Extending
exact typed values to every legacy client endpoint requires a separate compatibility
change. Query tools retain their own null, grouping, unit, and precision limits.
A date projected into the grid is a string; generic grid readers do not recover
its typed date metadata.

## Checked edits and revisions

The main-agent tools are available when an operation executor is supplied:

1. Discover an owned workbook/sheet and inspect placement if raw cells matter.
2. Call `inspect_typed_workbook` for managed values, IDs, revisions, and a snapshot
   fingerprint. Ownership and the source state come from one SQL statement.
3. Prepare `edit_typed_cells` with the unchanged fingerprint and explicit edits.
4. Execute the returned original operation reference, respecting any approval wait.
5. Read both the operation status and calculation status. Inspect again before
   another edit and refresh stale table metadata before a registered calculation.

`set_input` declares or replaces an input. It cannot overwrite a computed cell.
`set_formula` creates or replaces a formula, including converting an existing input
into a computed cell, subject to graph validation. New inputs must be committed
and inspected before another proposal can refer to their generated cell IDs.
Each position can occur once per batch. Typed edits cannot change registered
header cells.

Preparation persists intent and optional approval, not cell changes. Execution
rechecks ownership, the stored proposal fingerprint, approval where required, and
the workbook snapshot. Replays return the original committed receipt after a
current ownership check. A fresh preparation is a new operation; retries after an
unknown outcome must retain the original reference.

Execution locks the workbook and its source sheets. Calculation finishes before
commit; there is no transaction across a model or human wait. The whole-workbook
fingerprint includes grids, sheet metadata/revisions, managed cells, and registered
table metadata. An unrelated workbook change can therefore invalidate a proposal.

Ordinary writes cannot alter managed values or managed-sheet layout, move a managed
sheet to another workbook, or delete a managed sheet. Same-layout writes to other
cells remain possible. Sheet rename preserves sheet/cell identity. The managed
sheet guard does not prohibit deletion of its containing workbook through existing
workbook lifecycle operations; this subset adds no new workbook-delete tool.

Formula removal, moving managed cells, range references, automatic extension of a
table formula on append, cross-workbook links, and Excel expressions are unsupported.
Do not infer a formula from a column name. Checked catalogue refresh remains a
separate operation when typed edits make registered table observations stale.

## Failure and receipt contract

| Condition | Outcome |
| --- | --- |
| Invalid shape/type, unsupported operation, unknown/non-decimal dependency, cycle, stale snapshot, absent ownership | Reject without cell changes |
| Approval missing, rejected, expired, or bound to stale sources | No execution |
| Division by zero or invalid/overflowing arithmetic in a valid graph | Commit requested edits with failed formula state and a null cached result |
| Dependency calculation failed | Mark the dependent formula failed with no value |
| Storage, projection, or receipt persistence fails | Roll back the operation transaction |
| Final workbook exceeds source or inspection evidence bounds | Roll back so the next typed inspection remains possible |

Cell status is `input`, `pending`, `current`, or `failed`. Pending rows are an
internal transaction stage in this synchronous implementation; no background
calculation or publicly committed pending workflow is promised.

A committed receipt separately reports:

- `calculation_status=not_required` when no managed formulas exist.
- `calculation_status=current` when all evaluated formulas succeeded.
- `calculation_status=failed` when at least one evaluated formula failed.
- Actual invalidated, recalculated, and failed counts, plus per-formula results.
- Changed sheet revisions, operation identity, and catalogue refresh status.
- `accounting_validation=not_run`.

Every typed edit currently recalculates all managed formulas, including unaffected
ones. Invalidation counts only affected formula identities; recalculation counts
all evaluated formulas. An unrelated failed formula can make the workbook's
receipt calculation status failed. Updated formula projections can advance sheet
revisions even when a calculated number is unchanged.

Ordinary snapshots and registered financial reads reject a source sheet containing
pending/failed managed formulas, even outside the selected table. Use typed
inspection to diagnose and repair it. Do not convert a failed/null result into zero.
A historical receipt describes its committed operation, not the latest workbook.
Neither a current calculation nor a committed receipt proves accounting invariants,
user-intent fidelity, or the metric labels in a final model answer.

## Bounds and verification

| Boundary | Limit |
| --- | --- |
| Workbook | Eight sheets; 1 MiB combined encoded grids; 64 registered tables |
| Managed graph | 256 input/computed cells total |
| Edit batch | 32 distinct positions |
| Position | Rows 1–1000; columns 1–256, both one-based |
| Formula | 64 operands; supported operation arity still applies |
| Decimal literal | 128 characters and the 64-digit arithmetic budget |
| Text/date payload | 512 characters before kind-specific checks |
| Metadata | Unit 64 characters; display format 128 characters |
| Proposal and evidence | Each at most 65,536 encoded bytes |

Source budgets bound accepted work, not the memory cost of fetching oversized
JSONB grids before rejecting them. This is synchronous bounded execution, not a
storage-scale claim. Engine version identifies calculation semantics; automatic
migration between future engine versions is not implemented.

Run the focused tests against an isolated PostgreSQL database:

```bash
uv run pytest tests/unit/test_decimal_formula_engine.py tests/unit/test_typed_value_contracts.py -q
uv run pytest tests/integration/mcp-ledger/test_persistent_decimal_formulas.py tests/integration/api/test_typed_formula_chat.py -q
```

These tests cover exact typed reads, recalculation and repair, stale snapshots,
write guards, catalogue refresh, approval rejection/expiry/concurrency, revoked
ownership, HTTP/streamed receipts, and replay after an injected post-commit
checkpoint failure. The API tests use scripted models and real PostgreSQL.
They do not prove recovery from an actual process kill, live-model task fidelity,
or production-scale performance. Those remain separate acceptance measurements.
