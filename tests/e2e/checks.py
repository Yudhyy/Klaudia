"""Layer-agnostic evaluation of a turn against its `Expect` block.

Both the in-process pytest layer and the HTTP runner build a `ResponseView` and
call `evaluate()`. This keeps a single source of truth for what "pass" means and
guarantees the two layers score identically.

Content checks are deliberately tolerant: case-insensitive substrings and
digit-normalized amounts. We are measuring agent behavior, not exact wording.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from tests.e2e.schema import Expect
from tests.e2e.capability_checks import check_capabilities

# ── Semantic helpers (kept aligned with the agent's actual rejection/clarify
#    vocabulary in app/services/guardrails/prompts.py and the worker prompts) ──

_REJECTION_MARKERS = (
    "sorry",
    "cannot",
    "outside",
    "not within",
    "nfa",
    "not financial advice",
    "maaf",
    "tidak bisa",
    "tidak dapat",
    "di luar",
    "bukan tugas",
)
_CLARIFY_MARKERS = (
    "?",
    "could you",
    "please",
    "which sheet",
    "clarify",
    "not found",
    "bisakah",
    "mohon",
    "jelaskan",
    "maksud",
    "klarifikasi",
    "tidak ditemukan",
    "tidak ada sheet",
    "berapa ribu",
    "berapa juta",
    "konfirmasi",
)
# Phrases that would indicate a silent destructive write actually happened.
_SILENT_WRITE_MARKERS = (
    "[write_done]",
    "semua data dihapus",
    "all data deleted",
    "seluruh isi dihapus",
)


def digits(text: str) -> str:
    """Strip everything but digits. '2.163.500' / 'Rp 2,163,500' → '2163500'."""
    return re.sub(r"[^\d]", "", text)


def looks_rejected(text: str) -> bool:
    tl = text.lower()
    return any(m in tl for m in _REJECTION_MARKERS)


def looks_clarifying(text: str) -> bool:
    tl = text.lower()
    return any(m in tl for m in _CLARIFY_MARKERS)


def has_silent_write(text: str) -> bool:
    tl = text.lower()
    return any(m in tl for m in _SILENT_WRITE_MARKERS)


@dataclass
class ResponseView:
    """Normalized result of one turn, regardless of transport.

    mcp_calls is populated only by the in-process layer (HTTP cannot see it).
    """

    content: str
    tools_used: list[str]
    latency_ms: int
    session_id: int | None = None
    mcp_calls: list[tuple[str, dict]] = field(default_factory=list)
    # Aggregated extraction cache result over the turn's attachments. None means
    # no extraction ran this turn (no attachment). Populated by ExtractionSpy.
    cache_hits: int | None = None
    cache_misses: int | None = None
    # False for the HTTP layer, which cannot observe cache hits/misses — cache
    # assertions are skipped there rather than failed.
    cache_observable: bool = True
    # Irreversible operations the destructive guard parked for user approval.
    pending_approvals: list[dict] = field(default_factory=list)
    error: str | None = None
    runtime: str = "main"
    unsupported: str | None = None
    capabilities_attempted: list[str] = field(default_factory=list)
    calculations: list[dict] = field(default_factory=list)
    append_attempts: list[dict] = field(default_factory=list)
    append_attempts_omitted: int = 0
    append_attempts_observable: bool = False
    operation_receipts: list[dict] = field(default_factory=list)
    operation_references: list[str] = field(default_factory=list)
    ledger_state: dict | None = None
    model_steps: int | None = None
    loaded_skills: dict[str, str] = field(default_factory=dict)

    @property
    def mcp_tool_names(self) -> list[str]:
        return [name for name, _ in self.mcp_calls]

    @property
    def mcp_args_blob(self) -> str:
        try:
            return json.dumps(
                [{"tool": n, "args": a} for n, a in self.mcp_calls],
                ensure_ascii=False,
                default=str,
            ).lower()
        except Exception:
            return str(self.mcp_calls).lower()


@dataclass
class CheckResult:
    passed: bool
    reasons: list[str]  # human-readable failure reasons (empty if pass)
    detail: dict  # per-check booleans for the report
    latency_warn: bool = False


def _route_ok(expect: Expect, view: ResponseView, reasons: list[str]) -> bool:
    used = set(view.tools_used)
    ok = True

    targets = expect.route_any_of or ([expect.route] if expect.route != "any" else [])
    if targets:
        matched = False
        for t in targets:
            if t == "none" and not used:
                matched = True
            elif t in used:
                matched = True
        if not matched:
            ok = False
            reasons.append(
                f"routing: expected one of {targets}, got tools_used={sorted(used)}"
            )

    for forbidden in expect.forbid_agents:
        if forbidden in used:
            ok = False
            reasons.append(f"routing: {forbidden} must NOT run, but it did")
    return ok


def _content_ok(
    expect: Expect, view: ResponseView, reasons: list[str]
) -> tuple[bool, dict]:
    text = view.content or ""
    tl = text.lower()
    detail: dict = {}
    ok = True

    if expect.answer_lines:
        observed_lines = {line.strip() for line in text.splitlines()}
        missing = [line for line in expect.answer_lines if line not in observed_lines]
        detail["answer_lines"] = not missing
        if missing:
            ok = False
            reasons.append(f"answer_lines: missing exact labelled lines {missing}")

    if expect.content_any:
        hit = any(k.lower() in tl for k in expect.content_any)
        detail["content_any"] = hit
        if not hit:
            ok = False
            reasons.append(f"content_any: none of {expect.content_any} present")

    if expect.content_all:
        missing = [k for k in expect.content_all if k.lower() not in tl]
        detail["content_all"] = not missing
        if missing:
            ok = False
            reasons.append(f"content_all: missing {missing}")

    if expect.content_none:
        present = [k for k in expect.content_none if k.lower() in tl]
        detail["content_none"] = not present
        if present:
            ok = False
            reasons.append(f"content_none: forbidden {present} present")

    if expect.contains_amount:
        d = digits(text)
        missing = [a for a in expect.contains_amount if digits(a) not in d]
        detail["contains_amount"] = not missing
        if missing:
            ok = False
            reasons.append(f"contains_amount: {missing} not found in {d!r}")

    if expect.excludes_amount:
        d = digits(text)
        present = [a for a in expect.excludes_amount if digits(a) in d]
        detail["excludes_amount"] = not present
        if present:
            ok = False
            reasons.append(f"excludes_amount: forbidden {present} present in {d!r}")

    if expect.is_rejection is not None:
        got = looks_rejected(text)
        detail["is_rejection"] = got == expect.is_rejection
        if got != expect.is_rejection:
            ok = False
            reasons.append(f"is_rejection: expected {expect.is_rejection}, got {got}")

    if expect.pending_approvals_min is not None:
        got = len(view.pending_approvals)
        approvals_ok = got >= expect.pending_approvals_min
        detail["pending_approvals_min"] = approvals_ok
        if not approvals_ok:
            ok = False
            reasons.append(
                f"pending_approvals_min: expected >= "
                f"{expect.pending_approvals_min}, got {got}"
            )

    if expect.is_clarification is not None:
        got = looks_clarifying(text) and not has_silent_write(text)
        detail["is_clarification"] = got == expect.is_clarification
        if got != expect.is_clarification:
            ok = False
            reasons.append(
                f"is_clarification: expected {expect.is_clarification}, got {got}"
            )

    return ok, detail


def _mcp_ok(
    expect: Expect, view: ResponseView, reasons: list[str]
) -> tuple[bool, dict]:
    """Granular tool assertions — only meaningful when the spy captured calls.

    If no spy data is present (HTTP layer) these checks are skipped, not failed.
    """
    detail: dict = {}
    if not (
        expect.mcp_tools_any
        or expect.mcp_tools_all
        or expect.mcp_tools_none
        or expect.mcp_args_contains
    ):
        return True, detail
    if not view.mcp_calls and (expect.mcp_tools_any or expect.mcp_tools_all):
        # No spy data available (e.g. HTTP layer): skip rather than fail.
        detail["mcp_skipped"] = True
        return True, detail

    names = set(view.mcp_tool_names)
    ok = True

    if expect.mcp_tools_any:
        hit = bool(names & set(expect.mcp_tools_any))
        detail["mcp_tools_any"] = hit
        if not hit:
            ok = False
            reasons.append(
                f"mcp_tools_any: expected one of {expect.mcp_tools_any}, "
                f"called {sorted(names)}"
            )
    if expect.mcp_tools_all:
        missing = [t for t in expect.mcp_tools_all if t not in names]
        detail["mcp_tools_all"] = not missing
        if missing:
            ok = False
            reasons.append(f"mcp_tools_all: missing {missing}")
    if expect.mcp_tools_none:
        present = [t for t in expect.mcp_tools_none if t in names]
        detail["mcp_tools_none"] = not present
        if present:
            ok = False
            reasons.append(f"mcp_tools_none: forbidden {present} called")
    if expect.mcp_args_contains:
        blob = view.mcp_args_blob
        missing = [s for s in expect.mcp_args_contains if s.lower() not in blob]
        detail["mcp_args_contains"] = not missing
        if missing:
            ok = False
            reasons.append(f"mcp_args_contains: {missing} not in any tool args")

    return ok, detail


def _cache_ok(
    expect: Expect, view: ResponseView, reasons: list[str]
) -> tuple[bool, dict]:
    """Assert extraction cache hits/misses (in-process only).

    Skipped when the dataset asserts nothing. If a cache count IS expected but no
    extraction ran (view.cache_hits is None), that is a failure — the attachment
    didn't go through KIE.
    """
    detail: dict = {}
    if expect.cache_hits is None and expect.cache_misses is None:
        return True, detail
    if not view.cache_observable:
        # HTTP layer: cache not visible — skip rather than fail.
        detail["cache_skipped"] = True
        return True, detail
    ok = True
    if expect.cache_hits is not None:
        got = view.cache_hits
        detail["cache_hits"] = got == expect.cache_hits
        if got != expect.cache_hits:
            ok = False
            reasons.append(f"cache_hits: expected {expect.cache_hits}, got {got}")
    if expect.cache_misses is not None:
        got = view.cache_misses
        detail["cache_misses"] = got == expect.cache_misses
        if got != expect.cache_misses:
            ok = False
            reasons.append(f"cache_misses: expected {expect.cache_misses}, got {got}")
    return ok, detail


def evaluate(expect: Expect, view: ResponseView) -> CheckResult:
    """Score one turn. passed = routing AND content AND granular-tool checks."""
    reasons: list[str] = []
    detail: dict = {}

    if view.unsupported:
        return CheckResult(
            False, [f"unsupported: {view.unsupported}"], {"unsupported": True}
        )
    if view.error:
        return CheckResult(
            passed=False, reasons=[f"transport/error: {view.error}"], detail={}
        )

    route_ok = _route_ok(expect, view, reasons)
    content_ok, c_detail = _content_ok(expect, view, reasons)
    mcp_ok, m_detail = _mcp_ok(expect, view, reasons)
    cache_ok, cache_detail = _cache_ok(expect, view, reasons)
    capability_detail = check_capabilities(expect, view)
    reasons.extend(
        f"{name}: required evidence missing or incorrect"
        for name, passed in capability_detail.items()
        if not passed
    )
    detail.update(capability_detail)
    detail.update(c_detail)
    detail.update(m_detail)
    detail.update(cache_detail)

    latency_warn = False
    if expect.latency_ms_max is not None and view.latency_ms > expect.latency_ms_max:
        latency_warn = True
        msg = f"latency {view.latency_ms}ms > budget {expect.latency_ms_max}ms"
        if expect.latency_hard:
            reasons.append(msg)
        # soft budgets are reported via latency_warn, not as a failure reason

    passed = (
        route_ok
        and content_ok
        and mcp_ok
        and cache_ok
        and all(capability_detail.values())
    )
    if latency_warn and expect.latency_hard:
        passed = False

    return CheckResult(
        passed=passed, reasons=reasons, detail=detail, latency_warn=latency_warn
    )
