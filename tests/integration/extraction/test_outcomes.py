"""Durable extraction checks with PostgreSQL and controlled worker timing."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.attachment import FileAttachment
from app.services.core.orchestrator import KlaudiaOrchestrator
from app.services.extraction.ingest import EnqueuedFile, EnqueuedPage, IngestService
from app.services.extraction.outcomes import (
    MAX_EXTRACTION_CHARACTERS,
    ExtractionOutcomes,
    ExtractionScope,
    PageOutcome,
)
from app.services.extraction.queue import tasks
from app.services.extraction.queue.progress import ProgressWait, stream_progress
from config.settings import Settings
from ledger.resources import ResourceNotFoundError


@pytest.fixture
async def prepared_file(postgres_db):
    db = postgres_db
    sid = await db.create_session(1)
    fid = await db.fetchval(
        """INSERT INTO metadata_file (session_id, user_id, type, file_name, total_pages)
           VALUES ($1, 1, 'pdf', 'receipt.pdf', 2) RETURNING id""",
        (sid,),
    )
    blob = await db.insert_blob(
        user_id=1,
        blake3="file-hash",
        minio_key="file.pdf",
        content_type="application/pdf",
        size_bytes=3,
        page_count=2,
    )
    await db.link_metadata_file_blob(fid, blob)
    for page in (1, 2):
        await db.upsert_blob_page(
            blob_id=blob,
            page=page,
            page_blake3=f"hash-{page}",
            page_minio_key=f"page-{page}.jpg",
        )
    return db, ExtractionScope(1, sid, fid), blob


async def file_status(db, scope):
    return await db.fetchval(
        "SELECT status FROM metadata_file WHERE id = $1", (scope.file_id,)
    )


def worker_context(db, monkeypatch):
    cache = SimpleNamespace(
        get_extraction=AsyncMock(return_value=None),
        set_extraction=AsyncMock(),
        client=SimpleNamespace(publish=AsyncMock()),
    )
    ctx = SimpleNamespace(
        db=db,
        cache=cache,
        store=SimpleNamespace(get=AsyncMock(return_value=b"image")),
        ocr=SimpleNamespace(
            extract_from_image=AsyncMock(
                return_value={"info": {"store_name": "Saved shop"}}
            ),
            model_id="mock",
            schema_version="1",
        ),
    )
    monkeypatch.setattr(tasks, "get_context", lambda: ctx)
    return ctx


def orchestrator_for(db, scope, blob, enqueue=None):
    enqueued = EnqueuedFile(
        scope.file_id,
        "receipt.pdf",
        "pdf",
        blob,
        [EnqueuedPage(p, blob, f"hash-{p}", False) for p in (1, 2)],
        2,
        0,
    )
    return KlaudiaOrchestrator(
        SimpleNamespace(
            settings=Settings(_env_file=None, EXTRACTION_PAGE_TIMEOUT_SECONDS=0),
            db_client=db,
            dedup_cache=None,
            ingest_service=SimpleNamespace(
                enqueue=enqueue or AsyncMock(return_value=enqueued)
            ),
            extraction_agent=None,
            langfuse=None,
        )
    )


async def run_extraction(orchestrator, scope):
    return [
        event
        async for event in orchestrator._run_extraction_async(
            [
                FileAttachment(
                    filename="receipt.pdf", content_type="application/pdf", data=b"pdf"
                )
            ],
            scope.session_id,
            scope.user_id,
        )
    ]


async def test_duplicate_rows_and_unexpected_pages_cannot_complete_file(prepared_file):
    db, scope, _ = prepared_file
    for page in (1, 1, 99):
        await db.execute(
            "INSERT INTO pages (metadata_file_id, page, status) VALUES ($1, $2, 'extracted')",
            (scope.file_id, page),
        )
    outcome = await ExtractionOutcomes(db).read(scope)
    assert outcome.pending_pages == (2,)
    assert outcome.status == "pending"
    assert await file_status(db, scope) == "pending"


async def test_failure_and_cache_hit_roll_up_by_distinct_identity(prepared_file):
    db, scope, _ = prepared_file
    service = ExtractionOutcomes(db)
    first = await service.save(
        scope, PageOutcome(2, "cached", {"value": "12.50"}, "redis")
    )
    assert first.pending_pages == (1,)
    final = await service.save(scope, PageOutcome(1, "failed", error="OCR failed"))
    assert final.status == "partial"
    assert final.complete
    assert final.pages[1].cache_layer == "redis"
    assert await file_status(db, scope) == "partial"


async def test_all_failed_pages_are_terminal(prepared_file):
    db, scope, _ = prepared_file
    service = ExtractionOutcomes(db)
    await service.save(scope, PageOutcome(1, "failed", error="bad image"))
    outcome = await service.save(scope, PageOutcome(2, "failed", error="bad image"))
    assert outcome.status == "failed"
    assert outcome.complete
    assert await file_status(db, scope) == "failed"


async def test_concurrent_retries_do_not_duplicate_or_erase_success(prepared_file):
    db, scope, _ = prepared_file
    service = ExtractionOutcomes(db)
    await asyncio.gather(
        *(
            service.save(scope, PageOutcome(1, "extracted", {"amount": "1.00"}))
            for _ in range(4)
        )
    )
    await service.save(scope, PageOutcome(1, "failed", error="late retry"))
    await service.save(scope, PageOutcome(2, "extracted"))
    assert (
        await db.fetchval(
            "SELECT count(*) FROM pages WHERE metadata_file_id = $1", (scope.file_id,)
        )
        == 2
    )
    assert await file_status(db, scope) == "completed"
    assert (await service.read(scope)).pages[0].extraction == {"amount": "1.00"}


@pytest.mark.parametrize("mismatch", ["user", "session", "session_owner", "file_owner"])
async def test_read_and_save_check_file_and_session_ownership(prepared_file, mismatch):
    db, scope, _ = prepared_file
    other = await db.create_user("other", "other@test.local", "unused")
    if mismatch == "user":
        scope = replace(scope, user_id=other)
    elif mismatch == "session":
        scope = replace(scope, session_id=await db.create_session(1))
    elif mismatch == "session_owner":
        await db.execute(
            "UPDATE session SET user_id = $1 WHERE session_id = $2",
            (other, scope.session_id),
        )
    else:
        await db.execute(
            "UPDATE metadata_file SET user_id = $1 WHERE id = $2",
            (other, scope.file_id),
        )
    service = ExtractionOutcomes(db)
    with pytest.raises(ResourceNotFoundError):
        await service.read(scope)
    with pytest.raises(ResourceNotFoundError):
        await service.save(scope, PageOutcome(1, "extracted"))
    assert await db.fetchval("SELECT count(*) FROM pages") == 0
    assert await file_status(db, scope) == "pending"


async def test_worker_rejects_unlinked_blob_before_access(prepared_file, monkeypatch):
    db, scope, blob = prepared_file
    ctx = worker_context(db, monkeypatch)
    with pytest.raises(ResourceNotFoundError):
        await tasks.extract_page_task(
            user_id=1, file_id=scope.file_id, blob_id=blob + 1, page=1
        )
    ctx.store.get.assert_not_awaited()
    ctx.ocr.extract_from_image.assert_not_awaited()


async def test_worker_finished_inside_enqueue_before_subscription(
    prepared_file, monkeypatch
):
    db, scope, blob = prepared_file
    ctx = worker_context(db, monkeypatch)

    async def enqueue(*args, **kwargs):
        for page in (1, 2):
            await tasks.extract_page_task(
                user_id=1, file_id=scope.file_id, blob_id=blob, page=page
            )
        return EnqueuedFile(
            scope.file_id,
            "receipt.pdf",
            "pdf",
            blob,
            [EnqueuedPage(p, blob, f"hash-{p}", False) for p in (1, 2)],
            2,
            0,
        )

    events = await run_extraction(orchestrator_for(db, scope, blob, enqueue), scope)
    assert [
        e["data"]["page"]
        for e in events
        if e.get("data", {}).get("status") == "page_done"
    ] == [1, 2]
    assert events[-1]["payload"][0]["status"] == "completed"
    assert ctx.ocr.extract_from_image.await_count == 2


async def test_timeout_then_late_worker_persists_status_without_api(
    prepared_file, monkeypatch
):
    db, scope, blob = prepared_file
    worker_context(db, monkeypatch)
    await tasks.extract_page_task(
        user_id=1, file_id=scope.file_id, blob_id=blob, page=1
    )
    events = await run_extraction(orchestrator_for(db, scope, blob), scope)
    deferred = events[-2]["data"]
    assert deferred["status"] == "deferred"
    assert deferred["pending_pages"] == [2]
    assert deferred["complete"] is False
    assert events[-1]["payload"][0]["status"] == "pending"
    assert await file_status(db, scope) == "pending"
    await tasks.extract_page_task(
        user_id=1, file_id=scope.file_id, blob_id=blob, page=2
    )
    assert await file_status(db, scope) == "completed"


async def test_disconnected_waiter_does_not_own_finalization(
    prepared_file, monkeypatch
):
    db, scope, blob = prepared_file
    worker_context(db, monkeypatch)
    await tasks.extract_page_task(
        user_id=1, file_id=scope.file_id, blob_id=blob, page=1
    )
    stream = stream_progress(
        None, ExtractionOutcomes(db), ProgressWait({scope: (1, 2)}, 1)
    )
    assert (await anext(stream)).page == 1
    await stream.aclose()
    await tasks.extract_page_task(
        user_id=1, file_id=scope.file_id, blob_id=blob, page=2
    )
    assert await file_status(db, scope) == "completed"


@pytest.mark.parametrize(
    "failure", ["missing_reference", "object_store", "ocr", "schema"]
)
async def test_worker_failures_save_terminal_page_and_file(
    prepared_file, monkeypatch, failure
):
    db, scope, blob = prepared_file
    ctx = worker_context(db, monkeypatch)
    await ExtractionOutcomes(db).save(scope, PageOutcome(1, "extracted"))
    if failure == "missing_reference":
        await db.execute(
            "DELETE FROM file_blob_page WHERE blob_id = $1 AND page = 2", (blob,)
        )
    elif failure == "object_store":
        ctx.store.get.side_effect = RuntimeError("store unavailable")
    elif failure == "ocr":
        ctx.ocr.extract_from_image.side_effect = RuntimeError("OCR failed")
    else:
        monkeypatch.setattr(tasks, "validate_and_merge", lambda raw: 1 / 0)
    result = await tasks.extract_page_task(
        user_id=1, file_id=scope.file_id, blob_id=blob, page=2
    )
    assert result["status"] == "failed"
    assert await file_status(db, scope) == "partial"
    assert (await ExtractionOutcomes(db).read(scope)).pages[1].status == "failed"


async def test_cache_only_enqueue_finalizes_without_waiter(prepared_file, monkeypatch):
    db, scope, blob = prepared_file
    cache = SimpleNamespace(
        get_extraction=AsyncMock(return_value={"info": {"store_name": "Cached"}})
    )
    ingest = IngestService(
        settings=Settings(_env_file=None), db=db, cache=cache, store=None, ocr=None
    )
    monkeypatch.setattr(ingest, "_sanitize_and_verify", lambda attachment: attachment)
    monkeypatch.setattr(
        ingest,
        "_prepare_pdf",
        AsyncMock(return_value=(blob, scope.file_id, [(1, "hash-1"), (2, "hash-2")])),
    )
    monkeypatch.setattr(tasks.extract_page_task, "kiq", AsyncMock())
    result = await ingest.enqueue(
        FileAttachment(
            filename="receipt.pdf", content_type="application/pdf", data=b"pdf"
        ),
        session_id=scope.session_id,
        user_id=1,
    )
    assert result.cached_hits == 2
    assert result.queued == 0
    tasks.extract_page_task.kiq.assert_not_awaited()
    assert await file_status(db, scope) == "completed"


async def test_enqueue_rejects_foreign_session_before_storage(prepared_file):
    db, scope, _ = prepared_file
    ingest = IngestService(
        settings=Settings(_env_file=None), db=db, cache=None, store=None, ocr=None
    )
    with pytest.raises(ResourceNotFoundError):
        await ingest.enqueue(
            FileAttachment(
                filename="receipt.pdf", content_type="application/pdf", data=b"pdf"
            ),
            session_id=scope.session_id,
            user_id=42,
        )


async def test_bounded_outcomes_mark_oversized_text_and_reject_excess_pages(
    prepared_file,
):
    db, scope, _ = prepared_file
    service = ExtractionOutcomes(db)
    outcome = await service.save(
        scope, PageOutcome(1, "extracted", {"text": "x" * MAX_EXTRACTION_CHARACTERS})
    )
    assert outcome.pages[0].extraction_truncated
    assert outcome.pages[0].extraction == {}
    assert "archive" in outcome.pages[0].error
    with pytest.raises(ValueError, match="page limit"):
        await ExtractionOutcomes(db, max_pages=1).read(scope)
    with pytest.raises(ValueError, match="outside"):
        await service.save(scope, PageOutcome(3, "extracted"))


async def test_lost_worker_notification_reconciles_saved_pages(
    prepared_file, monkeypatch
):
    db, scope, blob = prepared_file
    ctx = worker_context(db, monkeypatch)
    ctx.cache.client.publish.side_effect = RuntimeError("notification lost")
    service = ExtractionOutcomes(db)
    first_read = True

    async def read_after_worker(scope, *, timeout_seconds=None):
        nonlocal first_read
        snapshot = await service.read(scope)
        if first_read:
            first_read = False
            for page in (1, 2):
                await tasks.extract_page_task(
                    user_id=1, file_id=scope.file_id, blob_id=blob, page=page
                )
        return snapshot

    reader = SimpleNamespace(read=read_after_worker)
    events = [
        event
        async for event in stream_progress(
            None, reader, ProgressWait({scope: (1, 2)}, 1, 0.001)
        )
    ]
    assert [event.page for event in events] == [1, 2]
    assert await file_status(db, scope) == "completed"


async def test_worker_rechecks_session_owner_after_ocr(prepared_file, monkeypatch):
    db, scope, blob = prepared_file
    ctx = worker_context(db, monkeypatch)
    other = await db.create_user("new-owner", "new@test.local", "unused")

    async def revoked_during_ocr(image):
        await db.execute(
            "UPDATE session SET user_id = $1 WHERE session_id = $2",
            (other, scope.session_id),
        )
        return {"info": {"store_name": "Uncommitted"}}

    ctx.ocr.extract_from_image.side_effect = revoked_during_ocr
    with pytest.raises(ResourceNotFoundError):
        await tasks.extract_page_task(
            user_id=1, file_id=scope.file_id, blob_id=blob, page=1
        )
    assert (
        await db.fetchval(
            "SELECT count(*) FROM pages WHERE metadata_file_id = $1", (scope.file_id,)
        )
        == 0
    )
    assert await file_status(db, scope) == "pending"


async def test_page_write_rolls_back_if_aggregate_update_fails(prepared_file):
    db, scope, _ = prepared_file
    await db.execute("""CREATE FUNCTION reject_extraction_rollup() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'controlled rollup failure'; END; $$ LANGUAGE plpgsql""")
    try:
        await db.execute("""CREATE TRIGGER reject_extraction_rollup BEFORE UPDATE ON metadata_file
            FOR EACH ROW EXECUTE FUNCTION reject_extraction_rollup()""")
        with pytest.raises(Exception, match="controlled rollup failure"):
            await ExtractionOutcomes(db).save(scope, PageOutcome(1, "extracted"))
        assert (
            await db.fetchval(
                "SELECT count(*) FROM pages WHERE metadata_file_id = $1",
                (scope.file_id,),
            )
            == 0
        )
        assert await file_status(db, scope) == "pending"
    finally:
        await db.execute(
            "DROP TRIGGER IF EXISTS reject_extraction_rollup ON metadata_file"
        )
        await db.execute("DROP FUNCTION reject_extraction_rollup()")


async def test_completed_extraction_with_actual_null_cache(prepared_file):
    from app.services.core.container import _NullDedupCache

    db, scope, blob = prepared_file
    service = ExtractionOutcomes(db)
    for page in (1, 2):
        await service.save(
            scope, PageOutcome(page, "cached", {"value": "12"}, "database")
        )
    orchestrator = orchestrator_for(db, scope, blob)
    orchestrator._c.dedup_cache = _NullDedupCache()
    events = await run_extraction(orchestrator, scope)
    assert events[-1]["payload"][0]["status"] == "completed"


async def test_held_session_lock_bounds_outcome_read_without_status_changes(
    prepared_file,
):
    db, scope, _ = prepared_file
    service = ExtractionOutcomes(db, operation_timeout_seconds=0.02)
    async with db.pool.acquire() as blocker:
        async with blocker.transaction():
            await blocker.execute(
                "SELECT 1 FROM session WHERE session_id = $1 FOR UPDATE",
                scope.session_id,
            )
            with pytest.raises(TimeoutError, match="Extraction outcomes"):
                await asyncio.wait_for(service.read(scope), timeout=1)
    assert await file_status(db, scope) == "pending"
    assert (
        await db.fetchval(
            "SELECT count(*) FROM pages WHERE metadata_file_id = $1", (scope.file_id,)
        )
        == 0
    )
    assert (await service.read(scope)).pending_pages == (1, 2)


async def test_pool_acquisition_has_the_same_outcome_deadline(prepared_file):
    db, scope, _ = prepared_file
    service = ExtractionOutcomes(db, operation_timeout_seconds=0.02)
    connections = []
    try:
        for _ in range(db.pool.get_max_size()):
            connections.append(await db.pool.acquire())
        with pytest.raises(TimeoutError, match="Extraction outcomes"):
            await asyncio.wait_for(service.read(scope), timeout=1)
    finally:
        for connection in connections:
            await db.pool.release(connection)
    assert await file_status(db, scope) == "pending"


async def test_polling_shortens_the_database_deadline(prepared_file):
    db, scope, _ = prepared_file
    async with db.pool.acquire() as blocker:
        async with blocker.transaction():
            await blocker.execute(
                "SELECT 1 FROM session WHERE session_id = $1 FOR UPDATE",
                scope.session_id,
            )
            stream = stream_progress(
                None, ExtractionOutcomes(db), ProgressWait({scope: (1, 2)}, 0.02)
            )
            with pytest.raises(TimeoutError, match="Extraction outcomes"):
                await asyncio.wait_for(anext(stream), timeout=1)
    assert await file_status(db, scope) == "pending"
