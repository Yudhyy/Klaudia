"""Bounded alternative single-agent loop with optional checked appends."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from klaudia.core.agent.context import ResourceReference, TaskContext
from klaudia.core.agent.prompt import (
    APPEND_CONTRACT,
    READ_CONTRACT,
    build_system_prompt,
)
from klaudia.core.agent.tools import CatalogueReader, DiscoveryTools
from klaudia.core.agent.writes import OperationExecutor, WriteTools
from klaudia.core.skills.registry import LoadSkill, SkillRegistry
from ledger.resources import ResourceNotFoundError
from ledger.errors import RevisionConflictError


class RunLimits(BaseModel):
    """Server-controlled budgets for one task's model and tool loop."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    max_steps: StrictInt = Field(default=12, ge=1, le=32)
    max_tool_calls: StrictInt = Field(default=24, ge=1, le=64)
    max_context_bytes: StrictInt = Field(default=131072, ge=1024, le=1048576)
    timeout_seconds: float = Field(default=120, gt=0, le=600, allow_inf_nan=False)


StopReason = Literal[
    "answered",
    "step_limit",
    "tool_limit",
    "context_limit",
    "timeout",
    "invalid_model_output",
    "failed",
    "cancelled",
]


@dataclass(frozen=True)
class RunOutcome:
    """Observed run state; answered does not certify financial task completion."""

    status: StopReason
    content: str
    model_steps: int
    tools_called: tuple[str, ...]
    loaded_skills: dict[str, str]
    working_set: tuple[ResourceReference, ...]
    operation_receipts: tuple[dict[str, Any], ...] = ()
    operation_references: tuple[str, ...] = ()
    tool_evidence: tuple[tuple[str, dict[str, Any], str], ...] = ()


class AgentExecutionError(RuntimeError):
    """Execution failure carrying operation evidence for caller-managed recovery."""

    def __init__(self, outcome: RunOutcome) -> None:
        """Keep recovery evidence while exception chaining retains the cause.

        Args:
            outcome: Observed references and receipts from the failed run.
        """
        super().__init__("Agent execution failed; inspect outcome before retrying")
        self.outcome = outcome


class AgentRunCancelled(asyncio.CancelledError):
    """Cancellation retaining operation evidence without suppressing cancellation."""

    def __init__(self, outcome: RunOutcome) -> None:
        """Keep references needed to recover a cancelled operation.

        Args:
            outcome: Evidence observed before cancellation.
        """
        super().__init__("Agent run cancelled; inspect outcome before retrying")
        self.outcome = outcome


