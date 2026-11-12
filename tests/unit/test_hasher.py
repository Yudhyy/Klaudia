"""BLAKE3 hasher unit tests."""

import pytest

from klaudia_api.adapters.documents.media.hasher import (
    HEX_DIGEST_LEN,
    hash_bytes,
    shard_path,
)


def test_hash_bytes_deterministic():
    """Same input -> same digest."""
    data = b"hello world"
    assert hash_bytes(data) == hash_bytes(data)


def test_hash_bytes_returns_64_hex_chars():
    digest = hash_bytes(b"x")
    assert len(digest) == HEX_DIGEST_LEN
    assert all(c in "0123456789abcdef" for c in digest)


def test_hash_bytes_different_inputs_differ():
    assert hash_bytes(b"a") != hash_bytes(b"b")


def test_shard_path_default_depth_2():
    assert shard_path("abcdef0123456789" * 4) == "ab/cd"


def test_shard_path_custom_depth():
    digest = "0011223344556677" * 4
    assert shard_path(digest, depth=3) == "00/11/22"


def test_shard_path_rejects_short_digest():
    with pytest.raises(ValueError):
        shard_path("ab", depth=2)
