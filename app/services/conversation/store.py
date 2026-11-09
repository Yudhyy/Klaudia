"""Session and message storage required by authenticated application callers."""

from typing import Any, Protocol


class ConversationStore(Protocol):
    """Keep session history independent of document extraction and infrastructure.

    Callers must check session ownership before reading or writing a selected
    session. Recent history stays newest first; session views stay oldest first.
    """

    async def get_session_owner(self, session_id: int) -> int | None:
        """Return the user that owns a session."""
        ...

    async def create_session(
        self, user_id: int, session_name: str | None = None
    ) -> int:
        """Create a session and return its ID."""
        ...

    async def get_conversation_history(
        self, session_id: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Return recent session messages in reverse time order."""
        ...

    async def save_message(
        self,
        session_id: int,
        user_id: int,
        sender: str,
        message_text: str,
        file_id: int | None = None,
    ) -> str:
        """Store a conversation message and return its ID."""
        ...

    async def update_session_timestamp(self, session_id: int) -> None:
        """Mark a session as recently updated."""
        ...

    async def get_sessions(self, user_id: int) -> list[dict[str, Any]]:
        """Return a user's sessions in reverse update order."""
        ...

    async def get_session_messages(self, session_id: int) -> list[dict[str, Any]]:
        """Return session messages in chronological order."""
        ...
