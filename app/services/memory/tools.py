"""On-demand context reads bound to authenticated chat identity."""

from typing import Any

from app.services.memory.contracts import DocumentPath
from app.services.memory.store import MemoryDocumentStore


class MemoryReader:
    """Read bounded context under the authenticated owner."""

    def __init__(self, store: MemoryDocumentStore, user_id: int) -> None:
        """Bind the authenticated owner outside tool arguments.

        Args:
            store: Application context store.
            user_id: Identity derived from the chat token.
        """
        self._store = store
        self._user_id = user_id

    async def read(self, path: str) -> dict[str, Any]:
        """Return observed context without claiming policy validation.

        Args:
            path: Requested context path, converted to the backend path type.

        Returns:
            Document revision and explicit trust/validation limits.

        Raises:
            ValueError: The context path is not supported.
        """
        document = await self._store.read(self._user_id, DocumentPath(path))
        return {
            "authority": "untrusted_context",
            "accounting_validation": "not_run",
            "document": document.model_dump(mode="json"),
        }
