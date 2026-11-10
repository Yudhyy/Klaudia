"""Coordinate durable task execution through supplied application capabilities."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from typing import Any, Generic, TypeVar

from workflow_core.ports import TaskSession, TaskStore

logger = logging.getLogger(__name__)
Session = TypeVar("Session", bound=TaskSession)
Outcome = TypeVar("Outcome")


@dataclass(frozen=True)
class TaskCompletion(Generic[Outcome]):
    """Pair an observed stop status with an application-owned outcome."""

    status: str
    value: Outcome


@dataclass(frozen=True)
class TaskRun(Generic[Outcome]):
    """Bind execution and failure recovery to the same stored task input."""

    run: Callable[[], Awaitable[TaskCompletion[Outcome]]]
    recover: Callable[[], Awaitable[Outcome]]


class WorkflowService(Generic[Session, Outcome]):
    """Keep task lifecycle handling independent of the supplied run implementation."""

    def __init__(
        self, store: TaskStore[Session], bind: Callable[[Session], TaskRun[Outcome]]
    ) -> None:
        """Bind storage and application execution without creating infrastructure.

        Args:
            store: Ownership-checking storage with exclusive execution scopes.
            bind: Restore an application run from the acquired stored session.
        """
        self._store = store
        self._bind = bind

    async def start(
        self, identity: tuple[int, int], payload: dict[str, Any]
    ) -> Outcome:
        """Create or find a task, then execute its persisted input.

        Args:
            identity: Authenticated user and owned conversation.
            payload: Original application input and retry identity.

        Returns:
            The supplied application's outcome for the stored task.
        """
        task_id = await self._store.create(identity, payload)
        return await self.resume(identity[0], task_id)

    async def resume(self, user_id: int, task_id: str) -> Outcome:
        """Run the original task once under exclusive storage access.

        Args:
            user_id: Authenticated owner.
            task_id: Original stored task identity.

        Returns:
            The observed outcome or application-owned recovery evidence.

        Raises:
            BaseException: Cancellation or a failure that cannot be recorded.
        """
        async with self._store.open(user_id, task_id) as session:
            prepared = self._bind(session)
            try:
                completed = await prepared.run()
                await session.set_status(completed.status)
                return completed.value
            except Exception:
                logger.exception("Durable task stopped before a recoverable outcome")
                await session.set_status("failed")
                return await prepared.recover()
            except BaseException:
                try:
                    await session.set_status("interrupted")
                except Exception:
                    logger.exception("Could not record interrupted task status")
                raise
