"""End-to-end async pipeline: orchestrator enqueue → Taskiq worker → pubsub.

Spawns a real worker subprocess against a test Redis DB, enqueues an image
extraction, and asserts the orchestrator stream emits a `page_done` event
plus persists the extraction to the `pages` table. This is the smoke test
that proves Phase 3 wiring works before pointing at vLLM.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from app.models.attachment import FileAttachment
from tests.integration.postgres import POSTGRES_TEST_URL


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SAMPLE_IMAGE = _PROJECT_ROOT / "sample-data" / "receipt" / "001-receipt.jpeg"


def _isolated_env(suffix: str) -> dict[str, str]:
    """Build isolated infrastructure settings for the worker test."""
    return {
        "MOCK_KIE": "true",
        "EXTRACTION_MODE": "async",
        "DATABASE_URL": POSTGRES_TEST_URL,
        "MINIO_BUCKET": f"klaudia-test-{suffix}",
        # Isolate Redis namespaces per-run: cache=10, broker=11, results=12
        "REDIS_URL": "redis://localhost:6379/10",
        "TASKIQ_BROKER_URL": "redis://localhost:6379/11",
        "TASKIQ_RESULT_BACKEND_URL": "redis://localhost:6379/12",
        "TASKIQ_QUEUE_NAME": f"ocr:test:{suffix}",
        # Cut langfuse / GCP from worker env to avoid noise
        "LANGFUSE_ENABLED": "false",
        "GOOGLE_GENAI_USE_VERTEXAI": "False",
        "GOOGLE_APPLICATION_CREDENTIALS": "",
    }


@pytest.mark.asyncio
async def test_async_image_extraction_via_worker(postgres_db, monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    env_overrides = _isolated_env(suffix)
    env = {**os.environ, **env_overrides}

    # Skip cleanly if Redis or MinIO aren't reachable — this is an opt-in
    # integration test, not a unit test.
    import redis.asyncio as aioredis

    try:
        for db in (10, 11, 12):
            c = aioredis.from_url(f"redis://localhost:6379/{db}", decode_responses=True)
            try:
                await c.flushdb()
            finally:
                await c.aclose()
    except Exception:
        pytest.skip("Redis not reachable on localhost:6379")

    import httpx

    try:
        async with httpx.AsyncClient(timeout=2.0) as hc:
            await hc.get("http://127.0.0.1:9000/health")
    except Exception:
        pytest.skip("S3 not reachable on http://127.0.0.1:9000")

    # Spawn worker subprocess
    worker = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "taskiq",
            "worker",
            "app.services.extraction.queue.broker:broker",
            "app.services.extraction.queue.tasks",
            "app.services.extraction.queue.state",
            "--workers",
            "1",
            "--max-async-tasks",
            "4",
            "--log-level",
            "WARNING",
        ],
        cwd=str(_PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Give worker time to boot (DB+MinIO+Redis+OCR mock fixture build)
    await asyncio.sleep(8)
    if worker.poll() is not None:
        out = worker.stdout.read().decode(errors="ignore") if worker.stdout else ""
        pytest.fail(f"Worker died early. Output:\n{out[-2000:]}")

    try:
        # Apply env overrides to the test process too — get_settings() reads
        # os.environ, and without these the parent would point at the live
        # cache and dedup against it.
        for key, value in env_overrides.items():
            monkeypatch.setenv(key, value)

        from importlib import reload

        import config.settings as settings_mod

        settings_mod.get_settings.cache_clear()
        settings = settings_mod.get_settings()

        from app.services.extraction.infra.dedup_cache import DedupCache
        from app.services.extraction.infra.object_store import MinIOClient
        from app.services.extraction.infra.kie_client import KIEClient
        from app.services.extraction.ingest import IngestService

        # IMPORTANT: orchestrator needs the same broker module the worker uses,
        # but on the FastAPI side we just call ingest.enqueue which kicks tasks
        # via Taskiq's HTTP/Redis broker — no separate broker init needed.
        # Reload the broker module to pick up the test TASKIQ_BROKER_URL env.
        import app.services.extraction.queue.broker as broker_mod

        reload(broker_mod)
        import app.services.extraction.queue.tasks as tasks_mod

        reload(tasks_mod)

        db = postgres_db
        sid = await db.fetchval(
            "INSERT INTO session (user_id) VALUES (1) RETURNING session_id"
        )
        cache = DedupCache(settings)
        await cache.connect()
        store = MinIOClient(settings)
        await store.ensure_bucket()
        ocr = KIEClient(settings)

        ingest = IngestService(
            settings=settings, db=db, cache=cache, store=store, ocr=ocr
        )

        att = FileAttachment(
            filename="001-receipt.jpeg",
            content_type="image/jpeg",
            data=_SAMPLE_IMAGE.read_bytes(),
        )

        # Need broker startup so kicker can dispatch
        await tasks_mod.broker.startup()

        ef = await ingest.enqueue(att, session_id=sid, user_id=1)
        assert ef.queued == 1
        assert ef.cached_hits == 0

        # Subscribe to progress; wait up to 30s
        from app.services.extraction.queue.progress import stream_progress

        events = []
        async for ev in stream_progress(
            cache.client, file_pages={ef.file_id: 1}, timeout_seconds=30.0
        ):
            events.append(ev)

        assert len(events) == 1
        assert events[0].status in ("extracted", "cached")
        assert events[0].page == 1

        # Verify worker persisted the extraction to LLM-visible pages table
        page_row = await db.fetchone(
            "SELECT agent_extracted, status FROM pages WHERE metadata_file_id = $1",
            (ef.file_id,),
        )
        assert page_row is not None
        assert page_row["status"] == "extracted"
        assert "ALFAMIDI CAWANG BARU" in (page_row["agent_extracted"] or "")

        await tasks_mod.broker.shutdown()
        await ocr.shutdown()
        await cache.close()

    finally:
        if worker.poll() is None:
            worker.send_signal(signal.SIGTERM)
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
