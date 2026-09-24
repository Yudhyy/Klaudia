"""Opt-in measured qualification of the main runtime on fresh sandbox records."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
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
from tests.e2e.rollout_cases import SCENARIOS, rollout_fixture, run_rollout_trial
from tests.e2e.rollout_measurement import (
    PRICE_CHECKED_ON,
    qualify,
    rollout_configuration,
)
from tests.e2e.sandbox import sandbox_enabled

pytestmark = [
    requires_live,
    pytest.mark.skipif(
        os.environ.get("E2E_RUNTIME_ROLLOUT") != "1",
        reason="Set E2E_RUNTIME_ROLLOUT=1 for measured live qualification",
    ),
]
REPEATS = 3


@pytest.fixture(scope="module", autouse=True)
def rollout_report():
    """Retain every scheduled case before service setup, including unrun cases.

    Yields:
        Mutable evidence and its unique report path.
    """
    from config.settings import get_settings

    settings = get_settings()
    if not sandbox_enabled() or os.environ.get("E2E_SANDBOX_ACTIVE") != "1":
        raise RuntimeError("Rollout trials require isolated sandbox stores")
    if (settings.chat_runtime, settings.memory_mode, settings.sheets_backend) != (
        "main",
        "off",
        "ledger",
    ):
        raise RuntimeError("Use main runtime, memory off and ledger backend")
    if (
        settings.model_provider,
        settings.llm_model,
        settings.llm_temperature,
        settings.llm_disable_thinking,
    ) != ("deepseek", "deepseek-flash", 0.5, True):
        raise RuntimeError(
            "Use the model settings declared in the qualification contract"
        )
    report = {
        "suite": "main_runtime_rollout",
        "contract_version": 2,
        "fixture_version": 1,
        "repeats": REPEATS,
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        ),
        "settings": rollout_configuration(settings),
        "thresholds": {
            "all_scheduled_pass": True,
            "wrong_scope_writes": 0,
            "duplicate_effects": 0,
            "p95_seconds": 60,
            "cost_upper_bound_usd_per_turn": "0.10",
        },
        "price_checked_on": PRICE_CHECKED_ON,
        "limitations": [
            "Small local sample, not production tail latency",
            "Archive handoff uses saved synthetic extraction, not live OCR",
            "Separate semantic review required",
            "No deployment migration or traffic cutover",
        ],
        "trials": {
            f"{scenario}-{repeat}": {"scenario": scenario, "status": "unrun"}
            for scenario in SCENARIOS
            for repeat in range(REPEATS)
        },
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = (
        Path(__file__).with_name("outputs")
        / f"runtime-rollout-{stamp}-{uuid4().hex[:8]}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    try:
        yield report, path
    finally:
        report["qualification"] = qualify(report["trials"])
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Runtime rollout report: {path}")


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("repeat", range(REPEATS))
async def test_live_runtime_rollout(
    scenario, repeat, rollout_report, container, orchestrator
):
    """Observe real model and guardrail calls through authenticated HTTP.

    Args:
        scenario: Declared workflow.
        repeat: Independent repeat index.
        rollout_report: Scheduled report and file path.
        container: Real sandbox dependencies.
        orchestrator: Actual chat coordinator.
    """
    report, path = rollout_report
    observed = report["trials"][f"{scenario}-{repeat}"]
    observed["status"] = "running"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    previous_policy = container.tasks._require_approval
    container.tasks._require_approval = scenario == "approval_resume"
    try:
        app = FastAPI()
        app.state.container, app.state.orchestrator = container, orchestrator
        for router in (chat_router, task_router, approval_router):
            app.include_router(router, prefix="/v1")
        async with (
            comparison_owner(container.ledger_store.pool) as owner,
            comparison_owner(container.ledger_store.pool) as foreign,
        ):
            async with rollout_fixture(container, (owner, foreign)) as fixture:
                observed["fixture"] = {
                    "owner": owner,
                    "foreign_owner": foreign,
                    "workbooks": fixture.workbooks,
                    "tables": fixture.tables,
                    "file_id": fixture.file_id,
                    "session_id": fixture.session_id,
                }
                headers = {
                    "Authorization": "Bearer "
                    + create_access_token(owner, secret=container.settings.jwt_secret)
                }
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                    headers=headers,
                    timeout=180,
                ) as client:
                    try:
                        await run_rollout_trial(client, fixture, observed)
                    finally:
                        observed["final_state"] = await fixture.snapshot()
    except BaseException as exc:
        observed.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        container.tasks._require_approval = previous_policy
        if "prompt" in observed:
            observed["prompt_sha256"] = hashlib.sha256(
                observed["prompt"].encode()
            ).hexdigest()
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
