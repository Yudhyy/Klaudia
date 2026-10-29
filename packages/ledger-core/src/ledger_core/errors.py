"""Failures that prevent a ledger operation from committing."""


class SheetNotFoundError(Exception):
    """The requested sheet does not exist in the authorised workbook."""


class RevisionConflictError(Exception):
    """The sheet changed after the operation's source snapshot."""


class IdempotencyConflictError(Exception):
    """An operation key already belongs to a different request."""


class ApprovalRequiredError(Exception):
    """Execution paused until a human decides the exact stored proposal."""

    def __init__(self, approval: dict) -> None:
        """Carry the client-visible proposal without treating preparation as consent.

        Args:
            approval: Durable identity, revisions and proposed records.
        """
        super().__init__("The stored operation requires approval")
        self.approval = approval
