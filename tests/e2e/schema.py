"""Dataset schema for the Klaudia whitebox E2E suite.

A *case* is a real-world user scenario expressed as one or more conversational
*turns* that share a session. Each turn carries an `expect` block describing what
the system should do (routing, content, granular MCP tool calls, latency).

The schema is intentionally declarative so the dataset stays easy to inspect and
maintain in YAML — no Python edits required to add or tweak a scenario.

Two assertion depths are supported because the public HTTP response only exposes
sub-agent names (`sql_agent`, `data_entry_team`), not the granular MCP tools:

  * Layer-agnostic (HTTP + in-process): routing, content, latency.
  * In-process only (MCP spy): `mcp_tools_*` / `mcp_args_contains`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Capability = Literal[
    "read_records",
    "discover_resources",
    "inspect_resource",
    "calculate",
    "append_records",
    "search_documents",
    "inspect_typed_workbook",
    "edit_typed_cells",
]


class FormulaReceiptExpectation(BaseModel):
    """Exact calculation evidence for one distinct committed typed operation."""

    model_config = ConfigDict(extra="forbid", strict=True)
    workbook_id: str = Field(min_length=1)
    sheet_id: int = Field(gt=0)
    calculation_status: Literal["not_required", "current", "failed"]
    calculation: dict[str, JsonValue]


class MetricExpectation(BaseModel):
    """Exact labelled calculation evidence, distinct from final-answer grading."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    table_id: str
    column: str
    operation: Literal["sum", "count"]
    value: Decimal
    group: dict[str, JsonValue] = Field(default_factory=dict)


# Sub-agent node names that can appear in KlaudiaResponse.tools_used.
ROUTE_AGENTS = ("data_entry_team", "sql_agent")
# Accepted values for expect.route.
ROUTE_VALUES = ("none", "data_entry_team", "sql_agent", "any")


class Expect(BaseModel):
    """What the system should do for a single turn.

    All list fields default empty = "no assertion of this kind". Keep content
    checks tolerant (substring / digit-normalized / at-least-one) so minor LLM
    phrasing differences do not create false negatives — we are measuring
    behavior, not exact strings.
    """

    model_config = ConfigDict(extra="forbid")

    capabilities_all: list[Capability] = Field(default_factory=list)
    answer_lines: list[str] = Field(default_factory=list)
    metric_evidence: list[MetricExpectation] = Field(default_factory=list)
    formula_receipts: list[FormulaReceiptExpectation] | None = None
    committed_operations_min: int | None = Field(default=None, ge=1)
    ledger_state: dict[str, list[list[JsonValue]]] = Field(default_factory=dict)

    # ── Routing (sub-agent level, assertable over HTTP) ──────────────────────
    # "none"           → tools_used must be empty (FINISH / answered from context)
    # "data_entry_team"→ data_entry_team present in tools_used
    # "sql_agent"      → sql_agent present in tools_used
    # "any"            → no routing assertion
    route: str = "any"
    # Acceptable alternatives when more than one route is legitimate
    # (e.g. answer-from-context FINISH OR sql_agent). Overrides `route` when set.
    route_any_of: list[str] = Field(default_factory=list)
    # Agents that must NOT appear (e.g. sql_agent on a pure GSheets question).
    forbid_agents: list[str] = Field(default_factory=list)

    # ── Content (assertable everywhere) ──────────────────────────────────────
    content_any: list[str] = Field(default_factory=list)  # ≥1 must appear (ci)
    content_all: list[str] = Field(default_factory=list)  # all must appear (ci)
    content_none: list[str] = Field(default_factory=list)  # none may appear (ci)
    contains_amount: list[str] = Field(default_factory=list)  # digit-normalized
    # Amounts that must NOT appear (digit-normalized). The negative half of
    # contains_amount: it grades a figure the agent had no legitimate way to
    # reach — another tenant's total, or a cross-spreadsheet sum the request's
    # scope cannot see — so stating it is a leak or a confabulation.
    excludes_amount: list[str] = Field(default_factory=list)
    is_rejection: bool | None = None  # guardrail-style refusal
    is_clarification: bool | None = None  # HITL clarifying question
    # Minimum destructive operations the guard must park for approval.
    pending_approvals_min: int | None = None

    # ── Granular MCP tool calls (in-process spy only) ────────────────────────
    mcp_tools_any: list[str] = Field(default_factory=list)  # ≥1 of these called
    mcp_tools_all: list[str] = Field(default_factory=list)  # all of these called
    mcp_tools_none: list[str] = Field(default_factory=list)  # none called
    # Substrings expected somewhere in the JSON-serialized args of ANY call.
    mcp_args_contains: list[str] = Field(default_factory=list)

    # ── KIE / extraction cache (in-process only) ─────────────────────────────
    # Asserted against the ExtractionAgent result, aggregated over the turn's
    # attachments. cache_hits = pages served from cache; cache_misses = fresh
    # extractions. This is the correct signal for "did it hit cache?" — NOT
    # latency. A fresh image (never ingested) => cache_misses=1, cache_hits=0;
    # a previously-ingested file => cache_hits=1, cache_misses=0.
    cache_hits: int | None = None
    cache_misses: int | None = None

    # ── Latency ──────────────────────────────────────────────────────────────
    # Soft budget: recorded always; a breach is reported but only hard-fails the
    # turn when `latency_hard` is true.
    latency_ms_max: int | None = None
    latency_hard: bool = False

    @model_validator(mode="after")
    def _check_route(self) -> "Expect":
        if self.route not in ROUTE_VALUES:
            raise ValueError(
                f"expect.route={self.route!r} invalid; use one of {ROUTE_VALUES}"
            )
        for r in self.route_any_of:
            if r not in ROUTE_VALUES:
                raise ValueError(
                    f"route_any_of has {r!r}; use values from {ROUTE_VALUES}"
                )
        for a in self.forbid_agents:
            if a not in ROUTE_AGENTS:
                raise ValueError(
                    f"forbid_agents has {a!r}; use names from {ROUTE_AGENTS}"
                )
        return self


