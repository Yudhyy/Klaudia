"""Opt-in service roundtrips without model calls or datastore resets."""

import os
from uuid import uuid4

import asyncpg
import pytest

from klaudia_api.adapters.documents.cache.redis import DedupCache
from klaudia_api.adapters.documents.object_store.s3 import MinIOClient
from klaudia_api.composition.settings import Settings, get_settings
from tests.e2e.sandbox import sandbox_enabled

pytestmark = pytest.mark.skipif(
    os.environ.get("E2E_INFRA_CHECK") != "1",
    reason="Set E2E_INFRA_CHECK=1 to check sandbox services without model calls",
)


@pytest.fixture(scope="module")
def sandbox_settings() -> Settings:
    """Require the active sandbox binding before opening any service connection.

    Returns:
        Settings that match the environment rebound by the e2e conftest.

    Raises:
        RuntimeError: Sandbox mode is off or settings predate the binding.
    """
    if os.environ.get("E2E_SANDBOX_ACTIVE") != "1" or not sandbox_enabled():
        raise RuntimeError("Service checks require the isolated sandbox")
    settings = get_settings()
    for field in ("database_url", "redis_url", "minio_bucket"):
        if getattr(settings, field) != os.environ[field.upper()]:
            raise RuntimeError("Service settings do not match the active sandbox")
    return settings


async def test_sandbox_postgres_read(sandbox_settings):
    """Check PostgreSQL without schema changes, fixture rows or table resets."""
    connection = await asyncpg.connect(sandbox_settings.database_url, timeout=5)
    try:
        async with connection.transaction(readonly=True):
            assert await connection.fetchval("SELECT 1") == 1
    finally:
        await connection.close()


async def test_sandbox_redis_roundtrip(sandbox_settings):
    """Check the app cache adapter and delete only its unique probe key."""
    cache = DedupCache(sandbox_settings)
    probe_id = uuid4().hex
    payload = {"probe": probe_id}
    try:
        await cache.connect()
        assert await cache.get_extraction(0, probe_id) is None
        try:
            await cache.set_extraction(0, probe_id, payload)
            assert await cache.get_extraction(0, probe_id) == payload
        finally:
            await cache.delete_extraction(0, probe_id)
        assert await cache.get_extraction(0, probe_id) is None
    finally:
        await cache.close()


async def test_sandbox_minio_roundtrip(sandbox_settings):
    """Check upload, download and removal of one uniquely named sandbox object."""
    store = MinIOClient(sandbox_settings)
    key = f"service-check/{uuid4().hex}.txt"
    payload = b"Klaudia sandbox service probe"
    await store.ensure_bucket()
    assert not await store.exists(key)
    try:
        stored = await store.put(key, payload, content_type="text/plain")
        assert stored.size_bytes == len(payload)
        assert await store.get(key) == payload
    finally:
        await store.delete(key)
    assert not await store.exists(key)
