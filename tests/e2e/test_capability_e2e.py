"""Opt-in live candidate checks; historical benchmark reports remain separate."""

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from tests.e2e.capability_cases import CAPABILITY_SEED, seeded_capability_case
from tests.e2e.conftest import requires_live
from tests.e2e.engine_inprocess import run_case_inprocess
from tests.e2e.report import Report
from tests.e2e.sut import MainAgentSUT
from tests.e2e.sandbox import sandbox_enabled

pytestmark = [
    requires_live,
    pytest.mark.skipif(
        os.environ.get("E2E_MAIN_AGENT_BENCH") != "1",
        reason="Set E2E_MAIN_AGENT_BENCH=1 for the candidate capability suite",
    ),
]


@pytest.fixture(scope="module", autouse=True)
def require_candidate_sandbox():
    """Reject development targets before constructing service fixtures."""
    if os.environ.get("E2E_SANDBOX_ACTIVE") != "1" or not sandbox_enabled():
        raise RuntimeError(
            "Candidate capability benchmark requires the isolated sandbox"
        )


@pytest.fixture(scope="module")
def capability_report():
    """Save a distinct report even when a candidate misses its expectations.

    Yields:
        Runtime-labelled accumulator with model, seed and source revision.
    """
    from config.settings import get_settings

    settings = get_settings()
    report = Report(
        metadata={
            "runtime": "main",
            "model": settings.llm_model,
            "provider": settings.model_provider,
            "temperature": settings.llm_temperature,
            "disable_thinking": settings.llm_disable_thinking,
            "fixture_seed": CAPABILITY_SEED,
            "fixture_version": 1,
            "git_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "scope": "owned_workbooks",
            "suite": "candidate_capabilities",
        }
    )
    try:
        yield report
    finally:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = (
            Path(__file__).with_name("outputs")
            / f"capability-main-{stamp}-{uuid4().hex[:8]}.json"
        )
        report.write_json(path)
        print(f"Candidate capability report: {path}")


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.e2e
@pytest.mark.parametrize(
    "scenario", ["sum_1000", pytest.param("append", marks=pytest.mark.mutating)]
)
async def test_main_agent_capability(scenario, container, capability_report):
    """Grade live model behavior using the same runner as scripted integration tests."""
    from app.services.core.operations import OperationService
    from klaudia.core.agent.agent import MainAgent
    from klaudia.core.agent.llm import build_chat_llm

    if container.ledger_store is None or container.catalogue is None:
        raise RuntimeError("Candidate capability benchmark requires the ledger backend")
    settings = container.settings
    endpoint, api_key = settings.active_openai_endpoint()
    model = build_chat_llm(
        model=settings.llm_model,
        provider=settings.model_provider,
        temperature=settings.llm_temperature,
        use_vertexai=settings.google_genai_use_vertexai,
        llm_api_key=settings.llm_api_key,
        google_cloud_project=settings.google_cloud_project,
        google_cloud_location=settings.google_cloud_location,
        openai_base_url=endpoint,
        openai_api_key=api_key,
        thinking_level=settings.llm_thinking_level,
        disable_thinking=settings.llm_disable_thinking,
    )
    agent = MainAgent(
        model, container.catalogue, operations=OperationService(container.ledger_store)
    )
    async with seeded_capability_case(container.ledger_store, scenario) as fixture:
        records = await run_case_inprocess(
            None,
            None,
            None,
            fixture.case,
            spreadsheet_ids={"active": fixture.workbook_id},
            sut=MainAgentSUT(agent),
            observe_state=fixture.observe_state,
        )
        for record in records:
            capability_report.add(record)
        assert all(record.result.passed for record in records), [
            record.result.reasons for record in records
        ]
