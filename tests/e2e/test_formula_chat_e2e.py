"""Opt-in live formula trials through authenticated chat and durable task APIs."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from app.routes.v1.approvals import router as approval_router
from app.routes.v1.chat import router as chat_router
from app.routes.v1.tasks import router as task_router
from app.services.auth.tokens import create_access_token
from tests.e2e.comparison_owner import comparison_owner
from tests.e2e.conftest import requires_live
from tests.e2e.formula_cases import formula_prompt
from tests.e2e.formula_trial import (
    FormulaTrial,
    formula_workbooks,
    run_formula_trial,
    retain_final_state,
)
from tests.e2e.sandbox import sandbox_enabled

pytestmark = [
    requires_live,
    pytest.mark.skipif(
        os.environ.get("E2E_FORMULA_CHAT") != "1",
        reason="Set E2E_FORMULA_CHAT=1 for live formula acceptance",
    ),
]
SCENARIOS = ("discovery", "recalculation", "failure_repair", "approval_resume")
REPEATS = 3


@pytest.fixture(scope="module", autouse=True)
def formula_report():
    """Record every scheduled trial before service setup, including unrun trials.

    Yields:
        Evidence report and its unique file path.
    """
    from config.settings import get_settings

    settings = get_settings()
    if not sandbox_enabled() or os.environ.get("E2E_SANDBOX_ACTIVE") != "1":
        raise RuntimeError("Live formula trials require isolated sandbox stores")
    prompts = {
        stage: formula_prompt(stage)
        for stage in ("inputs", "created", "edited", "failed", "repaired", "literals")
    }
    report = {
        "suite": "live_formula_http_acceptance",
        "model": settings.llm_model,
        "provider": settings.model_provider,
        "temperature": settings.llm_temperature,
        "disable_thinking": settings.llm_disable_thinking,
        "thinking_level": settings.llm_thinking_level,
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        ),
        "repeats": REPEATS,
        "prompts": prompts,
        "prompt_sha256": hashlib.sha256(
            json.dumps(prompts, sort_keys=True).encode()
        ).hexdigest(),
        "limits": "12 model steps per task; three independent trials per scenario",
        "limitations": [
            "Small acceptance sample, not a reliability estimate",
            "No actual process-kill or scale trial",
        ],
        "trials": {
            f"{scenario}-{repeat}": {"status": "unrun"}
            for scenario in SCENARIOS
            for repeat in range(REPEATS)
        },
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = (
        Path(__file__).with_name("outputs")
        / f"formula-chat-{stamp}-{uuid4().hex[:8]}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    try:
        yield report, path
    finally:
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Formula acceptance report: {path}")


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("repeat", range(REPEATS))
async def test_live_formula_chat(
    scenario, repeat, formula_report, container, orchestrator
):
    """Measure one fresh trial without replacing the model or guardrails.

    Args:
        scenario: Fixed workflow name.
        repeat: Independent trial number.
        formula_report: Evidence file established before service setup.
        container: Real application dependencies.
        orchestrator: Actual chat coordinator.
    """
    report, path = formula_report
    observed = report["trials"][f"{scenario}-{repeat}"]
    observed["status"] = "running"
    started = time.monotonic()
    previous_policy = container.tasks._require_approval
    container.tasks._require_approval = scenario == "approval_resume"
    try:
        app = FastAPI()
        app.state.container = container
        app.state.orchestrator = orchestrator
        for router in (chat_router, task_router, approval_router):
            app.include_router(router, prefix="/v1")
        async with comparison_owner(container.ledger_store.pool) as user_id:
            async with formula_workbooks(container.ledger_store, user_id) as identities:
                target, distractor, sheets = identities
                observed["fixture"] = {
                    "user_id": user_id,
                    "target": target,
                    "distractor": distractor,
                    "sheets": sheets,
                }
                headers = {
                    "Authorization": "Bearer "
                    + create_access_token(user_id, secret=container.settings.jwt_secret)
                }
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                    headers=headers,
                    timeout=180,
                ) as client:
                    fixture = FormulaTrial(
                        container.ledger_store,
                        user_id,
                        target,
                        distractor,
                        sheets,
                        scenario,
                    )
                    try:
                        await run_formula_trial(client, fixture, observed)
                    finally:
                        await retain_final_state(fixture, observed)
        observed["status"] = "state_passed_prose_pending"
    except BaseException as exc:
        observed["status"] = "failed"
        observed["error_type"] = type(exc).__name__
        raise
    finally:
        container.tasks._require_approval = previous_policy
        observed["elapsed_seconds"] = time.monotonic() - started
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
