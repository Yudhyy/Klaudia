"""Persistence contract for a bounded agent continuation."""

from typing import Any, Protocol


class CheckpointStore(Protocol):
    """Persist one task's model messages and pending tool calls under exclusive ownership."""

    async def load(self) -> dict[str, Any] | None:
        """Read the last durable continuation.

        Returns:
            Stored checkpoint or None for a new task.
        """
        ...

    async def save(self, state: dict[str, Any]) -> None:
        """Replace the checkpoint before permitting the next external action.

        Args:
            state: Bounded server-generated continuation, never client input.

        Raises:
            Exception: Persistence failed; the agent must stop.
        """
        ...
