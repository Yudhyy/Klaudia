"""Compatibility imports for the application access-token adapter."""

from klaudia_api.adapters.auth.tokens import (
    DEFAULT_EXPIRES,
    InvalidTokenError,
    create_access_token,
    decode_access_token,
)

__all__ = [
    "DEFAULT_EXPIRES",
    "InvalidTokenError",
    "create_access_token",
    "decode_access_token",
]
