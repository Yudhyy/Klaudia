"""Authenticated access to durable ledger append proposals and receipts."""

from ledger.authoring import AuthoringProposal, prepare_authoring

from typing import Any

from ledger.store import LedgerStore
from ledger.table_operations import (
    TableAppendProposal,
    prepare_table_append,
    execute_prepared_operation,
)


class OperationService:
    """Reuse ledger operation storage without owning a second receipt store."""

    def __init__(self, store: LedgerStore) -> None:
        """Bind the connected ledger store.

        Args:
            store: Ledger with operation migrations applied.
        """
        self._store = store

    async def prepare(
        self, user_id: int, request: TableAppendProposal
    ) -> dict[str, str]:
        """Retain a checked append under server-managed retry identity.

        Args:
            user_id: Server-authenticated identity.
            request: Records with revisions supplied by inspected evidence.

        Returns:
            A durable reference; no financial write has occurred.

        Raises:
            ResourceNotFoundError: The table is absent or foreign.
            ValueError: The request exceeds its budget.
        """
        return await prepare_table_append(self._store.pool, user_id, request)

    async def prepare_authoring(
        self, user_id: int, request: AuthoringProposal
    ) -> dict[str, Any]:
        """Persist an owned table-authoring proposal for later checked execution.

        Args:
            user_id: Server-authenticated task owner.
            request: Exact authoring action and observed revisions.

        Returns:
            Original stored reference with optional approval identity.
        """
        return await prepare_authoring(self._store.pool, user_id, request)

    async def execute(self, user_id: int, operation_ref: str) -> dict[str, Any]:
        """Execute the stored request or replay its committed receipt.

        Args:
            user_id: Server-authenticated identity.
            operation_ref: Previously prepared reference.

        Returns:
            Receipt after transactional access and execution checks.

        Raises:
            ResourceNotFoundError: Proposal or current ownership is absent.
            RevisionConflictError: Uncommitted input revisions changed.
            ValueError: Append validation fails.
        """
        return await execute_prepared_operation(
            self._store.pool, user_id, operation_ref
        )
