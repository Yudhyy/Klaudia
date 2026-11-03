"""Identity persistence required by registration and login."""

from typing import Protocol, TypedDict


class UserCredentials(TypedDict):
    """Credential fields returned by the existing account lookup."""

    user_id: int
    username: str
    email: str
    password_hash: str


class IdentityStore(Protocol):
    """Read and write accounts without exposing a database client."""

    async def get_user_by_username(self, username: str) -> UserCredentials | None:
        """Read credentials for an exact username.

        Args:
            username: Account name supplied by the caller.

        Returns:
            Stored credentials, or None when the account does not exist.
        """
        ...

    async def create_user(self, username: str, email: str, password_hash: str) -> int:
        """Save an account with an already hashed password.

        Args:
            username: Account name.
            email: Account email address.
            password_hash: Hash produced by the authentication service.

        Returns:
            Generated account ID.
        """
        ...

    async def update_last_login(self, user_id: int) -> None:
        """Record a successful login for an account.

        Args:
            user_id: Authenticated account ID.
        """
        ...
