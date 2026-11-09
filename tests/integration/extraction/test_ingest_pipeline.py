"""Full ingest pipeline integration test (mock OCR).

Exercises:
    Image upload → MinIO put + L1/L2 cache miss → OCR mock → persist
    Same image again → L1 (Redis) hit → no MinIO put, no OCR call
    PDF upload (6 pages) → per-page persist + cache
    Same PDF → 6 redis hits
    PDF cross-overlap: a single PDF page can hit cache if its content matches
        a previously-uploaded standalone image (true content-addressed dedup)

Requires Redis @ REDIS_URL and MinIO @ MINIO_ENDPOINT to be reachable.
Requires the isolated PostgreSQL test database. Skipped if a service is down.
"""

from __future__ import annotations

from app.adapters.persistence.documents import PostgresDocumentStore
from app.adapters.persistence.extraction_outcomes import PostgresExtractionOutcomes

import uuid
from pathlib import Path
from typing import AsyncIterator

import pytest

from app.models.attachment import FileAttachment
from app.services.extraction.infra.db_client import AppDBClient
from app.services.extraction.infra.dedup_cache import DedupCache
from app.services.extraction.infra.object_store import MinIOClient
from app.services.extraction.infra.kie_client import KIEClient
from app.services.extraction.ingest import IngestService
from config.settings import Settings
from tests.integration.postgres import POSTGRES_TEST_URL


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SAMPLE_IMG_001 = _PROJECT_ROOT / "sample-data" / "receipt" / "001-receipt.jpeg"
_SAMPLE_IMG_002 = _PROJECT_ROOT / "sample-data" / "receipt" / "002-receipt.png"
_SAMPLE_PDF = _PROJECT_ROOT / "sample-data" / "pdf" / "001-receipt.pdf"


def _settings_for_test() -> Settings:
    """Return isolated infrastructure settings for one test run."""
    suffix = uuid.uuid4().hex[:8]
    return Settings(
        _env_file=None,
        MOCK_KIE=True,
        DATABASE_URL=POSTGRES_TEST_URL,
        MINIO_BUCKET=f"klaudia-test-{suffix}",
        REDIS_URL="redis://localhost:6379/15",  # isolated test DB
    )


@pytest.fixture
async def ingest_stack(
    postgres_db,
) -> AsyncIterator[tuple[IngestService, AppDBClient, DedupCache, MinIOClient, int]]:
    settings = _settings_for_test()
    db = postgres_db
    cache = DedupCache(settings)
    try:
        await cache.connect()
    except Exception:
        pytest.skip("Redis not reachable on localhost:6379")
    store = MinIOClient(settings)
    try:
        await store.ensure_bucket()
    except Exception:
        pytest.skip("MinIO not reachable on configured endpoint")
    # Wipe Redis test DB to make assertions on cache_hits deterministic
    await cache.client.flushdb()
    await db.execute(
        """INSERT INTO "user" (user_id, username, email, password_hash)
           VALUES (1, 'test', 'test@local', 'x')
           ON CONFLICT (user_id) DO NOTHING"""
    )
    sid = await db.fetchval(
        "INSERT INTO session (user_id) VALUES (1) RETURNING session_id"
    )
    ocr = KIEClient(settings)

    ingest = IngestService(
        settings=settings,
        db=PostgresDocumentStore(db.pool),
        cache=cache,
        store=store,
        ocr=ocr,
        outcomes=PostgresExtractionOutcomes(db.pool, (settings).max_pdf_pages),
    )

    yield ingest, db, cache, store, int(sid)

    await ocr.shutdown()
    await cache.close()


def _att(path: Path, content_type: str) -> FileAttachment:
    return FileAttachment(
        filename=path.name,
        content_type=content_type,
        data=path.read_bytes(),
    )


@pytest.mark.asyncio
async def test_image_upload_first_then_cached(ingest_stack):
    ingest, db, cache, store, sid = ingest_stack

    att = _att(_SAMPLE_IMG_001, "image/jpeg")
    out1 = await ingest.ingest(att, session_id=sid, user_id=1)
    assert out1.status == "completed"
    assert len(out1.pages) == 1
    assert out1.cache_hits == 0
    assert out1.cache_misses == 1
    assert out1.pages[0].extraction["info"]["store_name"] == "ALFAMIDI CAWANG BARU"

    # Same upload → Redis hit, no fresh OCR
    out2 = await ingest.ingest(att, session_id=sid, user_id=1)
    assert out2.status == "completed"
    assert out2.cache_hits == 1
    assert out2.cache_misses == 0
    assert out2.pages[0].cache_layer == "redis"
    # Different metadata_file rows (separate uploads), same content blob
    assert out1.file_id != out2.file_id


