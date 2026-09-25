"""Synthetic hard-bench eval (in-process).

Runs the synthetic-ledger case categories against deterministic data seeded into
scratch users' own spreadsheets, so every ground truth is exact and code-computed
(tests/e2e/synthetic.py, tests/e2e/synthetic_finance.py) rather than
hand-written. Kept out of the main bench (test_e2e_dataset.py) because it seeds
its own fixtures under dedicated user ids, isolated from the docs/TABLE.md
baseline.

Categories and their scratch users live in tests/e2e/synthetic_cases.py; both
runners read that registry, so a new category cannot be seeded here and still be
attempted by the main bench.

Two seeding shapes:
  * One spreadsheet per category (the registry) - the common case.
  * One user owning several spreadsheets (multi_spreadsheet) - each holds an
    identically named sheet, and a turn picks one by logical name, which is what
    the tenancy-isolation cases grade.

Grading is deterministic (digit-normalized amount match), no LLM judge. A
behavioral miss is RECORDED by default (the point at this stage is to measure the
hard bench, not gate on an uncalibrated model); crashes always fail; E2E_STRICT=1
turns misses into failures.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio

from tests.e2e.conftest import requires_live
from tests.e2e.engine_inprocess import run_case_inprocess
from tests.e2e.ledger_seeder import LedgerSeeder, tool
from tests.e2e.loader import load_cases
from tests.e2e.report import Report
from tests.e2e.synthetic_cases import (
    BRANCH_BUILDER,
    BRANCH_CATEGORY,
    BRANCH_USER,
    SEED,
    SYNTHETIC_CATEGORIES,
    SYNTHETIC_SEEDS,
)

_SYNTH_CASES = [c for c in load_cases() if c.category in SYNTHETIC_CATEGORIES]
_STRICT = os.environ.get("E2E_STRICT", "").lower() in ("1", "true", "yes")
_OUT = Path(__file__).resolve().parent / "outputs" / "results_synthetic.json"

# Shared accumulator across all parametrized cases + the final report test. Kept
# separate from the main bench's report so the two tables never overwrite one
# another: this one measures the hard categories, that one the behavior suite.
_REPORT = Report()


async def _ensure_user(container, uid: int) -> None:
    """Create the scratch user row so the spreadsheet/session FKs resolve."""
    await container.db_client.execute(
        'INSERT INTO "user" (user_id, username, email, password_hash) '
        "VALUES ($1, $2, $3, $4) ON CONFLICT (user_id) DO NOTHING",
        (uid, f"e2e-user-{uid}", f"e2e-{uid}@local", "not-a-real-hash"),
    )


async def _branch_spreadsheet_id(container, uid: int, name: str) -> str:
    """Id of this user's spreadsheet called `name`, created once and reused.

    Reusing by name keeps repeated runs from piling up duplicate workspaces,
    which would leave the case bound to an empty one.
    """
    for existing in await container.spreadsheets.list_for_user(uid):
        if existing["name"] == name:
            return existing["spreadsheetId"]
    created = await container.spreadsheets.create(uid, name)
    return created["spreadsheetId"]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def synthetic_ledgers(container):
    """Seed every synthetic category's ledger into its scratch user's spreadsheet.

    Each spreadsheet is resolved through the same SpreadsheetService the
    orchestrator uses per request, so the agent reads exactly what was seeded.
    Yields category -> (spreadsheet scope, ledger); the multi-spreadsheet entry
    additionally carries the logical-name -> spreadsheet-id map its turns bind by.
    """
    if container.spreadsheets is None:
        pytest.skip("synthetic bench needs the ledger service")

    seeded: dict[str, tuple[str | None, object]] = {}
    for category, (uid, builder) in SYNTHETIC_SEEDS.items():
        await _ensure_user(container, uid)
        scope = await container.spreadsheets.resolve_scope(uid)
        ledger = builder(seed=SEED)
        seeder = LedgerSeeder(container.mcp_ledger, spreadsheet_id=scope)
        await seeder.seed_grids(ledger.grids)
        seeded[category] = (scope, ledger)

    await _ensure_user(container, BRANCH_USER)
    branches = BRANCH_BUILDER(seed=SEED)
    branch_ids: dict[str, str] = {}
    for branch_name, ledger in branches.per_branch.items():
        scope = await _branch_spreadsheet_id(container, BRANCH_USER, branch_name)
        branch_ids[branch_name] = scope
        await LedgerSeeder(container.mcp_ledger, spreadsheet_id=scope).seed_grids(
            ledger.grids
        )

    yield {"per_category": seeded, "branch_ids": branch_ids, "branches": branches}

    delete = tool(container.mcp_ledger, "tool_delete_sheet")
    if delete is None:
        return
    teardown = [(scope, ledger.grids) for scope, ledger in seeded.values()]
    teardown += [
        (branch_ids[name], ledger.grids) for name, ledger in branches.per_branch.items()
    ]
    for scope, grids in teardown:
        for name in grids:
            args = {"sheet": name}
            if scope is not None:
                args["spreadsheet_id"] = scope
            try:
                await delete.ainvoke(args)
            except Exception:
                pass


def _params():
    for case in _SYNTH_CASES:
        marks = [pytest.mark.e2e]
        if case.mutating:
            marks.append(pytest.mark.mutating)
        yield pytest.param(case, id=case.id, marks=marks)


@requires_live
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("case", list(_params()))
async def test_synthetic_case(
    case, orchestrator, spy, extraction_spy, synthetic_ledgers, container
):
    # A mutating case is bracketed by a full template restore (drop stray tabs +
    # reseed the frozen baseline) BEFORE and AFTER it runs, mirroring
    # SheetGuard.restore(). Before => a clean start regardless of any prior case;
    # after (in finally) => the template is repaired even if the agent corrupted
    # or deleted a sheet, so a destructive failure never poisons later cases.
    per_category = synthetic_ledgers["per_category"]
    branch_ids = synthetic_ledgers["branch_ids"]
    branches = synthetic_ledgers["branches"]

    async def _restore_baseline() -> None:
        if not case.mutating:
            return
        if case.category in per_category:
            scope, ledger = per_category[case.category]
            seeder = LedgerSeeder(container.mcp_ledger, spreadsheet_id=scope)
            await seeder.restore_to_baseline(ledger.grids)
        elif case.category == BRANCH_CATEGORY:
            for name, ledger in branches.per_branch.items():
                seeder = LedgerSeeder(
                    container.mcp_ledger, spreadsheet_id=branch_ids[name]
                )
                await seeder.restore_to_baseline(ledger.grids)

    await _restore_baseline()
    records = []
    try:
        records = await run_case_inprocess(
            orchestrator,
            spy,
            extraction_spy,
            case,
            None,
            container,
            spreadsheet_ids=branch_ids,
        )
    finally:
        await _restore_baseline()

    for record in records:
        _REPORT.add(record)

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
def test_zzz_synthetic_report():
    """Print the hard-bench table and write its JSON and markdown alongside it.

    Runs last (name-ordered) so it sees every case's records. Written even when
    cases missed - measuring the shortfall is the point of this bench.
    """
    if not _REPORT.records:
        pytest.skip("no synthetic cases executed")

    from config.settings import get_settings

    settings = get_settings()
    model = settings.llm_model

    print(_REPORT.render_table())
    _REPORT.write_json(_OUT)
    md_path = _REPORT.write_markdown(
        _OUT.parent,
        model=model,
        provider=settings.model_provider,
        filename="table-hard-bench.md",
    )
    print(f"\nJSON written to {_OUT}")
    print(f"Markdown table written to {md_path}")
