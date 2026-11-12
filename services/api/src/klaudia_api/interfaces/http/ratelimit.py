"""Edge rate limiting (slowapi).

Auth endpoints are keyed by client IP (pre-auth brute-force protection);
chat endpoints are keyed by authenticated user id so one user cannot burn
the deployment's LLM budget. Storage is in-process moving-window: no
network hop on the hot path. Per-node limits are the accepted trade-off
until the multi-node gateway phase.
"""

from fastapi import FastAPI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request

from klaudia_api.adapters.auth.tokens import InvalidTokenError, decode_access_token
from klaudia_api.composition.settings import get_settings


def user_or_ip(request: Request) -> str:
    """Rate-limit key: authenticated user id when available, else client IP.

    Args:
        request: Incoming HTTP request.

    Returns:
        "user:<id>" for a valid bearer token, otherwise the remote address.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        try:
            user_id = decode_access_token(
                auth_header[7:], secret=get_settings().jwt_secret
            )
            return f"user:{user_id}"
        except InvalidTokenError:
            pass
    return get_remote_address(request)


def auth_limit() -> str:
    """Current per-IP limit for auth endpoints (read per request)."""
    return get_settings().rate_limit_auth


def chat_limit() -> str:
    """Current per-user limit for chat endpoints (read per request)."""
    return get_settings().rate_limit_chat


limiter = Limiter(key_func=user_or_ip, headers_enabled=True)


def attach_rate_limiter(app: FastAPI) -> None:
    """Wire the shared limiter into an app (main app and test apps alike).

    Args:
        app: FastAPI application to attach limiter state and the 429
            exception handler to.
    """
    limiter.enabled = get_settings().rate_limit_enabled
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
