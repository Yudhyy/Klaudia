# IronCalc capability check

## Released 0.8.3

Checked on 2026-09-20 with Python 3.11 on macOS. The seven isolated tests pass;
some assert limitations rather than desirable behavior. IronCalc is not a
production dependency. Klaudia's bounded native formula contract is documented
in [Typed values and native formulas](TYPED_FORMULAS.md).

The APIs must be evaluated separately: `ironcalc.create()` returns raw `Model`,
which requires explicit `evaluate()`. `ironcalc.UserModel(...)` evaluates edits
automatically. Results from one API do not establish the other's methods.

| Capability | Raw Model 0.8.3 | UserModel 0.8.3 |
| --- | --- | --- |
| `get_cell_value` | Native float, text, boolean, or None | Absent |
| `get_cell_value_by_ref` | Reads `Sheet1!A1` | Absent |
| `get_cell_formula` | Formula text or None | Absent |
| Decimal sum `0.1+0.2` | Native float and XLSX cache: `0.30000000000000004`; display: `0.3` | Display: `0.3`; native reader unavailable |
| Formula content | Available; long numeric literals lose precision on round-trip | Simple expression retrieved; long-literal fidelity not measured |
| Large integer arithmetic | `9007199254740992+1` loses exactness in the XLSX cache | Not measured |
| Cross-sheet SUM and input edits | Explicit evaluation updates totals | Not measured in this probe |
| Sheet rename and row insertion | References rewrite and retain calculated result | Not measured |
| Deleted source sheet | Formatted `#REF!` | Not measured |
| Division by zero, cycles, unknown functions | Formatted `#DIV/0!`, `#CIRC!`, `#NAME?` | Not measured |
| Named dependency/parser API | No public method containing `depend` or `parse` found | Same name check; not a complete API capability proof |

The XLSX cache checks remain test probes, not a production retrieval strategy.
The raw reader now permits direct value inspection. A reader returning floats
does not provide exact decimal arithmetic, and a formula reader does not promise
the original user spelling of numeric literals. Reference rewriting alone does
not establish a public dependency graph API.

Run without adding IronCalc to project dependencies:

```bash
uv run --with ironcalc==0.8.3 pytest tests/unit/test_ironcalc_capabilities.py -q
```

The module skips if IronCalc is absent and asserts the version when installed.
Use the released package for this command. A locally built checkout may retain
`0.8.3` metadata while exposing unreleased methods; identify such probes by source
SHA and do not mix their results with this release table.

## Unreleased UserModel readers

GitHub status checked on 2026-09-20:

- [PR #1421](https://github.com/ironcalc/IronCalc/pull/1421) merged on September 17,
  adding `get_cell_value` to Python UserModel.
- [PR #1427](https://github.com/ironcalc/IronCalc/pull/1427) remained open, adding
  `get_cell_value_by_ref` and `get_cell_formula` to UserModel.

The proposed numeric readers return floats. These changes improve access to
results but do not establish exact arithmetic, original-literal fidelity, or
dependency inspection. Recheck the release contents and rerun probes before
adopting a later package. No local-source or 0.9 runtime result is claimed here.

## Historical 0.8.2 evidence

On 2026-09-14, five raw-Model probes passed on Python 3.11/macOS at capability
commit `963ebc6`. They recorded binary decimal results, large-integer loss,
numeric-formula round-trip loss, cross-sheet recalculation, structural reference
updates, and visible formula errors. The tested binding lacked `get_cell_value`.
That absence finding is historical and does not apply to raw Model 0.8.3.
The current test module pins 0.8.3; reproduce the old suite from its original
revision in an isolated checkout rather than changing this working tree.

## Decision boundary and unmeasured work

Klaudia keeps native decimal formulas as its bounded first contract. An IronCalc
adapter remains a candidate for spreadsheet compatibility under an explicit
numeric policy. A binary arithmetic example alone does not reject that use case;
it does reject a claim that the engine preserves arbitrary exact decimal amounts.

Not measured here: Linux deployment, workbook save/reload fidelity, cross-workbook
links, date semantics, explicit rounding functions, concurrency, execution limits,
and scale. Text/boolean/blank raw readers have small synthetic checks, not full
workbook fidelity coverage. No production IronCalc adapter is approved by these
results. Cross-workbook access stays unsupported until authorization and dependency
semantics have tests.

Sources: [released package](https://pypi.org/project/ironcalc/0.8.3/),
[Python API documentation](https://ironcalc.readthedocs.io/en/stable/raw_api_reference.html),
and [executable probes](../tests/unit/test_ironcalc_capabilities.py).
