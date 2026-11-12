"""Redis hot cache for blob metadata + per-page extraction JSON.

Two cache layers protect the extraction model:
    L1 (this) Redis hash, ~ms latency, capped TTL
    L2        PostgreSQL blob_extraction table, persistent

On miss in L1, IngestService reads L2; on hit there it warms L1. Pure cache —
authority is PostgreSQL. Per-user keyspace prevents cross-tenant reuse.

Keys (per-user):
    blob:u<uid>:<blake3>           hash of blob metadata (json)
    extract:u<uid>:<page_hash>     hash of extraction json
    queue:depth                    integer, set by Taskiq (Phase 3)
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from klaudia_api.composition.settings import Settings

logger = logging.getLogger(__name__)


class DedupCache:
    """Thin async Redis wrapper. Connection pool is reused across calls."""

    def __init__(self, settings: Settings) -> None:
        self._url = settings.redis_url
        self._ttl = settings.dedup_cache_ttl_seconds
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._client = aioredis.from_url(
            self._url,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
        await self._client.ping()
        logger.info("Redis dedup cache connected: %s", self._url)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError("DedupCache not connected")
        return self._client

    # ── Blob metadata ────────────────────────────────────────────────────

    @staticmethod
    def _blob_key(user_id: int, blake3_hex: str) -> str:
        return f"blob:u{user_id}:{blake3_hex}"

    async def get_blob(self, user_id: int, blake3_hex: str) -> dict[str, Any] | None:
        raw = await self.client.get(self._blob_key(user_id, blake3_hex))
        return json.loads(raw) if raw else None

    async def set_blob(
        self, user_id: int, blake3_hex: str, payload: dict[str, Any]
    ) -> None:
        await self.client.set(
            self._blob_key(user_id, blake3_hex),
            json.dumps(payload, ensure_ascii=False),
            ex=self._ttl,
        )

    # ── Per-page extraction JSON ────────────────────────────────────────

    @staticmethod
    def _extract_key(user_id: int, page_blake3: str) -> str:
        return f"extract:u{user_id}:{page_blake3}"

    async def get_extraction(
        self, user_id: int, page_blake3: str
    ) -> dict[str, Any] | None:
        raw = await self.client.get(self._extract_key(user_id, page_blake3))
        return json.loads(raw) if raw else None

    async def set_extraction(
        self, user_id: int, page_blake3: str, extraction: dict[str, Any]
    ) -> None:
        await self.client.set(
            self._extract_key(user_id, page_blake3),
            json.dumps(extraction, ensure_ascii=False),
            ex=self._ttl,
        )

    async def delete_extraction(self, user_id: int, page_blake3: str) -> None:
        """Drop the L1 extraction entry so the next ingest re-runs KIE.

        Also clears the blob-metadata entry for the same hash. Used by the E2E
        harness to make a cache-miss case reproducible across runs.
        """
        await self.client.delete(
            self._extract_key(user_id, page_blake3),
            self._blob_key(user_id, page_blake3),
        )

    # ── Queue depth (read-only here; Taskiq publishes) ──────────────────

    async def queue_depth(self, queue_name: str) -> int:
        """LLEN on the Taskiq Redis list. Best-effort; returns 0 on miss."""
        try:
            return int(await self.client.llen(queue_name))
        except Exception:  # pragma: no cover
            return 0
