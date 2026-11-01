"""Translate agent read intent without moving financial rules out of ledger."""

from typing import Any

from ledger_core.calculations import CheckedCalculation as LedgerCalculation
from ledger_core.financial_contracts import (
    CheckedFinancialRequest as LedgerFinancialRequest,
)
from ledger_core.resources import (
    ResourceInspection as LedgerInspection,
)
from ledger_core.resources import (
    ResourceSearch as LedgerSearch,
)

from app.adapters.agent.errors import ledger_failures
from app.services.catalogue.service import CatalogueService
from klaudia.core.agent.ports.calculations import CheckedCalculation
from klaudia.core.agent.ports.catalogue import (
    ResourceInspection,
    ResourceSearch,
)
from klaudia.core.agent.ports.financial import CheckedFinancialRequest


class CatalogueAdapter:
    """Use the existing owned service with canonical ledger request models."""

    def __init__(self, service: CatalogueService) -> None:
        """Bind the current application reader without opening another store.

        Args:
            service: Existing ownership-checking catalogue service.
        """
        self._service = service

    async def search(self, user_id: int, query: ResourceSearch) -> dict[str, Any]:
        """Translate model search intent and retain the authenticated owner.

        Args:
            user_id: Server-authenticated owner.
            query: Agent-owned metadata filters.

        Returns:
            Unchanged bounded discovery evidence.
        """
        with ledger_failures():
            return await self._service.search(
                user_id, LedgerSearch(**query.model_dump())
            )

    async def inspect(self, user_id: int, query: ResourceInspection) -> dict[str, Any]:
        """Translate the schema page request and recheck current ownership.

        Args:
            user_id: Server-authenticated owner.
            query: Selected table identity and column page.

        Returns:
            Unchanged bounded metadata and revision evidence.
        """
        with ledger_failures():
            return await self._service.inspect(
                user_id, LedgerInspection(**query.model_dump())
            )

    async def calculate(
        self, user_id: int, query: CheckedCalculation
    ) -> dict[str, Any]:
        """Translate metrics and inspected revisions for exact ledger calculation.

        Args:
            user_id: Server-authenticated owner.
            query: Agent intent with task-observed revisions.

        Returns:
            Unchanged exact metrics and source evidence.
        """
        with ledger_failures():
            return await self._service.calculate(
                user_id, LedgerCalculation(**query.model_dump())
            )

    async def financial_query(
        self, user_id: int, query: CheckedFinancialRequest
    ) -> dict[str, Any]:
        """Translate financial intent and preserve all source preconditions.

        Args:
            user_id: Server-authenticated owner.
            query: Agent intent and each inspected source revision.

        Returns:
            Unchanged exact ledger evidence.
        """
        with ledger_failures():
            return await self._service.financial_query(
                user_id, LedgerFinancialRequest(**query.model_dump())
            )
