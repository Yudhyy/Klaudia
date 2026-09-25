"""Sandbox runtime adapters with explicit scope and observation contracts."""

from dataclasses import dataclass
import json
from time import perf_counter
from typing import Any, Protocol
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from app.models.chat import KlaudiaMessage
from klaudia.core.agent.agent import MainAgent, AgentExecutionError
from klaudia.core.agent.context import TaskContext
from tests.e2e.checks import ResponseView
from tests.e2e.schema import Case

_CAPABILITY_TOOLS = {
    "read_records": {"tool_get_sheet_data", "tool_get_multiple_sheet_data"},
    "discover_resources": {"search_resources", "tool_search_resources"},
    "inspect_resource": {"inspect_resource", "tool_inspect_resource"},
    "calculate": {"calculate", "tool_aggregate_sheet"},
    "inspect_typed_workbook": {"inspect_typed_workbook"},
    "edit_typed_cells": {"prepare_typed_edit"},
    "append_records": {
        "prepare_table_append",
        "tool_append_rows",
        "tool_append_rows_checked",
    },
}

MAX_APPEND_INPUT_BYTES = 65536
MAX_APPEND_ATTEMPTS = 24


def attempted_capabilities(names: list[str]) -> list[str]:
    """Map observed tool attempts without claiming their operations succeeded.

    Args:
        names: Granular tool names recorded by the runtime adapter.

    Returns:
        Stable capability names; worker routing names grant no capability evidence.
    """
    return sorted(
        capability
        for capability, tools in _CAPABILITY_TOOLS.items()
        if tools.intersection(names)
    )


@dataclass(frozen=True)
class TurnRequest:
    """Runner-supplied identity and message, excluding grading expectations."""

    messages: list[KlaudiaMessage]
    user_id: int
    session_id: int | None = None
    spreadsheet_id: str | None = None
    user_name: str = "QARunner"


class SystemUnderTest(Protocol):
    """A runtime declares unsupported contracts before any case executes."""

    runtime: str

    def unsupported(self, case: Case) -> str | None:
        """Declare unsupported contracts before execution.

        Args:
            case: Dataset scope and behavior requirements.

        Returns:
            An unsupported reason, or None when the contract can be attempted.
        """
        ...

    async def run(self, request: TurnRequest) -> ResponseView:
        """Execute one turn without receiving grading expectations.

        Args:
            request: Authenticated identity and user message.

        Returns:
            Runtime observations for the shared grader.
        """
        ...


class ApplicationSUT:
    """Preserve the existing orchestrator, extraction spies and bound scope."""

    runtime = "main"

    def __init__(self, orchestrator: Any, spies: tuple[Any, Any]) -> None:
        """Bind the existing chat runtime and its two observation boundaries.

        Args:
            orchestrator: Existing chat/session service.
            spies: MCP and extraction spies from the sandbox fixtures.
        """
        self._orchestrator = orchestrator
        self._spy, self._extraction_spy = spies

    def unsupported(self, case: Case) -> str | None:
        """Reject cases that require cross-workbook authorised discovery.

        Args:
            case: Dataset case and declared access contract.

        Returns:
            A reason when the fixture bound-workbook contract is insufficient.
        """
        return (
            "owned_workbooks cases require the explicit main-agent adapter"
            if case.resource_scope != "bound_workbook"
            else None
        )

    async def run(self, request: TurnRequest) -> ResponseView:
        """Invoke application chat while preserving its original evidence fields.

        Args:
            request: Server identity, session and bound workbook.

        Returns:
            Chat output and observed capability attempts, not inferred commits.
        """
        with (
            self._spy.capture() as calls,
            self._extraction_spy.capture() as extractions,
        ):
            response = await self._orchestrator.process(
                messages=request.messages,
                session_id=request.session_id,
                user_id=request.user_id,
                user_name=request.user_name,
                spreadsheet_id=request.spreadsheet_id,
            )
        return ResponseView(
            content=response.message.content,
            tools_used=list(response.tools_used),
            latency_ms=response.processing_time_ms,
            session_id=response.session_id,
            mcp_calls=list(calls),
            cache_hits=sum(item["cache_hits"] for item in extractions)
            if extractions
            else None,
            cache_misses=sum(item["cache_misses"] for item in extractions)
            if extractions
            else None,
            pending_approvals=list(response.pending_approvals),
            runtime=self.runtime,
            capabilities_attempted=attempted_capabilities([name for name, _ in calls]),
        )


