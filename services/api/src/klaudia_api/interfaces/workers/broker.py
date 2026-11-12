"""Taskiq broker + result backend.

The broker drives async OCR extraction. Sync-mode IngestService stays as
the test/dev fast path; production wiring will swap inline OCR for
``extract_page_task.kiq(...)`` so multiple users + multi-page PDFs run in
parallel against a single GPU.

Broker layout:
    DB 1 — Taskiq queue list (TASKIQ_BROKER_URL)
    DB 2 — Taskiq result backend (TASKIQ_RESULT_BACKEND_URL)
    DB 0 — Application hot cache (separate via REDIS_URL)

Three databases on one Redis instance keep operational concerns isolated:
flushing the cache for a release does not nuke in-flight tasks.
"""

from __future__ import annotations

import logging

from taskiq import AsyncBroker
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from klaudia_api.composition.settings import get_settings

logger = logging.getLogger(__name__)


def _build_broker() -> AsyncBroker:
    settings = get_settings()
    backend = RedisAsyncResultBackend(
        redis_url=settings.taskiq_result_backend_url,
        result_ex_time=settings.taskiq_result_ttl_seconds,
    )
    broker = ListQueueBroker(
        url=settings.taskiq_broker_url,
        queue_name=settings.taskiq_queue_name,
    ).with_result_backend(backend)
    logger.info(
        "Taskiq broker configured: queue=%s broker=%s result=%s",
        settings.taskiq_queue_name,
        settings.taskiq_broker_url,
        settings.taskiq_result_backend_url,
    )
    return broker


# Module-level singleton so workers and the FastAPI app share state.
broker = _build_broker()
