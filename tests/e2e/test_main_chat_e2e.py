"""Opt-in live smoke checks for the actual main chat integration."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from app.models.chat import KlaudiaMessage
from app.services.core.verifier import extract_claims
from tests.e2e.capability_cases import CAPABILITY_SEED, seeded_capability_case
from tests.e2e.comparison_owner import comparison_owner
from tests.e2e.conftest import requires_live
from tests.e2e.sandbox import sandbox_enabled

pytestmark = [
    requires_live,
    pytest.mark.skipif(
        os.environ.get("E2E_MAIN_CHAT") != "1",
        reason="Set E2E_MAIN_CHAT=1 to call the configured live model through chat",
    ),
]


@pytest.fixture(scope="module", autouse=True)
def chat_report():
    """Record scheduled cases before container setup and retain incomplete runs.

    Yields:
        Mutable report with only non-secret configuration and synthetic evidence.
    """
    from config.settings import get_settings

    settings = get_settings()
    if os.environ.get("E2E_SANDBOX_ACTIVE") != "1" or not sandbox_enabled():
        raise RuntimeError("Main chat smoke checks require the isolated sandbox")
    if settings.chat_runtime != "main" or settings.memory_mode != "off":
        raise RuntimeError("Use CHAT_RUNTIME=main and MEMORY_MODE=off")
    report = {
        "suite": "main_chat_smoke",
        "model": settings.llm_model,
        "provider": settings.model_provider,
        "temperature": settings.llm_temperature,
        "disable_thinking": settings.llm_disable_thinking,
        "fixture_seed": CAPABILITY_SEED,
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "git_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        ),
        "cases": {name: {"status": "unrun"} for name in ("sum_1000", "append")},
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = (
        Path(__file__).with_name("outputs")
        / f"main-chat-{stamp}-{uuid4().hex[:8]}.json"
    )
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    try:
        yield report
    finally:
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Main chat smoke report: {path}")


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("scenario", ["sum_1000", "append"])
async def test_main_chat_live(scenario, container, orchestrator, chat_report):
    """Grade exact outcomes through extraction-ready app services and guardrails."""
    observed = chat_report["cases"][scenario]
    observed["status"] = "running"
    store = container.ledger_store
    try:
        async with comparison_owner(store.pool) as user_id:
            observed["user_id"] = user_id
            async with seeded_capability_case(
                store, scenario, user_id=user_id
            ) as fixture:
                observed["workbook_id"] = fixture.workbook_id
                turn = fixture.case.turns[0]
                response = await orchestrator.process(
                    [KlaudiaMessage(role="user", content=turn.user)],
                    None,
                    user_id,
                    spreadsheet_id=fixture.workbook_id,
                )
                observed["response"] = response.model_dump()
                observed["actual_state"] = await fixture.observe_state()
                assert response.runtime == "main"
                assert response.run_status == "answered"
                assert observed["actual_state"] == turn.expect.ledger_state
                if scenario == "append":
                    assert len(response.operation_receipts) == 1
                    assert response.operation_receipts[0]["status"] == "committed"
                    assert len(response.operation_references) == 1
                else:
                    assert "calculate" in response.tools_used
                    assert "Amount" in response.message.content
                    assert int(turn.expect.contains_amount[0]) in extract_claims(
                        response.message.content
                    )
        observed["status"] = "passed"
    except BaseException as exc:
        observed["status"] = "failed"
        observed["error_type"] = type(exc).__name__
        raise
