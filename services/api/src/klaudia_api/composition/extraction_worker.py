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

from klaudia_api.adapters.persistence.application import ApplicationDatabase
from klaudia_api.adapters.persistence.documents import PostgresDocumentStore
from klaudia_api.adapters.persistence.extraction_outcomes import (
    PostgresExtractionOutcomes,
)
from document_intelligence.ports import DocumentIngestionStore, ExtractionOutcomeStore
from klaudia_api.adapters.documents.cache.redis import DedupCache
from klaudia_api.adapters.documents.object_store.s3 import MinIOClient
from klaudia_api.adapters.documents.kie.client import KIEClient
from klaudia_api.interfaces.workers.broker import broker
from klaudia_api.composition.settings import get_settings

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
            "Workers must be started via `taskiq worker klaudia_api.interfaces.workers.broker:broker`."
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
