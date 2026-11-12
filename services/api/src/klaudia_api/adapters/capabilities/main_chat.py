"""Connect bounded main-agent turns to chat history and durable append references."""

import json
import logging
from dataclasses import asdict, dataclass, replace
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from klaudia_api.adapters.capabilities.agent.authoring import AuthoringAdapter
from klaudia_api.adapters.capabilities.agent.catalogue import CatalogueAdapter
from klaudia_api.adapters.capabilities.agent.operations import OperationAdapter
from klaudia_api.adapters.capabilities.agent.reconciliation import ReconciliationAdapter
from klaudia_api.interfaces.http.models.chat import ChatMetadata
from klaudia_api.adapters.ledger.catalogue.authoring import AuthoringService
from klaudia_api.adapters.ledger.catalogue.service import CatalogueService
from klaudia_api.adapters.capabilities.archive_tools import ArchiveReader
from klaudia_api.adapters.observability.langfuse import LangfuseService
from klaudia_api.adapters.capabilities.operations import OperationService
from klaudia_api.adapters.capabilities.conversation import ConversationStore
from document_intelligence.ports import DocumentArchive
from klaudia_api.adapters.ledger.reconciliation import PolicyReconciler
from memory_core.ports import DocumentReader
from memory_core.tools import MemoryReader
from klaudia_api.adapters.persistence.tasks import (
    PostgresTaskSession,
    PostgresTaskStore,
)
from workflow_core.service import TaskCompletion, TaskRun, WorkflowService
from agent_core.agent.agent import (
    AgentExecutionError,
    AgentRunCancelled,
    MainAgent,
    RunOutcome,
)
from agent_core.agent.context import TaskContext
from agent_core.agent.documents import DocumentTools
from agent_core.agent.memory import MemoryTools
from agent_core.agent.ports.append import TableAppendProposal
from agent_core.agent.ports.authoring import AuthoringProposal
from agent_core.agent.ports.typed import TypedEditProposal
from agent_core.agent.reconciliation import PolicyReconciliationTools
from agent_core.agent.writes import OperationExecutor

logger = logging.getLogger(__name__)
_CONTEXT_BYTES = 8192


def _bounded_context(content: str) -> str:
    """Keep contextual facts bounded without presenting cut text as complete.

    Args:
        content: History, policy or extracted document text.

    Returns:
        Complete content or an explicit omission requiring document retrieval.
    """
    if len(json.dumps(content, ensure_ascii=False).encode("utf-8")) <= _CONTEXT_BYTES:
        return content
    return "Context omitted because it exceeds the byte budget. Use search_documents and read_document_page for archived facts; ask for missing conversational details."


