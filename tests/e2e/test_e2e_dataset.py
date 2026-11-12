"""Whitebox E2E — in-process layer.

Drives the real KlaudiaOrchestrator over every dataset case, scoring routing,
content, granular MCP tool calls (via the spy), and latency. One pytest per case;
all turns of a case run in a shared session with self-reversing cleanup.

Run:
  # full suite (includes mutating sheet writes, each self-cleans)
  uv run pytest tests/e2e/test_e2e_dataset.py -v -s

  # non-destructive subset
  uv run pytest tests/e2e/test_e2e_dataset.py -v -s -m "not mutating"

  # fail the run on behavioral misses (gate mode)
  E2E_STRICT=1 uv run pytest tests/e2e/test_e2e_dataset.py -v -s

By default a behavioral miss is RECORDED, not failed — the goal here is a full
measurement and a results table, not a pass/fail gate. Transport/pipeline crashes
always fail the case. The final report writes tests/e2e/outputs/results_inprocess.json.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.e2e.engine_inprocess import run_case_inprocess
from tests.e2e.loader import load_cases
from tests.e2e.report import Report
from tests.e2e.synthetic_cases import SYNTHETIC_CATEGORIES
from tests.e2e.conftest import requires_live

_CASES = load_cases()
_STRICT = os.environ.get("E2E_STRICT", "").lower() in ("1", "true", "yes")
_OUT = Path(__file__).resolve().parent / "outputs" / "results_inprocess.json"

# Shared accumulator across all parametrized cases + the final report test.
_REPORT = Report()


def _params():
    for c in _CASES:
        # Some categories have their own runner and fixture needs, so they are
        # kept out of this bench table:
        #   memory   -> test_memory_e2e.py (long-term memory + reset/isolation)
        #   synthetic hard-bench categories -> test_synthetic_bench_e2e.py (seed
        #     synthetic ledgers under scratch users, isolated from the TABLE.md
        #     baseline)
        if c.category == "memory" or c.category in SYNTHETIC_CATEGORIES:
            continue
        marks = [pytest.mark.e2e]
        if c.mutating:
            marks.append(pytest.mark.mutating)
        yield pytest.param(c, id=c.id, marks=marks)


@requires_live
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("case", list(_params()))
async def test_case(case, orchestrator, spy, extraction_spy, sheet_guard, container):
    records = await run_case_inprocess(
        orchestrator, spy, extraction_spy, case, sheet_guard, container
    )
    for r in records:
        _REPORT.add(r)

    # Transport crashes always fail; behavioral misses fail only in strict mode.
    crashes = [r for r in records if r.view.error]
    assert not crashes, "\n".join(
        f"{r.case_id} turn {r.turn_index}: {r.view.error}" for r in crashes
    )

    if _STRICT:
        misses = [r for r in records if not r.result.passed]
        assert not misses, "\n".join(
            f"{r.case_id} turn {r.turn_index}: {'; '.join(r.result.reasons)}"
            for r in misses
        )


@requires_live
def test_zzz_report():
    """Print the table, dump JSON, and write a per-model markdown summary.

    The markdown file is named tests/e2e/outputs/table-<model>.md so each model's
    run is kept side-by-side for comparison over time.
    """
    if not _REPORT.records:
        pytest.skip("no cases executed")

    from klaudia_api.composition.settings import get_settings

    settings = get_settings()
    model = settings.llm_model
    provider = settings.model_provider

    print(_REPORT.render_table())
    _REPORT.write_json(_OUT)

    md_path = _REPORT.write_markdown(_OUT.parent, model=model, provider=provider)
    print(f"\nJSON written to {_OUT}")
    print(f"Markdown table written to {md_path}")
