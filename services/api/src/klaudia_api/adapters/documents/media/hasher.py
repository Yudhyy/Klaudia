"""BLAKE3 hashing for content-addressed dedup.

Hashes are an internal identifier. They never appear in agent prompts or in
tables exposed via MCP-Archive tools. Storage layer uses them as keys; the
LLM only ever sees `metadata_file.id` / `pages.id`.
"""

from __future__ import annotations

from blake3 import blake3

# 32 bytes -> 64 hex chars; cheap to index, fits in a TEXT column.
HEX_DIGEST_LEN = 64


def hash_bytes(data: bytes) -> str:
    """Return BLAKE3 hex digest of `data`."""
    return blake3(data).hexdigest()


def shard_path(digest: str, *, depth: int = 2) -> str:
    """Two-level shard: 'ab/cd' from 'abcd…'. Keeps any single MinIO prefix
    listing bounded even at millions of objects.
    """
    if len(digest) < depth * 2:
        raise ValueError(f"digest too short for shard depth {depth}: {digest!r}")
    parts = [digest[i * 2 : i * 2 + 2] for i in range(depth)]
    return "/".join(parts)
