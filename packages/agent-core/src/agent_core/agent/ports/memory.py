"""Read-only context interface with no backend document representation."""

from enum import StrEnum
from typing import Any, Protocol


class DocumentPath(StrEnum):
    """Allow only the supported context documents."""

    PREFERENCES = "/preferences.md"
    CONVENTIONS = "/conventions.md"
    ACCOUNTING_POLICY = "/accounting-policy.md"


class MemoryReader(Protocol):
    """Read owner-scoped context without granting memory write access."""

    async def read(self, path: str) -> dict[str, Any]:
        """Read a saved document under a server-bound owner.

        Args:
            path: One of the supported context paths.

        Returns:
            Bounded document fields, revision, status and explicit trust limits.

        Raises:
            ValueError: The path is not supported.
        """
        ...
