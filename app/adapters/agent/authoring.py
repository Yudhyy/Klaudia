"""Translate placement-read failures without changing HTTP authoring methods."""

from typing import Any

from app.adapters.agent.errors import ledger_failures
from app.services.catalogue.authoring import AuthoringService


class AuthoringAdapter:
    """Expose the existing owned placement reads to the agent."""

    def __init__(self, service: AuthoringService) -> None:
        """Bind the current authoring service.

        Args:
            service: Application service that retains its HTTP methods.
        """
        self._service = service

    async def sheets(
        self, user_id: int, *, workbook_id: str | None = None, offset: int = 0
    ) -> dict[str, Any]:
        """Read an owned sheet page with unchanged filters and limits.

        Args:
            user_id: Server-authenticated owner.
            workbook_id: Optional destination filter.
            offset: Number of sheets already read.

        Returns:
            Unchanged sheet identities and continuation offset.
        """
        with ledger_failures():
            return await self._service.sheets(
                user_id, workbook_id=workbook_id, offset=offset
            )

    async def inspect(
        self, user_id: int, sheet_id: int, table_range: str
    ) -> dict[str, Any]:
        """Read the selected region with current ownership and placement checks.

        Args:
            user_id: Server-authenticated owner.
            sheet_id: Selected sheet identity.
            table_range: Bounded placement region.

        Returns:
            Unchanged revision and placement evidence.
        """
        with ledger_failures():
            return await self._service.inspect(user_id, sheet_id, table_range)
