"""JWT access-token helpers (HS256, self-issued).

The token carries only the user id (`sub`) plus issued-at/expiry claims.
Anything else about the user is looked up server-side, so tokens never go
stale on profile changes.
"""

import datetime

import jwt

DEFAULT_EXPIRES = datetime.timedelta(days=7)
_ALGORITHM = "HS256"


class InvalidTokenError(Exception):
    """Raised when a token is expired, tampered with, or malformed."""


def create_access_token(
    user_id: int,
    secret: str,
    expires_delta: datetime.timedelta = DEFAULT_EXPIRES,
) -> str:
    """Issue a signed access token for a user.

    Args:
        user_id: Authenticated user's id.
        secret: HS256 signing secret.
        expires_delta: Token lifetime; defaults to 7 days.

    Returns:
        Encoded JWT string.
    """
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_access_token(token: str, secret: str) -> int:
    """Validate a token and return the user id it was issued to.

    Args:
        token: Encoded JWT from the Authorization header.
        secret: HS256 signing secret.

    Returns:
        The user id from the `sub` claim.

    Raises:
        InvalidTokenError: If the token is expired, tampered with,
            malformed, or missing a numeric `sub` claim.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as e:
        raise InvalidTokenError(str(e)) from e
    sub = payload.get("sub")
    try:
        return int(sub)
    except (TypeError, ValueError) as e:
        raise InvalidTokenError("token missing numeric sub claim") from e


class JwtTokenIssuer:
    """Issue access tokens with application-supplied signing settings."""

    def __init__(self, secret: str, expires_delta: datetime.timedelta) -> None:
        """Retain the process-owned signing secret and token lifetime.

        Args:
            secret: HS256 signing secret.
            expires_delta: Access-token lifetime.
        """
        self._secret = secret
        self._expires_delta = expires_delta

    def issue(self, user_id: int) -> str:
        """Issue a token with the current access-token claims and algorithm.

        Args:
            user_id: Authenticated account ID.

        Returns:
            Signed HS256 access token.
        """
        return create_access_token(user_id, self._secret, self._expires_delta)
