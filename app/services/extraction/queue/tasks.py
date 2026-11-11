"""Translate Taskiq jobs and Redis progress for queued document extraction."""

from __future__ import annotations

from functools import partial
import json
import logging
from typing import Any

from app.services.extraction.queue.broker import broker
from app.services.extraction.queue.progress import PROGRESS_CHANNEL_FMT
from app.services.extraction.queue.state import get_context
from document_intelligence.contracts import PageOutcome, QueuedPage
from document_intelligence.ports import ExtractionServices
from document_intelligence.queued import QueuedExtraction

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
    """Process the existing job payload through the worker's capabilities."""
    context = get_context()
    services = ExtractionServices(
        context.documents, context.outcomes, context.cache, context.store, context.ocr
    )
    extraction = QueuedExtraction(services, partial(_publish_progress, context.cache))
    return await extraction.run(QueuedPage(user_id, file_id, blob_id, page))


async def _publish_progress(cache, file_id: int, page: PageOutcome) -> None:
    """Publish the existing wake-up payload without affecting the saved outcome."""
    channel = PROGRESS_CHANNEL_FMT.format(file_id=file_id)
    payload = {
        "file_id": file_id,
        "page": page.page,
        "status": page.status,
        "cache_layer": page.cache_layer,
        "error": page.error,
    }
    try:
        await cache.client.publish(channel, json.dumps(payload, ensure_ascii=False))
    except Exception as error:
        logger.warning("publish progress failed (%s): %s", channel, error)