class _FinancialToolObserver(BaseCallbackHandler):
    """Capture financial tool evidence without changing the agent or its prompt."""

    run_inline = True

    def __init__(self) -> None:
        """Create fresh callback state for one turn."""
        self._names: dict[UUID, str] = {}
        self.calculations: list[dict] = []
        self.append_attempts: list[dict] = []
        self.append_attempts_omitted = 0
        self._append_runs: dict[UUID, dict] = {}
        self._append_input_bytes = 0

    def on_tool_start(
        self,
        serialized: dict,
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Record the tool name for its result callback.

        Args:
            serialized: Tool metadata from LangChain.
            input_str: Tool input text, unused by the grader.
            run_id: Callback identity for this invocation.
            inputs: Structured model arguments before tool validation.
            kwargs: Additional callback context.
        """
        self._names[run_id] = serialized.get("name", "")
        if self._names[run_id] != "prepare_table_append":
            return
        if inputs is None or len(self.append_attempts) >= MAX_APPEND_ATTEMPTS:
            self.append_attempts_omitted += 1
            return
        try:
            encoded = json.dumps(inputs, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            self.append_attempts_omitted += 1
            return
        input_bytes = len(encoded.encode("utf-8"))
        if self._append_input_bytes + input_bytes > MAX_APPEND_INPUT_BYTES:
            self.append_attempts_omitted += 1
            return
        attempt = {"arguments": json.loads(encoded), "operation_ref": None}
        self.append_attempts.append(attempt)
        self._append_runs[run_id] = attempt
        self._append_input_bytes += input_bytes

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """Retain successful native calculation output at the callback boundary.

        Args:
            output: Tool-returned evidence, not final model text.
            run_id: Matching invocation identity.
            kwargs: Additional callback context.
        """
        name = self._names.pop(run_id, None)
        attempt = self._append_runs.pop(run_id, None)
        if attempt is not None and isinstance(output, dict):
            reference = output.get("operation_ref")
            if isinstance(reference, str) and len(reference) <= 128:
                attempt["operation_ref"] = reference
        if (
            name == "calculate"
            and isinstance(output, dict)
            and "source" in output
            and "groups" in output
        ):
            self.calculations.append(output)

    def on_tool_error(
        self, error: BaseException, *, run_id: UUID, **kwargs: Any
    ) -> None:
        """Retain attempted arguments without inventing a returned reference.

        Args:
            error: Tool failure; exception text is not stored in diagnostics.
            run_id: Failed invocation identity.
            kwargs: Additional callback context.
        """
        self._names.pop(run_id, None)
        self._append_runs.pop(run_id, None)


class MainAgentSUT:
    """Expose the optional agent's supported single-turn owned-workbook contract."""

    runtime = "main"

    def __init__(self, agent: MainAgent) -> None:
        """Use a caller-configured agent, including its model and optional writes.

        Args:
            agent: Alternative runtime supplied by the sandbox fixture.
        """
        self._agent = agent

    def unsupported(self, case: Case) -> str | None:
        """Reject contract changes that could make a historical case pass falsely.

        Args:
            case: Dataset requirements before execution.

        Returns:
            Unsupported scope, sessions, attachments or legacy assertion reason.
        """
        if case.resource_scope != "owned_workbooks":
            return "bound_workbook isolation is not the main agent's scope contract"
        if len(case.turns) != 1 or case.cleanup:
            return "multi-turn sessions and cleanup prompts are not supported"
        turn = case.turns[0]
        if turn.all_attachments():
            return "attachments are not integrated with the main agent"
        expected = turn.expect
        if expected.cache_hits is not None or expected.cache_misses is not None:
            return "extraction cache observations are not supported"
        if (
            expected.route != "any"
            or expected.route_any_of
            or expected.forbid_agents
            or expected.mcp_tools_any
            or expected.mcp_tools_all
            or expected.mcp_tools_none
            or expected.mcp_args_contains
        ):
            return "legacy routing/MCP assertions require explicit capability cases"
        return None

    async def run(self, request: TurnRequest) -> ResponseView:
        """Run the agent and retain observed evidence even on recoverable failure.

        Args:
            request: Authenticated identity and active workbook hint.

        Returns:
            Capability attempts, calculations, receipts and explicit failure status.

        Raises:
            ValueError: The caller supplies a session or unsupported message shape.
            Exception: Unexpected failures before operation evidence exists propagate.
        """
        if (
            request.session_id is not None
            or len(request.messages) != 1
            or request.messages[0].attachments
        ):
            raise ValueError(
                "Main agent adapter requires one text turn without session history"
            )
        observer = _FinancialToolObserver()
        started = perf_counter()
        error = None
        try:
            outcome = await self._agent.run(
                request.messages[0].content,
                TaskContext(
                    user_id=request.user_id, active_workbook_id=request.spreadsheet_id
                ),
                config={"callbacks": [observer]},
            )
        except AgentExecutionError as exc:
            outcome = exc.outcome
            error = type(exc.__cause__).__name__
        if outcome.status != "answered":
            error = error or outcome.status
        return ResponseView(
            content=outcome.content,
            tools_used=list(outcome.tools_called),
            latency_ms=round((perf_counter() - started) * 1000),
            runtime=self.runtime,
            error=error,
            capabilities_attempted=attempted_capabilities(list(outcome.tools_called)),
            calculations=observer.calculations,
            append_attempts=observer.append_attempts,
            append_attempts_omitted=observer.append_attempts_omitted,
            append_attempts_observable=True,
            operation_receipts=list(outcome.operation_receipts),
            operation_references=list(outcome.operation_references),
            model_steps=outcome.model_steps,
            loaded_skills=dict(outcome.loaded_skills),
            cache_observable=False,
        )
