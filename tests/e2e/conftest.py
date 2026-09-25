"""Pytest fixtures for the in-process E2E layer.

A single real container (LLM + MCP stdio servers + DB + object store) is built
once per module and shared across all cases. Requires live credentials and
running infra — the suite skips cleanly when they are absent.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# Load model credentials before resolving the isolated sandbox settings.
load_dotenv()

# Repoint every datastore at the sandbox BEFORE anything reads settings: the
# settings object is cached on first build, and the MCP servers are spawned with
# a copy of this environment, so the rebind has to happen at import time to
# reach them. Raises rather than falling back if a target still resolves to the
# development store. Opt out with E2E_SANDBOX=0.
from tests.e2e.sandbox import bind_sandbox, describe  # noqa: E402

bind_sandbox()

# Pin "now" to a fixed June instant for the whole E2E run so the system prompt's
# CURRENT DATE/TIME — and therefore any "use today's date" write — stays inside
# June, matching the June-based docs/TABLE.md fixtures. Real time is used in
# production (the app never sets this). setdefault lets a developer override.
os.environ.setdefault("E2E_FREEZE_NOW", "2026-06-30T19:22:00")


def pytest_configure(config):
    # Name the stores in the run header so a results table can never be read as
    # if it came from a different environment than it did.
    print(f"\n{describe()}")
    config.addinivalue_line(
        "markers",
        "mutating: case mutates the sandbox ledger (deselect with -m 'not mutating')",
    )
    config.addinivalue_line("markers", "e2e: Klaudia whitebox end-to-end case")


# ── Skip guard: the in-process layer needs real LLM credentials ──────────────
# Any one of: Gemini Developer key, Vertex (use_vertexai + project), or DeepSeek.
_has_llm = (
    bool(os.environ.get("LLM_API_KEY"))
    or bool(os.environ.get("DEEPSEEK_API_KEY"))
    or (
        os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true"
        and bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))
    )
)

requires_live = pytest.mark.skipif(
    not _has_llm,
    reason="E2E in-process layer needs LLM credentials (LLM_API_KEY / DEEPSEEK_API_KEY / Vertex)",
)


# loop_scope="module" is REQUIRED: the container's MCP stdio sessions and their
# background tasks are bound to the loop that creates the fixture. The tests must
# run on that SAME loop or every MCP call (e.g. tool_list_sheets in
# the sheet-read API) awaits across event loops
# and deadlocks. Tests therefore use @pytest.mark.asyncio(loop_scope="module").
@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def container():
    """Build the real KlaudiaContainer once for the module."""
    from app.services.core.container import KlaudiaContainer
    from config.settings import get_settings

    c = await KlaudiaContainer.create(get_settings())
    yield c
    await c.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def orchestrator(container):
    from app.services.core.orchestrator import KlaudiaOrchestrator

    return KlaudiaOrchestrator(container)


@pytest.fixture(scope="module")
def spy(container):
    from tests.e2e.spy import MCPSpy

    return MCPSpy([container.mcp_ledger])


@pytest.fixture(scope="module")
def extraction_spy(container):
    from tests.e2e.spy import ExtractionSpy

    return ExtractionSpy(container.extraction_agent)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def sheet_guard(container):
    """Snapshot guarded sheets before the suite, restore them after.

    Snapshot runs before any case mutates the sheet, so it captures the pristine
    TABLE.md state. Per-mutating-case restore is driven from run_case_inprocess;
    the teardown restore here leaves the sheet clean at suite end.

    With the ledger backend, the guard is scoped to the test user's default
    spreadsheet — resolved through the SAME SpreadsheetService path the
    orchestrator uses per request — and the full TABLE.md baseline is seeded
    into it, because a per-user spreadsheet starts empty (unlike the gsheets
    fixture sheet, which is assumed to match TABLE.md already).
    """
    from tests.e2e.engine_inprocess import TEST_USER_ID
    from tests.e2e.sheet_guard import SheetGuard

    scope = None
    if container.spreadsheets is not None:
        scope = await container.spreadsheets.resolve_scope(TEST_USER_ID)

    guard = SheetGuard(container.mcp_ledger, spreadsheet_id=scope)
    await guard.snapshot()
    if scope is not None:
        await guard.seed()
    yield guard
    await guard.restore()