class Turn(BaseModel):
    """One user message and its expectation."""

    user: str
    # Single attachment path relative to the repo root, e.g.
    # "sample-data/receipt/002-receipt.png". Convenience for the common case.
    attachment: str | None = None
    # Multiple attachments (for attachment-shape rejection cases: >1 PDF, mixed,
    # >5 images). Merged with `attachment` by `all_attachments()`.
    attachments: list[str] = Field(default_factory=list)
    note: str | None = None
    # Start a fresh session for this turn (same user unless as_user is set). The
    # new session has an empty conversation window, so anything the turn recalls
    # must come from long-term memory / continuity state, not history. The engine
    # flushes pending background memory writes before starting the new session.
    new_session: bool = False
    # Run this turn as a different user id (default: the harness TEST_USER_ID).
    # Enables cross-user isolation cases: a user must not recall another's memory.
    as_user: int | None = None
    # Logical name of the spreadsheet this turn is bound to, for users who own
    # more than one. The runner maps the name to the real spreadsheet id and
    # binds it exactly as /v1/chat does; None uses the user's default. A request
    # sees only the bound spreadsheet (tenancy scope), which is what the
    # multi-spreadsheet isolation cases grade.
    spreadsheet: str | None = None
    expect: Expect = Field(default_factory=Expect)

    def all_attachments(self) -> list[str]:
        out: list[str] = []
        if self.attachment:
            out.append(self.attachment)
        out.extend(self.attachments)
        return out


class Case(BaseModel):
    """A full scenario. Turns share one session (created on the first turn)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    title: str
    tags: list[str] = Field(default_factory=list)
    resource_scope: Literal[
        "bound_workbook", "owned_workbooks", "single_workbook_fixture"
    ] = "bound_workbook"
    # True when the case mutates the real Google Sheet (write/sheet ops). Lets
    # callers deselect destructive cases without running them.
    mutating: bool = False
    turns: list[Turn]
    # Best-effort prompts run after the case (finally) to reverse mutations.
    # Each is a plain user message sent in the same session.
    cleanup: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _non_empty(self) -> "Case":
        if not self.turns:
            raise ValueError(f"case {self.id} has no turns")
        return self
