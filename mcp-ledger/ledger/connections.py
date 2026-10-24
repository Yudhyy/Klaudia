"""Connection contract shared by pools and caller-owned transactions."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, AsyncContextManager, Protocol

import asyncpg


class ConnectionProvider(Protocol):
    """Supply a connection without requiring a separately allocated pool."""

    def acquire(self) -> AsyncContextManager[asyncpg.Connection]:
        """Enter the provider's connection scope.

        Returns:
            Context manager yielding a connection for a short transaction.
        """
        ...


class BorrowedConnection:
    """Use a caller-held connection without another pool acquisition."""

    def __init__(self, connection: asyncpg.Connection) -> None:
        """Retain the connection owned by the surrounding transaction."""
        self._connection = connection

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        """Yield the held connection without releasing or committing it."""
        yield self._connection

    async def fetch(self, query: str, *arguments: Any) -> list[asyncpg.Record]:
        """Read rows on the caller's connection."""
        return await self._connection.fetch(query, *arguments)

    async def fetchrow(self, query: str, *arguments: Any) -> asyncpg.Record | None:
        """Read one row on the caller's connection."""
        return await self._connection.fetchrow(query, *arguments)

    async def fetchval(self, query: str, *arguments: Any) -> Any:
        """Read one value on the caller's connection."""
        return await self._connection.fetchval(query, *arguments)

    async def execute(self, query: str, *arguments: Any) -> str:
        """Execute a statement inside the caller's existing transaction."""
        return await self._connection.execute(query, *arguments)