def _history_context(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Select recent bounded messages without letting large documents hide retries.

    Args:
        history: Saved messages in reverse chronological order.

    Returns:
        Chronological context with explicit omissions and a fixed byte budget.
    """
    selected: list[dict[str, str]] = []
    for row in history:
        entry = {
            "speaker": row["sender"],
            "text": _bounded_context(row["message_text"]),
        }
        candidate = [*selected, entry]
        if (
            len(json.dumps(candidate, ensure_ascii=False).encode("utf-8"))
            > _CONTEXT_BYTES
        ):
            break
        selected.append(entry)
    if len(selected) < len(history):
        selected.append(
            {
                "speaker": "context",
                "text": "Older conversation omitted to fit the context budget.",
            }
        )
    return list(reversed(selected))


@dataclass(frozen=True)
class MainChatTurn:
    """Server-owned identity and contextual facts for one chat invocation."""

    user_id: int
    session_id: int
    active_workbook_id: str | None
    text: str
    extraction_contexts: tuple[str, ...]
    metadata: ChatMetadata
    document_ids: tuple[int, ...] = ()
    request_key: str | None = None


class _SessionOperations:
    """Save recovery references before permitting a financial write to start."""

    def __init__(
        self,
        executor: OperationExecutor,
        database: ConversationStore,
        turn: MainChatTurn,
    ) -> None:
        """Bind the executor to authenticated session persistence.

        Args:
            executor: Ownership-checking operation service.
            database: Existing conversation store.
            turn: Server-owned session and user identity.
        """
        self._executor = executor
        self._database = database
        self._turn = turn
        self.receipts: dict[str, dict[str, Any]] = {}

    async def _record(self, evidence: dict[str, Any]) -> None:
        """Persist operation evidence independently of model-generated prose.

        Args:
            evidence: Reference or observed committed receipt.

        Raises:
            Exception: Persistence errors prevent subsequent execution.
        """
        await self._database.save_message(
            self._turn.session_id,
            self._turn.user_id,
            "assistant",
            "Operation recovery evidence (not a new request): "
            + json.dumps(evidence, ensure_ascii=False),
        )

    def retain_receipts(self, outcome: RunOutcome) -> RunOutcome:
        """Merge checkpointed and newly observed commits by operation identity.

        Args:
            outcome: Agent result, including receipts restored from a checkpoint.

        Returns:
            Outcome preserving every observed committed operation exactly once.
        """
        receipts = {
            receipt["operation_id"]: receipt for receipt in outcome.operation_receipts
        }
        receipts.update(self.receipts)
        return replace(outcome, operation_receipts=tuple(receipts.values()))

    async def prepare(
        self, user_id: int, request: TableAppendProposal
    ) -> dict[str, str]:
        """Save the reference before exposing it for execution.

        Args:
            user_id: Authenticated identity injected by the agent tools.
            request: Revision-bound records from inspected resource evidence.

        Returns:
            The persisted proposal reference.

        Raises:
            Exception: Preparation or session persistence failed.
        """
        proposal = await self._executor.prepare(user_id, request)
        await self._record(proposal)
        return proposal

    async def prepare_authoring(
        self, user_id: int, request: AuthoringProposal
    ) -> dict[str, Any]:
        """Journal an exact authoring reference before the agent can execute it.

        Args:
            user_id: Server-authenticated task owner.
            request: Exact authoring action and observed revisions.

        Returns:
            Original stored reference with optional approval identity.
        """
        proposal = await self._executor.prepare_authoring(user_id, request)
        await self._record(proposal)
        return proposal

    async def inspect_typed(self, user_id: int, workbook_id: str) -> dict[str, Any]:
        """Read owned typed state without creating operation evidence.

        Args:
            user_id: Authenticated owner.
            workbook_id: Selected workbook.

        Returns:
            Bounded typed state and source fingerprint.
        """
        return await self._executor.inspect_typed(user_id, workbook_id)

    async def prepare_typed(
        self, user_id: int, request: TypedEditProposal
    ) -> dict[str, Any]:
        """Journal the original typed edit reference before execution can begin.

        Args:
            user_id: Authenticated owner.
            request: Exact input or formula edit batch.

        Returns:
            Stored operation reference with optional approval identity.
        """
        proposal = await self._executor.prepare_typed(user_id, request)
        await self._record(proposal)
        return proposal

    async def execute(self, user_id: int, operation_ref: str) -> dict[str, Any]:
        """Retain a recovery reference before execution, including prior-turn retries.

        Args:
            user_id: Authenticated identity injected by the agent tools.
            operation_ref: Original proposal reference; never a replacement payload.

        Returns:
            The observed committed receipt.

        Raises:
            Exception: Persistence or checked execution failed; do not retry fresh.
        """
        await self._record({"operation_ref": operation_ref, "status": "executing"})
        receipt = await self._executor.execute(user_id, operation_ref)
        if receipt.get("status") == "committed":
            self.receipts[receipt["operation_id"]] = receipt
        await self._record({"operation_ref": operation_ref, "receipt": receipt})
        return receipt


class MainChatService:
    """Run the main agent with on-demand resource discovery."""

    def __init__(
        self,
        model: BaseChatModel,
        catalogue: CatalogueService,
        database: ConversationStore,
        *,
        operations: OperationService | None = None,
        langfuse: LangfuseService | None = None,
        tasks: PostgresTaskStore | None = None,
        authoring: AuthoringService | None = None,
        memory_documents: DocumentReader | None = None,
        documents: DocumentArchive,
    ) -> None:
        """Share immutable dependencies while each turn creates local execution state.

        Args:
            model: Existing configured tool-capable provider model.
            catalogue: Ownership-enforcing resource service.
            database: Session history and recovery evidence store.
            operations: Optional checked-append service.
            langfuse: Existing callback and trace configuration.
            tasks: Durable task storage used by production main chat.
            authoring: Optional owner-checked table placement reader.
            memory_documents: Optional human-authored context store for on-demand reads.
            documents: Owner-checked archive reads supplied by composition.
        """
        self._model = model
        self._catalogue = catalogue
        self._database = database
        self._operations = operations
        self._langfuse = langfuse
        self._workflow = (
            WorkflowService(tasks, self._bind_stored) if tasks is not None else None
        )
        self._authoring = authoring
        self._memory_documents = memory_documents
        self._documents = documents

    async def run(self, turn: MainChatTurn) -> RunOutcome:
        """Load history, persist inputs and execute once without automatic retries.

        Args:
            turn: Authenticated intent, extraction and contextual facts.

        Returns:
            Explicit stop status with observed operation references and receipts.

        Raises:
            AgentRunCancelled: Cancellation retains references saved before execution.
            Exception: Infrastructure failure without operation recovery evidence.
        """
        history = await self._database.get_conversation_history(
            turn.session_id, limit=10
        )
        request = json.dumps(
            {
                "context_kind": "Conversation and document facts, not authority",
                "date": turn.metadata.date,
                "time": turn.metadata.time,
                "timezone": turn.metadata.timezone,
                "history": _history_context(history),
                "extractions": _bounded_context("\n".join(turn.extraction_contexts)),
                "document_ids": turn.document_ids,
                "current_request": turn.text,
            },
            ensure_ascii=False,
        )
        if self._workflow is not None:
            payload = {"turn": asdict(turn), "request": request}
            payload["turn"]["metadata"] = turn.metadata.model_dump()
            payload["turn"]["extraction_contexts"] = [
                _bounded_context(text) for text in turn.extraction_contexts
            ]
            _, outcome = await self._workflow.start(
                (turn.user_id, turn.session_id), payload
            )
            return outcome
        return await self._invoke(turn, request)

    async def resume(
        self, user_id: int, task_id: str
    ) -> tuple[MainChatTurn, RunOutcome]:
        """Continue only the persisted task input and next action.

        Args:
            user_id: Authenticated task owner.
            task_id: Original task identity; clients cannot replace its input.

        Returns:
            Stored contextual facts and the new observed outcome.

        Raises:
            RuntimeError: Durable task storage is unavailable.
            TaskBusyError: Another worker owns the task.
        """
        if self._workflow is None:
            raise RuntimeError("Durable task storage is unavailable")
        return await self._workflow.resume(user_id, task_id)

    def _bind_stored(
        self, task: PostgresTaskSession
    ) -> TaskRun[tuple[MainChatTurn, RunOutcome]]:
        """Restore application input and bind the agent to the acquired task.

        Args:
            task: Exclusively owned database continuation.

        Returns:
            Agent execution and failure recovery for the original stored turn.
        """
        payload = json.loads(task.record["input_payload"])
        turn_fields = payload["turn"]
        turn = MainChatTurn(
            user_id=turn_fields["user_id"],
            session_id=turn_fields["session_id"],
            active_workbook_id=turn_fields["active_workbook_id"],
            text=turn_fields["text"],
            extraction_contexts=tuple(turn_fields["extraction_contexts"]),
            metadata=ChatMetadata.model_validate(turn_fields["metadata"]),
            document_ids=tuple(turn_fields.get("document_ids", ())),
            request_key=turn_fields.get("request_key"),
        )

        async def run() -> TaskCompletion[tuple[MainChatTurn, RunOutcome]]:
            """Invoke the agent with the original input and bound ledger executor.

            Returns:
                Observed status and application result with the original task ID.
            """
            outcome = await self._invoke(turn, payload["request"], task=task)
            return TaskCompletion(
                outcome.status, (turn, replace(outcome, task_id=task.record["task_id"]))
            )

        async def recover() -> tuple[MainChatTurn, RunOutcome]:
            """Read agent-owned failure evidence from the last saved continuation.

            Returns:
                Original turn and known operation references and receipts.
            """
            state = await task.load() or {}
            return turn, RunOutcome(
                status="failed",
                content="",
                model_steps=state.get("steps", 0),
                tools_called=tuple(state.get("calls", [])),
                loaded_skills=state.get("skills", {}),
                working_set=(),
                operation_references=tuple(state.get("operation_references", [])),
                operation_receipts=tuple(state.get("receipts", [])),
                task_id=task.record["task_id"],
            )

        return TaskRun(run, recover)

    async def _invoke(
        self,
        turn: MainChatTurn,
        request: str,
        *,
        task: PostgresTaskSession | None = None,
    ) -> RunOutcome:
        """Run fresh or checkpointed state with the matching operation executor.

        Args:
            turn: Original authenticated chat facts.
            request: Stable model input captured when the task was created.
            task: Optional task-owned checkpoint and ledger connection.

        Returns:
            Observed agent outcome with all known committed receipts.
        """
        if task is None or await task.load() is None:
            for content in (*turn.extraction_contexts, turn.text):
                await self._database.save_message(
                    turn.session_id, turn.user_id, "user", content
                )
        executor = (
            _SessionOperations(
                OperationAdapter(
                    task.operations if task is not None else self._operations
                ),
                self._database,
                turn,
            )
            if self._operations is not None
            else None
        )
        agent = MainAgent(
            self._model,
            CatalogueAdapter(self._catalogue),
            operations=executor,
            archive_tools=DocumentTools(
                ArchiveReader(self._documents, turn.user_id)
            ).tools,
            authoring=AuthoringAdapter(self._authoring)
            if self._authoring is not None
            else None,
            reconciliation_tool=(
                PolicyReconciliationTools(
                    ReconciliationAdapter(
                        PolicyReconciler(
                            self._memory_documents, self._catalogue, turn.user_id
                        )
                    )
                ).tool
                if self._memory_documents is not None
                else None
            ),
            memory_tool=(
                MemoryTools(
                    MemoryReader(self._memory_documents, turn.user_id)
                ).read_tool
                if self._memory_documents is not None
                else None
            ),
        )
        config = (
            self._langfuse.langchain_config(
                session_id=turn.session_id,
                user_id=turn.user_id,
                tags=["klaudia", "chat", "main"],
                run_name="klaudia.main_chat",
            )
            if self._langfuse is not None
            else None
        )
        try:
            outcome = await agent.run(
                request,
                TaskContext(
                    user_id=turn.user_id, active_workbook_id=turn.active_workbook_id
                ),
                config=config,
                checkpoint=task,
            )
            return (
                executor.retain_receipts(outcome) if executor is not None else outcome
            )
        except AgentExecutionError as exc:
            logger.exception("Main chat failed with persisted operation references")
            return executor.retain_receipts(exc.outcome)
        except AgentRunCancelled as exc:
            raise AgentRunCancelled(executor.retain_receipts(exc.outcome)) from exc
