"""Account, password and token capabilities required by identity use cases."""

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


class PasswordHasher(Protocol):
    """Hash new passwords and verify saved password hashes."""

    def hash(self, password: str) -> str:
        """Create a stored hash from a plaintext password.

        Args:
            password: Plaintext account password.

        Returns:
            Hash suitable for account storage.
        """
        ...

    def verify(self, password: str, password_hash: str) -> bool:
        """Check a plaintext password against the saved hash.

        Args:
            password: Candidate password.
            password_hash: Saved account hash.

        Returns:
            True when the password matches.
        """
        ...


class TokenIssuer(Protocol):
    """Issue access tokens for authenticated account identities."""

    def issue(self, user_id: int) -> str:
        """Issue a token for the supplied account ID.

        Args:
            user_id: Account ID established by the use case.

        Returns:
            Encoded access token.
        """
        ...
