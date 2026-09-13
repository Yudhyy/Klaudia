"""Bounded resource reads using the authenticated user's current ownership."""

from typing import Any

from ledger.catalogue import CatalogueStore
from ledger.calculations import CheckedCalculation
from ledger.financial_contracts import CheckedFinancialRequest
from ledger.evidence import bounded_evidence, schema_page
from ledger.resources import ResourceSearch, ResourceInspection


class CatalogueService:
    """Keep authenticated identity separate from model-provided read arguments."""

    def __init__(self, store: CatalogueStore) -> None:
        """Use the catalogue connected to the application's ledger database.

        Args:
            store: Catalogue store enforcing ownership within each read query.
        """
        self._store = store

    async def search(self, user_id: int, query: ResourceSearch) -> dict[str, Any]:
        """Search current ownership without taking workbook IDs from the caller.

        Args:
            user_id: Identity supplied by the authentication boundary.
            query: Intent and metadata filters, with no authority fields.

        Returns:
            Bounded discovery evidence across owned workbooks.

        Raises:
            ValueError: Metadata exceeds the output budget.
        """
        return bounded_evidence(await self._store.search_owned(user_id, query))

    async def inspect(self, user_id: int, query: ResourceInspection) -> dict[str, Any]:
        """Recheck ownership while fetching the selected resource schema.

        Args:
            user_id: Identity supplied by the authentication boundary.
            query: Requested identity and schema page.

        Returns:
            Bounded metadata and column pagination with source freshness.

        Raises:
            ResourceNotFoundError: The resource is absent or foreign.
            ValueError: Metadata exceeds the output budget.
        """
        descriptor = await self._store.inspect_owned(user_id, query.table_id)
        return bounded_evidence(schema_page(descriptor, query))

    async def calculate(
        self, user_id: int, query: CheckedCalculation
    ) -> dict[str, Any]:
        """Calculate registered metrics with current ownership and revision checks.

        Args:
            user_id: Identity supplied by the authentication boundary.
            query: Metrics and observed sheet/catalogue revisions.

        Returns:
            Bounded exact metrics with stable identities and source revisions.

        Raises:
            ResourceNotFoundError: The table is absent or foreign.
            RevisionConflictError: Source or metadata revisions do not match.
            ValueError: Calculation inputs or output budget are invalid.
        """
        return bounded_evidence(await self._store.calculate_owned(user_id, query))

    async def financial_query(
        self, user_id: int, query: CheckedFinancialRequest
    ) -> dict[str, Any]:
        """Execute bounded financial reads with current ownership and revisions.

        Args:
            user_id: Authenticated owner supplied by the server.
            query: Explicit financial intent and observed source revisions.

        Returns:
            Labelled, bounded evidence from one database snapshot.

        Raises:
            ResourceNotFoundError: Any source is absent or foreign.
            RevisionConflictError: Source or metadata observations changed.
            ValueError: Inputs or evidence exceed the financial contract.
        """
        return await self._store.financial_owned(user_id, query)
