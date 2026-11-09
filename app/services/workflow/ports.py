"""Define task storage and decision interfaces without concrete infrastructure."""

from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, TypeVar


class TaskBusyError(Exception):
    """Another worker owns this task or all task connections are busy."""


class TaskConflictError(Exception):
    """A retry key was reused with a changed user request."""


class TaskSession(Protocol):
    """Persist state while the storage adapter holds exclusive task access."""

    async def load(self) -> dict[str, Any] | None:
        """Read the saved continuation.

        Returns:
            Opaque application state, or None before the first checkpoint.
        """
        ...

    async def save(self, state: dict[str, Any]) -> None:
        """Save the exact continuation before its next action.

        Args:
            state: Application-owned continuation within the storage budget.
        """
        ...

    async def set_status(self, status: str) -> None:
        """Persist the observed task state.

        Args:
            status: Run outcome or infrastructure interruption status.
        """
        ...


Session = TypeVar("Session", bound=TaskSession, covariant=True)


class TaskStore(Protocol[Session]):
    """Create tasks and supply sessions with exclusive execution access."""

    async def create(self, identity: tuple[int, int], payload: dict[str, Any]) -> str:
        """Create a task or find the same task for an unchanged retry key.

        Args:
            identity: Authenticated user and owned conversation.
            payload: Original application input and retry identity.

        Returns:
            The stable stored task identity.
        """
        ...

    def open(self, user_id: int, task_id: str) -> AbstractAsyncContextManager[Session]:
        """Acquire exclusive task access and recheck saved source access.

        Args:
            user_id: Authenticated task owner.
            task_id: Original stored identity.

        Returns:
            An owned session released when its execution scope ends.

        Raises:
            TaskBusyError: Task execution or storage capacity is busy.
        """
        ...


class TaskReader(Protocol):
    """Expose owned progress without exposing the stored continuation."""

    async def view(self, user_id: int, task_id: str) -> dict[str, Any]:
        """Read progress and authoritative operation evidence.

        Args:
            user_id: Authenticated task owner.
            task_id: Original stored identity.

        Returns:
            Public progress without raw model state or foreign receipts.
        """
        ...

    async def list_session(
        self, user_id: int, session_id: int, *, offset: int = 0
    ) -> dict[str, Any]:
        """Find recent tasks in an owned conversation.

        Args:
            user_id: Authenticated owner.
            session_id: Owned conversation identity.
            offset: Number of task summaries already inspected.

        Returns:
            Bounded summaries and a continuation offset.
        """
        ...


class OperationDecisions(Protocol):
    """Route human decisions to the owner of the exact stored proposal."""

    async def list_pending(self, user_id: int) -> list[dict[str, Any]]:
        """Read proposals available to the authenticated human.

        Args:
            user_id: Authenticated decision maker.

        Returns:
            Exact current proposals and their identities.
        """
        ...

    async def approve(self, user_id: int, approval_id: str) -> dict[str, Any]:
        """Approve the stored proposal under fresh execution checks.

        Args:
            user_id: Authenticated decision maker.
            approval_id: Exact identity presented to the human.

        Returns:
            The recorded decision and checked execution evidence.
        """
        ...

    async def reject(self, user_id: int, approval_id: str) -> dict[str, Any]:
        """Reject the stored proposal without executing it.

        Args:
            user_id: Authenticated decision maker.
            approval_id: Exact identity presented to the human.

        Returns:
            The recorded rejection.
        """
        ...
