"""Run a dataset case through an explicit sandbox runtime adapter.

Application turns use orchestrator.process(). Turns in a case share a session
(created on the first turn, reused after). The MCP spy captures granular tool
calls per turn so `mcp_tools_*` assertions can be evaluated — something the HTTP
layer cannot see. Cleanup prompts run best-effort in a finally block.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from collections.abc import Awaitable, Callable

from app.models.attachment import FileAttachment
from app.models.chat import KlaudiaMessage
from klaudia.core.agent.agent import AgentRunCancelled
from tests.e2e.checks import ResponseView, evaluate
from tests.e2e.loader import attachment_bytes
from tests.e2e.report import TurnRecord
from tests.e2e.schema import Case, Turn
from tests.e2e.sut import ApplicationSUT, SystemUnderTest, TurnRequest

logger = logging.getLogger(__name__)

TEST_USER_ID = 1
TEST_USER_NAME = "QARunner"

# A turn that exceeds this is recorded as a failure instead of blocking the suite.
# Real agent turns run ~30-55s (cold first call ~55s; heavier multi-tool/KIE turns
# more), so the budget is generous to avoid false timeouts. Override via
# E2E_TURN_TIMEOUT for faster local iteration.
TURN_TIMEOUT_S = float(os.environ.get("E2E_TURN_TIMEOUT", "180"))
CLEANUP_TIMEOUT_S = float(os.environ.get("E2E_CLEANUP_TIMEOUT", "120"))


def _build_message(turn: Turn) -> list[KlaudiaMessage]:
    attachments = None
    paths = turn.all_attachments()
    if paths:
        attachments = []
        for rel in paths:
            name, ct, data = attachment_bytes(rel)
            attachments.append(
                FileAttachment(filename=name, content_type=ct, data=data)
            )
    return [KlaudiaMessage(role="user", content=turn.user, attachments=attachments)]


async def _prepurge_cache_miss(container, case: Case) -> None:
    """For a `cache_miss`-tagged case, delete its attachments' dedup/extraction
    footprint BEFORE running so the ingest is a genuine MISS on every run —
    idempotent and independent of what a prior run left behind."""
    if container is None or "cache_miss" not in (case.tags or []):
        return
    from tests.e2e.kie_reset import purge_kie_file

    for turn in case.turns:
        for rel in turn.all_attachments():
            file_name = Path(rel).name
            try:
                await purge_kie_file(container, TEST_USER_ID, file_name)
            except Exception:
                logger.warning("KIE pre-purge failed for %s (%s)", case.id, file_name)


async def _ensure_users(container, case: Case) -> None:
    """Create synthetic `as_user` rows so the session->user FK inserts succeed.

    Cross-user cases run turns as arbitrary user ids that were never
    registered. The app DB enforces a session->user foreign key, so those users
    must exist first.
    """
    if container is None:
        return
    user_ids = {t.as_user for t in case.turns if t.as_user is not None}
    for uid in user_ids:
        try:
            await container.db_client.execute(
                'INSERT INTO "user" (user_id, username, email, password_hash) '
                "VALUES ($1, $2, $3, $4) ON CONFLICT (user_id) DO NOTHING",
                (uid, f"e2e-user-{uid}", f"e2e-{uid}@local", "not-a-real-hash"),
            )
        except Exception:
            logger.warning("could not ensure e2e user %s (%s)", uid, case.id)


def _resolve_spreadsheet(
    turn: Turn, spreadsheet_ids: dict[str, str] | None, case: Case
) -> str | None:
    """Real spreadsheet id for a turn's logical `spreadsheet` name.

    Raises rather than falling back to the default: a typo would otherwise run
    the turn against the wrong workspace and score a leak case as a pass.
    """
    if turn.spreadsheet is None:
        return None
    if not spreadsheet_ids or turn.spreadsheet not in spreadsheet_ids:
        raise KeyError(
            f"case {case.id}: spreadsheet {turn.spreadsheet!r} is not seeded "
            f"(known: {sorted(spreadsheet_ids or {})})"
        )
    return spreadsheet_ids[turn.spreadsheet]


async def run_case_inprocess(
    orchestrator,
    spy,
    extraction_spy,
    case: Case,
    sheet_guard=None,
    container=None,
    spreadsheet_ids: dict[str, str] | None = None,
    *,
    sut: SystemUnderTest | None = None,
    observe_state: Callable[[], Awaitable[dict]] | None = None,
) -> list[TurnRecord]:
    """Execute every turn of `case`, returning a TurnRecord per turn.

    spy: MCPSpy recording granular tool calls for the turn.
    extraction_spy: ExtractionSpy recording KIE cache hits/misses for the turn.
    sheet_guard: optional SheetGuard; for mutating cases its deterministic
    restore() runs in the finally block so write drift never leaks into later
    read/routing cases.
    spreadsheet_ids: logical name -> real spreadsheet id, for cases whose turns
    set `spreadsheet`. An unmapped name is a dataset error and raises.
    sut: Explicit adapter; omitted selects the application orchestrator.
    observe_state: Optional fixture database probe, never passed to the model.
    Unsupported contracts return failed records without executing the case.
    Behavioral mismatches do NOT raise — they are recorded in the TurnRecord.
    Runtime and state-observation failures retain a failed report record.
    """
    records: list[TurnRecord] = []
    runtime = (
        sut if sut is not None else ApplicationSUT(orchestrator, (spy, extraction_spy))
    )
    unsupported = runtime.unsupported(case)
    if unsupported:
        for index, turn in enumerate(case.turns):
            view = ResponseView(
                content="",
                tools_used=[],
                latency_ms=0,
                runtime=runtime.runtime,
                unsupported=unsupported,
            )
            records.append(
                TurnRecord(
                    case.id,
                    case.category,
                    case.title,
                    index,
                    turn.user,
                    view,
                    evaluate(turn.expect, view),
                )
            )
        return records
    session_id: int | None = None
    current_user = TEST_USER_ID

    await _prepurge_cache_miss(container, case)
    await _ensure_users(container, case)

    try:
        for idx, turn in enumerate(case.turns):
            turn_user = turn.as_user if turn.as_user is not None else TEST_USER_ID
            if turn.new_session or turn_user != current_user:
                session_id = None
            current_user = turn_user
            messages = _build_message(turn)
            scope = _resolve_spreadsheet(turn, spreadsheet_ids, case)
            try:
                view = await asyncio.wait_for(
                    runtime.run(
                        TurnRequest(
                            messages, turn_user, session_id, scope, TEST_USER_NAME
                        )
                    ),
                    timeout=TURN_TIMEOUT_S,
                )
                session_id = view.session_id
            except asyncio.TimeoutError as exc:
                logger.error(
                    "case %s turn %d timed out after %.0fs",
                    case.id,
                    idx,
                    TURN_TIMEOUT_S,
                )
                view = ResponseView(
                    content="",
                    tools_used=[],
                    latency_ms=int(TURN_TIMEOUT_S * 1000),
                    session_id=session_id,
                    error=f"timeout after {TURN_TIMEOUT_S:.0f}s (agent hung or looping)",
                    runtime=runtime.runtime,
                )
                if isinstance(exc.__cause__, AgentRunCancelled):
                    outcome = exc.__cause__.outcome
                    view.operation_references = list(outcome.operation_references)
                    view.operation_receipts = list(outcome.operation_receipts)
                    view.model_steps = outcome.model_steps
                    view.loaded_skills = dict(outcome.loaded_skills)
            except Exception as exc:  # transport / pipeline failure
                logger.exception("case %s turn %d crashed", case.id, idx)
                view = ResponseView(
                    content="",
                    tools_used=[],
                    latency_ms=0,
                    session_id=session_id,
                    error=f"{type(exc).__name__}: {exc}",
                    runtime=runtime.runtime,
                )

            if observe_state is not None:
                try:
                    observed = await asyncio.wait_for(
                        observe_state(), timeout=TURN_TIMEOUT_S
                    )
                    json.dumps(observed, allow_nan=False)
                    view.ledger_state = observed
                except Exception as exc:
                    observation_error = (
                        f"state observation: {type(exc).__name__}: {exc}"
                    )
                    view.error = (
                        f"{view.error}; {observation_error}"
                        if view.error
                        else observation_error
                    )
            result = evaluate(turn.expect, view)
            records.append(
                TurnRecord(
                    case_id=case.id,
                    category=case.category,
                    title=case.title,
                    turn_index=idx,
                    user=turn.user,
                    view=view,
                    result=result,
                    layer="in_process",
                )
            )
    finally:
        # Best-effort cleanup in the same session — never fails the case.
        for prompt in case.cleanup:
            try:
                await asyncio.wait_for(
                    orchestrator.process(
                        messages=[KlaudiaMessage(role="user", content=prompt)],
                        session_id=session_id,
                        user_id=current_user,
                        user_name=TEST_USER_NAME,
                    ),
                    timeout=CLEANUP_TIMEOUT_S,
                )
            except Exception:
                logger.warning("cleanup failed/timed out for %s: %r", case.id, prompt)

        # Deterministic restore of guarded sheets after a mutating case — the
        # authoritative reset, independent of whether the LLM cleanup above
        # succeeded. Keeps later cases reading the pristine TABLE.md baseline.
        if sheet_guard is not None and case.mutating:
            try:
                await sheet_guard.restore()
            except Exception:
                logger.warning("sheet restore failed for %s", case.id)

    return records
