"""Build the existing MCP HTTP token verifier."""

import os

from fastmcp.server.auth.providers.jwt import JWTVerifier


def _build_auth() -> JWTVerifier | None:
    """Build HTTP bearer-token verification from deployment settings.

    Returns:
        An HS256 verifier when MCP_JWT_SECRET is set, otherwise None.

    Raises:
        RuntimeError: If the configured shared secret is too short.
    """
    secret = os.environ.get("MCP_JWT_SECRET", "")
    if not secret:
        return None
    if len(secret) < 32:
        raise RuntimeError("MCP_JWT_SECRET must contain at least 32 characters")
    return JWTVerifier(
        public_key=secret,
        issuer=os.environ.get("MCP_JWT_ISSUER") or None,
        audience=os.environ.get("MCP_JWT_AUDIENCE") or None,
        algorithm="HS256",
    )
