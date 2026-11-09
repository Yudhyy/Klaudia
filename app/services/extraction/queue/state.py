"""Worker-side singletons.

A Taskiq worker runs as a separate process from FastAPI. We need our own
DB / Redis / MinIO / OCR clients there. To avoid reconnecting per task we
build them once on WORKER_STARTUP and reuse them across all task
invocations on that worker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from taskiq import TaskiqEvents

from app.adapters.persistence.application import ApplicationDatabase
from app.adapters.persistence.documents import PostgresDocumentStore
from app.adapters.persistence.extraction_outcomes import PostgresExtractionOutcomes
from app.services.documents.ports import DocumentIngestionStore, ExtractionOutcomeStore
from app.services.extraction.infra.dedup_cache import DedupCache
from app.services.extraction.infra.object_store import MinIOClient
from app.services.extraction.infra.kie_client import KIEClient
from app.services.extraction.queue.broker import broker
from config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class WorkerContext:
    db: ApplicationDatabase
    documents: DocumentIngestionStore
    outcomes: ExtractionOutcomeStore
    cache: DedupCache
    store: MinIOClient
    ocr: KIEClient


_ctx: WorkerContext | None = None


def get_context() -> WorkerContext:
    if _ctx is None:
        raise RuntimeError(
            "WorkerContext not initialized. "
            "Workers must be started via `taskiq worker app.services.extraction.queue.broker:broker`."
        )
    return _ctx


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _startup(_state) -> None:
    global _ctx
    settings = get_settings()
    db = ApplicationDatabase(settings)
    await db.connect()
    cache = DedupCache(settings)
    await cache.connect()
    store = MinIOClient(settings)
    await store.ensure_bucket()
    ocr = KIEClient(settings)
    _ctx = WorkerContext(
        db=db,
        cache=cache,
        store=store,
        ocr=ocr,
        documents=PostgresDocumentStore(db.pool),
        outcomes=PostgresExtractionOutcomes(db.pool, settings.max_pdf_pages),
    )
    logger.info("Extraction worker context ready")


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _shutdown(_state) -> None:
    global _ctx
    if _ctx is None:
        return
    await _ctx.ocr.shutdown()
    await _ctx.cache.close()
    await _ctx.db.close()
    _ctx = None
    logger.info("Extraction worker context shut down")
