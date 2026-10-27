"""Worker-side extraction tasks.

A task takes a blob_page reference (resolved via the private file_blob_page
table) and runs OCR + cache persistence. The orchestrator publishes one
task per page; results land in:
    - blob_extraction (persistent database cache)
    - dedup cache (Redis L1)
    - pages.agent_extracted (LLM-visible)
    - pubsub channel `extraction:file:<file_id>:progress` for SSE streaming

Page writes serialize by file and page. A failed retry preserves an already
saved success. Each write commits the aggregate file status in the same transaction.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.services.extraction.agents.schema import validate_and_merge
from app.services.extraction.queue.broker import broker
from app.services.extraction.queue.state import get_context
from app.services.extraction.outcomes import ExtractionOutcomes, PageOutcome, QueuedPage
from app.services.extraction.queue.progress import PROGRESS_CHANNEL_FMT
from config.settings import get_settings

logger = logging.getLogger(__name__)


@broker.task(
    task_name="ocr.extract_page",
    retry_on_error=True,
    max_retries=3,
    delay=2,
)
async def extract_page_task(
    *,
    user_id: int,
    file_id: int,
    blob_id: int,
    page: int,
) -> dict[str, Any]:
    """Run OCR for one page. Publishes a progress event regardless of outcome
    so the orchestrator can stream completion to the client.
    """
    ctx = get_context()
    db = ctx.db
    cache = ctx.cache
    store = ctx.store
    ocr = ctx.ocr

    outcomes = ExtractionOutcomes(db, get_settings().max_pdf_pages)
    scope, row = await outcomes.resolve_page(
        QueuedPage(user_id, file_id, blob_id, page)
    )
    if row["page_blake3"] is None:
        msg = "Prepared page reference is missing"
        await outcomes.save(scope, PageOutcome(page, "failed", error=msg))
        await _publish_progress(cache, file_id, page, status="failed", error=msg)
        return {"status": "failed", "error": msg}

    page_blake3 = row["page_blake3"]
    page_key = row["page_minio_key"]

    # L1
    cached = await _safe_get(cache.get_extraction, user_id, page_blake3)
    if cached is not None:
        await outcomes.save(scope, PageOutcome(page, "cached", cached, "redis"))
        await _publish_progress(
            cache, file_id, page, status="cached", cache_layer="redis"
        )
        return {"status": "cached", "cache_layer": "redis"}

    database_row = await db.get_cached_extraction(user_id, page_blake3)
    if database_row is not None:
        try:
            extraction = json.loads(database_row["extraction_json"])
            await _safe_set(cache.set_extraction, user_id, page_blake3, extraction)
            await outcomes.save(
                scope, PageOutcome(page, "cached", extraction, "database")
            )
            await _publish_progress(
                cache, file_id, page, status="cached", cache_layer="database"
            )
            return {"status": "cached", "cache_layer": "database"}
        except json.JSONDecodeError:
            logger.warning("blob_extraction row corrupted; re-extracting")

    # Miss → fetch JPG from MinIO and call OCR
    try:
        jpg = await store.get(page_key)
    except Exception as e:
        msg = f"object_store.get({page_key!r}) failed: {e}"
        logger.error(msg)
        await outcomes.save(scope, PageOutcome(page, "failed", error=msg))
        await _publish_progress(cache, file_id, page, status="failed", error=msg)
        return {"status": "failed", "error": msg}

    try:
        raw = await ocr.extract_from_image(jpg)
        validated = validate_and_merge(raw)
    except Exception as e:
        await outcomes.save(scope, PageOutcome(page, "failed", error=str(e)))
        await _publish_progress(cache, file_id, page, status="failed", error=str(e))
        return {"status": "failed", "error": str(e)}

    await db.upsert_extraction(
        user_id=user_id,
        page_blake3=page_blake3,
        extraction_json=json.dumps(validated, ensure_ascii=False),
        ocr_model=ocr.model_id,
        schema_version=ocr.schema_version,
    )
    await _safe_set(cache.set_extraction, user_id, page_blake3, validated)
    await outcomes.save(scope, PageOutcome(page, "extracted", validated))
    await _publish_progress(cache, file_id, page, status="extracted")
    return {"status": "extracted"}


async def _publish_progress(
    cache,
    file_id: int,
    page: int,
    *,
    status: str,
    cache_layer: str | None = None,
    error: str | None = None,
) -> None:
    channel = PROGRESS_CHANNEL_FMT.format(file_id=file_id)
    payload = {
        "file_id": file_id,
        "page": page,
        "status": status,
        "cache_layer": cache_layer,
        "error": error,
    }
    try:
        await cache.client.publish(channel, json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        logger.warning("publish progress failed (%s): %s", channel, e)


async def _safe_get(getter, *args):
    try:
        return await getter(*args)
    except Exception as e:
        logger.warning("cache get failed: %s", e)
        return None


async def _safe_set(setter, *args):
    try:
        await setter(*args)
    except Exception as e:
        logger.warning("cache set failed: %s", e)
