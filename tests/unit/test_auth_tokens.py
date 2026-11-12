"""Unit tests for JWT access-token helpers."""

import datetime

import pytest

from app.services.auth.tokens import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
)

_SECRET = "unit-test-secret-with-32-plus-bytes!"


def test_create_and_decode_roundtrip():
    token = create_access_token(user_id=42, secret=_SECRET)
    assert decode_access_token(token, secret=_SECRET) == 42


def test_decode_rejects_wrong_secret():
    token = create_access_token(user_id=42, secret=_SECRET)
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, secret="other-secret")


def test_decode_rejects_expired_token():
    token = create_access_token(
        user_id=42,
        secret=_SECRET,
        expires_delta=datetime.timedelta(seconds=-1),
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, secret=_SECRET)


def test_decode_rejects_garbage():
    with pytest.raises(InvalidTokenError):
        decode_access_token("not.a.jwt", secret=_SECRET)


def test_decode_rejects_token_without_subject():
    import jwt

    token = jwt.encode({"foo": "bar"}, _SECRET, algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, secret=_SECRET)


def test_configured_issuer_retains_claims_algorithm_and_lifetime():
    """Use the supplied secret and lifetime with the existing JWT format."""
    import jwt

    from klaudia_api.adapters.auth.tokens import JwtTokenIssuer

    lifetime = datetime.timedelta(days=3)
    token = JwtTokenIssuer(_SECRET, lifetime).issue(42)
    claims = jwt.decode(token, _SECRET, algorithms=["HS256"])
    assert set(claims) == {"sub", "iat", "exp"}
    assert claims["sub"] == "42"
    assert claims["exp"] - claims["iat"] == lifetime.total_seconds()
    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    assert decode_access_token(token, secret=_SECRET) == 42
