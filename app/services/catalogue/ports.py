"""Owner-scoped workbook access required by saved task replay."""

from typing import Protocol


class WorkbookAccess(Protocol):
    """Recheck the current owner of saved workbook identities."""

    async def count_owned_workbooks(self, user_id: int, workbook_ids: list[str]) -> int:
        """Count selected workbooks that still belong to the caller.

        Args:
            user_id: Authenticated task owner.
            workbook_ids: Distinct workbook identities from saved evidence.

        Returns:
            Number of selected workbooks currently owned by the caller.
        """
        ...
