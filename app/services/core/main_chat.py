"""Connect bounded main-agent turns to chat history and durable append references."""

import json
import logging
from dataclasses import dataclass, replace
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from app.models.chat import ChatMetadata
from app.services.core.archive_tools import ArchiveTools
from app.services.core.observability import LangfuseService
from app.services.extraction.infra.db_client import AppDBClient
from klaudia.core.agent.agent import (
    AgentExecutionError,
    AgentRunCancelled,
    MainAgent,
    RunOutcome,
)
from klaudia.core.agent.context import TaskContext
from klaudia.core.agent.tools import CatalogueReader
from klaudia.core.agent.writes import OperationExecutor
from ledger.table_operations import TableAppendProposal

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
    memory_context: str = ""
    document_ids: tuple[int, ...] = ()


class _SessionOperations:
    """Save recovery references before permitting a financial write to start."""

    def __init__(
        self, executor: OperationExecutor, database: AppDBClient, turn: MainChatTurn
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
    """Run the main agent without injecting legacy prompts or sheet inventories."""

    def __init__(
        self,
        model: BaseChatModel,
        catalogue: CatalogueReader,
        database: AppDBClient,
        *,
        operations: OperationExecutor | None = None,
        langfuse: LangfuseService | None = None,
    ) -> None:
        """Share immutable dependencies while each turn creates local execution state.

        Args:
            model: Existing configured tool-capable provider model.
            catalogue: Ownership-enforcing resource service.
            database: Session history and recovery evidence store.
            operations: Optional checked-append service.
            langfuse: Existing callback and trace configuration.
        """
        self._model = model
        self._catalogue = catalogue
        self._database = database
        self._operations = operations
        self._langfuse = langfuse

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
                "memory": _bounded_context(turn.memory_context),
                "history": _history_context(history),
                "extractions": _bounded_context("\n".join(turn.extraction_contexts)),
                "document_ids": turn.document_ids,
                "current_request": turn.text,
            },
            ensure_ascii=False,
        )
        for content in (*turn.extraction_contexts, turn.text):
            await self._database.save_message(
                turn.session_id, turn.user_id, "user", content
            )
        executor = (
            _SessionOperations(self._operations, self._database, turn)
            if self._operations is not None
            else None
        )
        agent = MainAgent(
            self._model,
            self._catalogue,
            operations=executor,
            archive_tools=ArchiveTools(self._database, turn.user_id).tools,
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
            )
            return (
                replace(outcome, operation_receipts=tuple(executor.receipts.values()))
                if executor is not None
                else outcome
            )
        except AgentExecutionError as exc:
            logger.exception("Main chat failed with persisted operation references")
            return replace(
                exc.outcome, operation_receipts=tuple(executor.receipts.values())
            )
        except AgentRunCancelled as exc:
            raise AgentRunCancelled(
                replace(
                    exc.outcome, operation_receipts=tuple(executor.receipts.values())
                )
            ) from exc
