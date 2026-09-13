"""Connection contract shared by pools and caller-owned transactions."""

from typing import AsyncContextManager, Protocol

import asyncpg


class ConnectionProvider(Protocol):
    """Supply a connection without requiring a separately allocated pool."""

    def acquire(self) -> AsyncContextManager[asyncpg.Connection]:
        """Enter the provider's connection scope.

        Returns:
            Context manager yielding a connection for a short transaction.
        """
        ...
