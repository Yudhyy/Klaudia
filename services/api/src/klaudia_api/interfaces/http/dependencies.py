"""FastAPI auth dependency: Bearer JWT -> user_id.

Every protected route depends on get_current_user, so identity always comes
from the signed token and never from the request body.
"""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from klaudia_api.adapters.auth.tokens import InvalidTokenError, decode_access_token
from klaudia_api.composition.settings import get_settings

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> int:
    """Resolve the authenticated user id from the Authorization header.

    Args:
        credentials: Parsed "Authorization: Bearer <jwt>" header, if present.

    Returns:
        The user id encoded in the token.

    Raises:
        HTTPException: 401 when the header is missing or the token is
            invalid or expired.
    """
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return decode_access_token(
            credentials.credentials, secret=get_settings().jwt_secret
        )
    except InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
