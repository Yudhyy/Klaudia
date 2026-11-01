"""Document reads with ownership and response limits enforced by the backend."""

from typing import Any, Protocol


class DocumentReader(Protocol):
    """Read archived facts under a server-bound owner."""

    async def search(self, name: str = "", offset: int = 0) -> dict[str, Any]:
        """Search owned metadata without reading page bodies.

        Args:
            name: Literal filename text.
            offset: Number of matching documents to skip.

        Returns:
            At most 20 records and the next offset, if present.
        """
        ...

    async def read(self, file_id: int, page: int, offset: int = 0) -> dict[str, Any]:
        """Read one page segment after checking file and session ownership.

        Args:
            file_id: Archived document identity.
            page: One-based page number.
            offset: Character offset in the saved text.

        Returns:
            Page status, at most 8192 characters and the next offset.

        Raises:
            ResourceNotFoundError: The page is missing or foreign.
        """
        ...
