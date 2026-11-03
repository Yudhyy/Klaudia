"""PostgreSQL identity operations on an application-owned pool."""

import asyncpg

from app.services.auth.store import UserCredentials


class PostgresIdentityStore:
    """Borrow the application pool without owning schema setup or shutdown."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Retain the connected pool supplied by application composition.

        Args:
            pool: Application-owned PostgreSQL pool.
        """
        self._pool = pool

    async def get_user_by_username(self, username: str) -> UserCredentials | None:
        """Read the existing account's credential fields.

        Args:
            username: Exact account name.

        Returns:
            Stored credentials, or None when no account matches.
        """
        account = await self._pool.fetchrow(
            'SELECT user_id, username, email, password_hash FROM "user" '
            "WHERE username = $1",
            username,
        )
        if account is None:
            return None
        return UserCredentials(
            user_id=account["user_id"],
            username=account["username"],
            email=account["email"],
            password_hash=account["password_hash"],
        )

    async def create_user(self, username: str, email: str, password_hash: str) -> int:
        """Insert an account without changing database conflict errors.

        Args:
            username: Account name.
            email: Account email address.
            password_hash: Hash produced by the authentication service.

        Returns:
            Generated account ID.

        Raises:
            asyncpg.UniqueViolationError: The username or email already exists.
        """
        user_id = await self._pool.fetchval(
            'INSERT INTO "user" (username, email, password_hash) '
            "VALUES ($1, $2, $3) RETURNING user_id",
            username,
            email,
            password_hash,
        )
        return int(user_id)

    async def update_last_login(self, user_id: int) -> None:
        """Save the database time for the matching account's login.

        Args:
            user_id: Authenticated account ID.
        """
        await self._pool.execute(
            'UPDATE "user" SET last_login = CURRENT_TIMESTAMP WHERE user_id = $1',
            user_id,
        )
