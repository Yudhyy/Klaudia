"""Register accounts and authenticate credentials through supplied capabilities."""

from identity_core.contracts import (
    AccountCreationError,
    AuthenticatedAccount,
    InvalidCredentialsError,
    Login,
    Registration,
    UsernameTakenError,
)
from identity_core.ports import IdentityStore, PasswordHasher, TokenIssuer


class IdentityService:
    """Keep account decisions and token issuance order independent of adapters."""

    def __init__(
        self, store: IdentityStore, passwords: PasswordHasher, tokens: TokenIssuer
    ) -> None:
        """Retain the caller's account, password and token capabilities.

        Args:
            store: Account storage owned by application composition.
            passwords: Password hashing and verification implementation.
            tokens: Access-token issuer configured by the application.
        """
        self._store = store
        self._passwords = passwords
        self._tokens = tokens

    async def register(self, request: Registration) -> AuthenticatedAccount:
        """Save an account with a password hash, then issue its access token.

        Args:
            request: Registration fields validated by the caller.

        Returns:
            Created account identity and access token.

        Raises:
            UsernameTakenError: The username already exists.
            AccountCreationError: Password hashing or the account write failed.
            Exception: Account lookup or token issuance failed.
        """
        if await self._store.get_user_by_username(request.username) is not None:
            raise UsernameTakenError
        try:
            user_id = await self._store.create_user(
                username=request.username,
                email=request.email,
                password_hash=self._passwords.hash(request.password),
            )
        except Exception:
            raise AccountCreationError from None
        return AuthenticatedAccount(
            user_id, request.username, self._tokens.issue(user_id)
        )

    async def login(self, request: Login) -> AuthenticatedAccount:
        """Record a successful credential check before issuing an access token.

        Args:
            request: Username and password supplied by the caller.

        Returns:
            Stored account identity and access token.

        Raises:
            InvalidCredentialsError: The account or matching password is absent.
            Exception: A supplied capability failed.
        """
        user = await self._store.get_user_by_username(request.username)
        if user is None or not self._passwords.verify(
            request.password, user["password_hash"]
        ):
            raise InvalidCredentialsError
        await self._store.update_last_login(user["user_id"])
        return AuthenticatedAccount(
            user["user_id"], user["username"], self._tokens.issue(user["user_id"])
        )
