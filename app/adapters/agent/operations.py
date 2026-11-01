"""Translate requests through the executor selected for the current run."""

from typing import Any

from ledger_core.append_contracts import TableAppendProposal as LedgerAppend
from ledger_core.authoring_contracts import AuthoringProposal as LedgerAuthoring
from ledger_core.typed_contracts import TypedEditProposal as LedgerTypedEdit

from app.adapters.agent.errors import ledger_failures
from app.services.core.operations import OperationService
from app.services.workflow.store import TaskSession
from agent_core.agent.ports.append import TableAppendProposal
from agent_core.agent.ports.authoring import AuthoringProposal
from agent_core.agent.ports.typed import TypedEditProposal


class OperationAdapter:
    """Keep standalone or task-bound execution on its existing connection."""

    def __init__(self, executor: OperationService | TaskSession) -> None:
        """Bind the supplied executor without building a store or pool.

        Args:
            executor: Selected operation service or locked durable task session.
        """
        self._executor = executor

    async def prepare(
        self, user_id: int, request: TableAppendProposal
    ) -> dict[str, str]:
        """Translate an append without changing its observed revisions.

        Args:
            user_id: Server-authenticated owner.
            request: Exact records and inspected source revisions.

        Returns:
            Original persisted proposal reference.
        """
        with ledger_failures():
            return await self._executor.prepare(
                user_id, LedgerAppend(**request.model_dump())
            )

    async def prepare_authoring(
        self, user_id: int, request: AuthoringProposal
    ) -> dict[str, Any]:
        """Validate authoring intent in ledger before the selected executor stores it.

        Args:
            user_id: Server-authenticated owner.
            request: Action, definition and observed revisions.

        Returns:
            Original persisted reference and any approval identity.
        """
        with ledger_failures():
            return await self._executor.prepare_authoring(
                user_id, LedgerAuthoring(**request.model_dump())
            )

    async def inspect_typed(self, user_id: int, workbook_id: str) -> dict[str, Any]:
        """Read typed evidence through the selected execution scope.

        Args:
            user_id: Server-authenticated owner.
            workbook_id: Selected workbook identity.

        Returns:
            Unchanged typed evidence and source fingerprint.
        """
        with ledger_failures():
            return await self._executor.inspect_typed(user_id, workbook_id)

    async def prepare_typed(
        self, user_id: int, request: TypedEditProposal
    ) -> dict[str, Any]:
        """Validate exact values and formula rules in the canonical ledger model.

        Args:
            user_id: Server-authenticated owner.
            request: Typed edits and observed workbook fingerprint.

        Returns:
            Original persisted reference and any approval identity.
        """
        with ledger_failures():
            return await self._executor.prepare_typed(
                user_id, LedgerTypedEdit(**request.model_dump())
            )

    async def execute(self, user_id: int, operation_ref: str) -> dict[str, Any]:
        """Execute or replay the original reference on the supplied executor.

        Args:
            user_id: Server-authenticated owner.
            operation_ref: Original stored operation identity.

        Returns:
            Unchanged committed receipt.
        """
        with ledger_failures():
            return await self._executor.execute(user_id, operation_ref)
