# IronCalc capability check

Checked on 2026-09-14 with Python 3.11 and IronCalc 0.8.2 on macOS.
This is an isolated candidate evaluation. IronCalc is not a production dependency,
and the ledger does not yet persist or recalculate formulas.

The five capability tests pass because they assert the observed behaviour,
including limitations. Passing these tests does not approve the engine for exact
financial calculations.

| Capability | Observed result |
|---|---|
| Arithmetic and SUM | Evaluates synthetic inputs |
| Input changes | Explicit evaluation updates dependent results |
| Cross-sheet references | SUM reads another sheet |
| Rename and row insertion | Formula references rewrite and retain the result |
| Deleted source sheet | Dependent formula returns `#REF!` |
| Division by zero | Returns `#DIV/0!` |
| Circular reference | Returns `#CIRC!` |
| Unknown function | Returns `#NAME?` |
| Exact decimal arithmetic | Fails: `0.1+0.2` caches `0.30000000000000004` |
| Large exact integer | Fails: `9007199254740992+1` does not cache `9007199254740993` |
| Numeric formula round-trip | Fails: `=1.234567890123456789` returns changed formula text |
| Raw calculated value API | No public reader in the tested binding |
| Dependency inspection API | No public method in the tested binding |

The displayed result of the decimal sum is `0.3`. The test reads the temporary
XLSX numeric cache to expose the underlying result. This is a test probe, not a
proposed production value-retrieval path. Likewise, automatic reference rewriting
does not establish a public dependency API.
The API findings also use a separate inspection of every public method returned
by `dir(model)`, checked against the official Python reference. The automated test
alone only rejects the named raw-value method and dependency/parser method names.

Run the isolated suite without changing project dependencies:

```bash
uv run --with ironcalc==0.8.2 pytest tests/unit/test_ironcalc_capabilities.py -q
```

Without IronCalc installed, the module skips. A different installed version fails
the version check rather than inheriting the recorded results.

The production numeric contract remains a decision gate. An exact decimal engine
would preserve the current financial arithmetic convention. An Excel-style
floating-point engine would need a distinct, explicit rounding and precision
contract; it must not claim exact ledger arithmetic. Original formula text would
also need independent storage rather than relying on this engine's round-trip.

Not yet measured: Linux deployment, saved-workbook reload, cross-workbook links,
date/boolean/text fidelity, explicit rounding functions, concurrency, execution
limits and performance at scale. No production adapter or supported formula
subset has been selected. Cross-workbook access must remain closed until its
ownership and dependency semantics have tests.

Sources: [pinned package](https://pypi.org/project/ironcalc/0.8.2/),
[official Python API](https://ironcalc.readthedocs.io/en/stable/raw_api_reference.html),
and [executable probes](../tests/unit/test_ironcalc_capabilities.py).
