"""Expected capability failures that the agent can report or pause for."""

from typing import Any


class ResourceNotFoundError(Exception):
    """The requested source or operation is missing or foreign."""


class ResourceExistsError(Exception):
    """A registered table already occupies the requested region."""


class RevisionConflictError(Exception):
    """Observed policy, source or catalogue revisions no longer match."""


class ApprovalRequiredError(Exception):
    """Execution paused until a human decides the exact stored proposal."""

    def __init__(self, approval: dict[str, Any]) -> None:
        """Carry the client-visible proposal without treating preparation as consent.

        Args:
            approval: Durable identity, revisions and proposed records.
        """
        super().__init__("The stored operation requires approval")
        self.approval = approval