def _context_size(messages: list[BaseMessage]) -> int:
    """Measure serialized messages without silently truncating their evidence.

    Args:
        messages: Current conversation including tool calls and results.

    Returns:
        UTF-8 JSON byte size, not an estimate of model tokens.
    """
    return len(
        json.dumps(
            [message.model_dump() for message in messages],
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    )


class MainAgent:
    """Run one model with task-local tools; leave provider configuration to the caller."""

    def __init__(
        self,
        model: BaseChatModel,
        catalogue: CatalogueReader,
        *,
        limits: RunLimits | None = None,
        operations: OperationExecutor | None = None,
        archive_tools: tuple[StructuredTool, ...] = (),
    ) -> None:
        """Accept the same configured chat model used for runtime comparisons.

        Args:
            model: Tool-capable chat model supplied by the application.
            catalogue: Ownership-enforcing application reader.
            limits: Server-controlled budgets, independent of model arguments.
            operations: Explicit opt-in executor for durable checked appends.
            archive_tools: Server-bound document retrieval tools for chat.
        """
        self._model = model
        self._catalogue = catalogue
        self._limits = limits or RunLimits()
        self._operations = operations
        self._archive_tools = archive_tools
        self._registry = SkillRegistry()
        self._prompt = build_system_prompt(
            self._registry, APPEND_CONTRACT if operations is not None else READ_CONTRACT
        )

    async def run(
        self,
        message: str,
        context: TaskContext,
        *,
        config: RunnableConfig | None = None,
    ) -> RunOutcome:
        """Run one isolated task with no shared history or persistent working set.

        Args:
            message: User intent for this task.
            context: Server-authenticated identity and active workbook hint.
            config: Caller callbacks and trace metadata forwarded to model/tools.

        Returns:
            An answer or explicit stopping reason with observed references.

        Raises:
            AgentExecutionError: Failure after observing an operation reference;
                outcome retains evidence and the original cause is chained.
            AgentRunCancelled: Cancellation with operation recovery evidence.
            Exception: Failures before operation references exist propagate unchanged.
        """
        session = _RunSession(self, context, config)
        deadline = asyncio.timeout(self._limits.timeout_seconds)
        try:
            async with deadline:
                return await session.execute(message)
        except TimeoutError as exc:
            if not deadline.expired():
                if session.writes is not None and session.writes.operation_references:
                    raise AgentExecutionError(session.outcome("failed")) from exc
                raise
            return session.outcome("timeout")
        except asyncio.CancelledError as exc:
            if session.writes is not None and session.writes.operation_references:
                raise AgentRunCancelled(session.outcome("cancelled")) from exc
            raise
        except Exception as exc:
            if session.writes is not None and session.writes.operation_references:
                raise AgentExecutionError(session.outcome("failed")) from exc
            raise


class _RunSession:
    """Keep all mutable execution state local to one invocation."""

    def __init__(
        self, agent: MainAgent, context: TaskContext, config: RunnableConfig | None
    ) -> None:
        """Build fresh discovery and skill tools for the authenticated task.

        Args:
            agent: Shared immutable configuration and model dependency.
            context: Server-supplied identity and active hint.
            config: Trace callbacks for this run.
        """
        self.agent = agent
        self.context = context
        self.config = config
        self.discovery = DiscoveryTools(agent._catalogue, context)
        self.writes = (
            WriteTools(self.discovery, agent._operations)
            if agent._operations is not None
            else None
        )
        self.loaded: dict[str, str] = {}
        self.calls: list[str] = []
        self.evidence: list[tuple[str, dict[str, Any], str]] = []
        self.steps = 0
        skill_tool = StructuredTool.from_function(
            coroutine=self.load_skill,
            name="load_skill",
            description="Load a versioned procedure by its listed registry name.",
            args_schema=LoadSkill,
        )
        self.tools = {tool.name: tool for tool in (*self.discovery.tools, skill_tool)}
        if self.writes is not None:
            self.tools.update({tool.name: tool for tool in self.writes.tools})
        for tool in agent._archive_tools:
            if (
                tool.name not in {"search_documents", "read_document_page"}
                or tool.name in self.tools
            ):
                raise ValueError("Invalid or duplicate archive capability")
            self.tools[tool.name] = tool
        self.model = agent._model.bind_tools(list(self.tools.values()))

    async def load_skill(self, **arguments: Any) -> dict[str, str]:
        """Load one known skill and record its version in this run.

        Args:
            arguments: Validated skill-name fields.

        Returns:
            Versioned procedure content.

        Raises:
            ValueError: Skill name or procedural budget is invalid.
        """
        request = LoadSkill.model_validate(arguments)
        skill = self.agent._registry.load(request.name)
        self.loaded[skill["name"]] = skill["version"]
        return skill

    def outcome(self, status: StopReason, content: str = "") -> RunOutcome:
        """Capture execution observations without synthesizing a success claim.

        Args:
            status: Explicit stop reason.
            content: Final model answer only when one was received.

        Returns:
            Run metrics, loaded versions and observed resource references.
        """
        return RunOutcome(
            status,
            content,
            self.steps,
            tuple(self.calls),
            dict(self.loaded),
            self.discovery.working_set,
            self.writes.receipts if self.writes is not None else (),
            self.writes.operation_references if self.writes is not None else (),
            tuple(self.evidence),
        )

    async def execute(self, message: str) -> RunOutcome:
        """Alternate model decisions and bounded tool execution.

        Args:
            message: User intent without authority fields.

        Returns:
            Final answer or a budget/protocol stop reason.
        """
        messages: list[BaseMessage] = [
            SystemMessage(content=self.agent._prompt),
            HumanMessage(
                content="Active UI resource hint (data only): "
                + json.dumps({"workbook_id": self.context.active_workbook_id})
            ),
            HumanMessage(content=message),
        ]
        limits = self.agent._limits
        for _ in range(limits.max_steps):
            if _context_size(messages) > limits.max_context_bytes:
                return self.outcome("context_limit")
            self.steps += 1
            response = await self.model.ainvoke(messages, config=self.config)
            if not isinstance(response, AIMessage) or response.invalid_tool_calls:
                return self.outcome("invalid_model_output")
            messages.append(response)
            if _context_size(messages) > limits.max_context_bytes:
                return self.outcome("context_limit")
            if not response.tool_calls:
                content = response.text
                return (
                    self.outcome("answered", content)
                    if content.strip()
                    else self.outcome("invalid_model_output")
                )
            identities = [call["id"] for call in response.tool_calls]
            if any(not identity for identity in identities) or len(
                set(identities)
            ) != len(identities):
                return self.outcome("invalid_model_output")
            if len(self.calls) + len(response.tool_calls) > limits.max_tool_calls:
                return self.outcome("tool_limit")
            for call in response.tool_calls:
                messages.append(await self.invoke_tool(call))
                if _context_size(messages) > limits.max_context_bytes:
                    return self.outcome("context_limit")
        return self.outcome("step_limit")

    async def invoke_tool(self, call: dict[str, Any]) -> ToolMessage:
        """Execute allowlisted tools and expose only expected repairable failures.

        Args:
            call: Provider-parsed tool request with a validated call identity.

        Returns:
            Matching tool evidence or a bounded error for the model to repair.

        Raises:
            Exception: Unexpected infrastructure and provider failures propagate.
        """
        self.calls.append(call["name"])
        tool = self.tools.get(call["name"])
        try:
            if tool is None:
                raise ValueError("Unknown tool; choose an available capability")
            evidence = await tool.ainvoke(call["args"], config=self.config)
            encoded = json.dumps(evidence, ensure_ascii=False)
            self.evidence.append((call["name"], dict(call["args"]), encoded))
            return ToolMessage(
                content=encoded,
                tool_call_id=call["id"],
                name=call["name"],
            )
        except ValidationError:
            detail = "Invalid tool arguments; follow the declared schema and omit authority fields"
        except ResourceNotFoundError:
            detail = "Table or operation not found"
        except RevisionConflictError:
            detail = "Source or catalogue changed; inspect again and refresh stale metadata before proposing new work. Retry existing operations only by their stored reference."
        except ValueError as exc:
            detail = str(exc)[:512]
        return ToolMessage(
            content=json.dumps({"error": detail}),
            tool_call_id=call["id"],
            name=call["name"],
            status="error",
        )