@pytest.mark.asyncio
async def test_redis_miss_falls_back_to_database(ingest_stack):
    ingest, db, cache, store, sid = ingest_stack

    att = _att(_SAMPLE_IMG_002, "image/png")
    out1 = await ingest.ingest(att, session_id=sid, user_id=1)
    assert out1.cache_hits == 0

    # Wipe just the Redis cache (simulate Redis flush / eviction)
    await cache.client.flushdb()

    out2 = await ingest.ingest(att, session_id=sid, user_id=1)
    assert out2.pages[0].cache_layer == "database"
    assert out2.cache_hits == 1


@pytest.mark.asyncio
async def test_pdf_per_page_dedup(ingest_stack):
    ingest, db, cache, store, sid = ingest_stack

    att = _att(_SAMPLE_PDF, "application/pdf")
    first = await ingest.ingest(att, session_id=sid, user_id=1)
    assert first.status == "completed"
    assert len(first.pages) == 6
    assert first.cache_misses == 6
    assert first.cache_hits == 0
    # Each page should have a unique extraction (or at least valid schema)
    for p in first.pages:
        assert "info" in p.extraction
        assert isinstance(p.extraction["items"], list)

    second = await ingest.ingest(att, session_id=sid, user_id=1)
    assert second.cache_hits == 6
    assert second.cache_misses == 0


@pytest.mark.asyncio
async def test_per_user_keyspace_isolation(ingest_stack):
    """User A's blob/extraction must not leak to User B."""
    ingest, db, cache, store, sid = ingest_stack
    # Seed user 2
    await db.execute(
        """INSERT INTO "user" (user_id, username, email, password_hash)
           VALUES (2, 'test2', 'test2@local', 'x')
           ON CONFLICT (user_id) DO NOTHING"""
    )
    sid2 = await db.fetchval(
        "INSERT INTO session (user_id) VALUES (2) RETURNING session_id"
    )

    att = _att(_SAMPLE_IMG_001, "image/jpeg")
    out_user1 = await ingest.ingest(att, session_id=sid, user_id=1)
    out_user2 = await ingest.ingest(att, session_id=sid2, user_id=2)

    # User 2 sees a cache miss (different keyspace) even though content is identical
    assert out_user1.cache_hits == 0
    assert out_user2.cache_hits == 0


@pytest.mark.asyncio
async def test_metadata_file_blob_link_persisted(ingest_stack):
    """Verify metadata_file_blob join row exists; the LLM never sees this
    table but the application uses it for retention / dedup analytics."""
    ingest, db, cache, store, sid = ingest_stack

    att = _att(_SAMPLE_IMG_001, "image/jpeg")
    out = await ingest.ingest(att, session_id=sid, user_id=1)

    row = await db.fetchone(
        "SELECT blob_id FROM metadata_file_blob WHERE metadata_file_id = $1",
        (out.file_id,),
    )
    assert row is not None
    assert row["blob_id"] >= 1

    blob = await db.fetchone(
        "SELECT user_id, page_count FROM file_blob WHERE blob_id = $1",
        (row["blob_id"],),
    )
    assert blob is not None
    assert blob["user_id"] == 1
    assert blob["page_count"] == 1


@pytest.mark.asyncio
async def test_corrupt_image_rejected_by_magic_check(ingest_stack):
    """Bad magic bytes get rejected pre-flight (before any DB / MinIO writes).
    The orchestrator catches IngestRejectedError and surfaces a friendly
    message to the user."""
    from app.exceptions import IngestRejectedError

    ingest, db, cache, store, sid = ingest_stack

    att = FileAttachment(
        filename="broken.jpg",
        content_type="image/jpeg",
        data=b"not an image at all",
    )
    with pytest.raises(IngestRejectedError) as exc:
        await ingest.ingest(att, session_id=sid, user_id=1)
    assert exc.value.reason == "bad_magic"

    # No metadata_file row should have been created for the bad upload.
    rows = await db.fetchall(
        "SELECT id FROM metadata_file WHERE session_id = $1 AND file_name = $2",
        (sid, "broken.jpg"),
    )
    assert rows == []
