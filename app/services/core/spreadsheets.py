"""Per-user spreadsheet management over the ledger store.

The app-side owner of the user -> spreadsheets -> sheets model: CRUD with
ownership guards for /v1/spreadsheets, and scope resolution for chat
requests (the active workbook hint supplied to the main agent).

Ownership failures raise SpreadsheetNotFoundError so foreign spreadsheets
are indistinguishable from absent ones (non-enumerating, same stance as
session 404s).
"""

from __future__ import annotations

import logging
from typing import Any

from ledger.store import (
    LedgerStore,
    SpreadsheetExistsError,
    SpreadsheetNotFoundError,
)

logger = logging.getLogger(__name__)

DEFAULT_SPREADSHEET_NAME = "Utama"


class SpreadsheetService:
    """Ownership-checked spreadsheet CRUD + chat scope resolution."""

    def __init__(
        self, store: LedgerStore, default_name: str = DEFAULT_SPREADSHEET_NAME
    ) -> None:
        self._store = store
        self._default_name = default_name

    async def list_for_user(self, user_id: int) -> list[dict[str, Any]]:
        return await self._store.list_spreadsheets(user_id)

    async def create(self, user_id: int, name: str) -> dict[str, Any]:
        return await self._store.create_spreadsheet(user_id, name)

    async def rename(self, user_id: int, spreadsheet_id: str, new_name: str) -> None:
        await self._owned(user_id, spreadsheet_id)
        await self._store.rename_spreadsheet(spreadsheet_id, new_name)

    async def delete(self, user_id: int, spreadsheet_id: str) -> None:
        await self._owned(user_id, spreadsheet_id)
        await self._store.delete_spreadsheet(spreadsheet_id)

    async def recent_activity(
        self, spreadsheet_id: str, limit: int = 3
    ) -> list[dict[str, Any]]:
        """Most-recently-edited sheets in a spreadsheet (continuity signal).

        The caller passes the already-resolved active scope (ownership was
        validated in resolve_scope), so no ownership re-check is done here.
        """
        return await self._store.get_recent_activity(spreadsheet_id, limit)

    async def resolve_scope(self, user_id: int, requested: str | None = None) -> str:
        """Return the spreadsheet the current request operates on.

        Args:
            user_id: Authenticated user (from JWT).
            requested: Optional client-selected spreadsheet id.

        Returns:
            The validated spreadsheet id. First-time users get a default
            spreadsheet provisioned automatically.

        Raises:
            SpreadsheetNotFoundError: If requested is absent or foreign.
        """
        if requested:
            await self._owned(user_id, requested)
            return requested

        existing = await self._store.list_spreadsheets(user_id)
        if existing:
            return existing[0]["spreadsheetId"]

        try:
            created = await self._store.create_spreadsheet(user_id, self._default_name)
            logger.info("Provisioned default spreadsheet for user %s", user_id)
            return created["spreadsheetId"]
        except SpreadsheetExistsError:
            # Lost a concurrent first-request race; the winner's row exists now.
            return (await self._store.list_spreadsheets(user_id))[0]["spreadsheetId"]

    async def _owned(self, user_id: int, spreadsheet_id: str) -> dict[str, Any]:
        info = await self._store.get_spreadsheet(spreadsheet_id)
        if info["userId"] != user_id:
            raise SpreadsheetNotFoundError(f"Spreadsheet '{spreadsheet_id}' not found")
        return info
