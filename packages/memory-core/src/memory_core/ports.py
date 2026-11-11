"""Owner-scoped context reads and atomic revision edits."""

from typing import Protocol

from memory_core.contracts import DocumentEdit, DocumentPath, MemoryDocument


class DocumentConflict(ValueError):
    """The supplied revision no longer matches the current document."""


class DocumentReader(Protocol):
    """Read current context without granting edit access."""

    async def read(self, user_id: int, path: DocumentPath) -> MemoryDocument:
        """Read only the authenticated owner's current document.

        Args:
            user_id: Server-derived owner identity.
            path: Allowed document path.

        Returns:
            Current content, a tombstone, or a missing document at revision zero.
        """
        ...


class DocumentRepository(DocumentReader, Protocol):
    """Commit each edit and its history together under the same owner."""

    async def write(
        self, user_id: int, path: DocumentPath, edit: DocumentEdit
    ) -> MemoryDocument:
        """Create, replace or restore a document at its expected revision.

        Args:
            user_id: Server-derived owner and actor identity.
            path: Allowed document path.
            edit: Validated content, policy, source note and expected revision.

        Returns:
            The committed active document at the next revision.

        Raises:
            DocumentConflict: The expected revision is stale.
            ValueError: Structured policy targets a different document path.
        """
        ...

    async def delete(
        self, user_id: int, path: DocumentPath, expected_revision: int
    ) -> MemoryDocument:
        """Clear content and policy while retaining revision history.

        Args:
            user_id: Server-derived owner and actor identity.
            path: Allowed document path.
            expected_revision: Current revision observed by the caller.

        Returns:
            The committed tombstone at the next revision.

        Raises:
            DocumentConflict: The document is absent or its revision is stale.
        """
        ...
