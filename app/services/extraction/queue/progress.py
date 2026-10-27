"""Reconcile saved page outcomes; Redis notifications only wake the check."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncIterator

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.services.extraction.outcomes import ExtractionOutcomes, ExtractionScope

logger = logging.getLogger(__name__)
PROGRESS_CHANNEL_FMT = "extraction:file:{file_id}:progress"
RECONCILIATION_GRACE_SECONDS = 0.1


@dataclass(frozen=True)
class ProgressEvent:
    file_id: int
    page: int
    status: str
    cache_layer: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ProgressWait:
    file_pages: dict[ExtractionScope, tuple[int, ...]]
    timeout_seconds: float
    poll_interval_seconds: float = 0.5


async def stream_progress(
    redis_client: aioredis.Redis | None,
    outcomes: ExtractionOutcomes,
    wait: ProgressWait,
) -> AsyncIterator[ProgressEvent]:
    """Yield each saved terminal page once, stopping on completion or timeout.

    Subscribe before the first read so completion during subscription is seen.
    Polling covers lost notifications and unavailable Redis. The caller reads
    the final outcome to distinguish complete files from deferred pages.
    """
    if not wait.file_pages:
        return
    if wait.poll_interval_seconds <= 0:
        raise ValueError("Progress poll interval must be positive")
    deadline = asyncio.get_running_loop().time() + max(wait.timeout_seconds, 0)
    pubsub = redis_client.pubsub() if redis_client is not None else None
    seen: set[tuple[int, int]] = set()
    try:
        if pubsub is not None:
            channels = [
                PROGRESS_CHANNEL_FMT.format(file_id=scope.file_id)
                for scope in wait.file_pages
            ]
            try:
                await asyncio.wait_for(
                    pubsub.subscribe(*channels),
                    timeout=max(deadline - asyncio.get_running_loop().time(), 0.001),
                )
            except (RedisError, asyncio.TimeoutError):
                logger.warning(
                    "Progress subscription unavailable; polling saved outcomes"
                )
                await _close_pubsub(pubsub)
                pubsub = None

        while True:
            complete = True
            for scope, expected in wait.file_pages.items():
                # Allow one bounded snapshot even when the worker wait is zero.
                remaining = deadline - asyncio.get_running_loop().time()
                outcome = await outcomes.read(
                    scope, timeout_seconds=max(remaining, RECONCILIATION_GRACE_SECONDS)
                )
                if tuple(page.page for page in outcome.pages) != expected:
                    raise ValueError(
                        "Expected pages differ from the saved file manifest"
                    )
                complete = complete and outcome.complete
                for page in outcome.pages:
                    identity = (scope.file_id, page.page)
                    if page.status == "pending" or identity in seen:
                        continue
                    seen.add(identity)
                    yield ProgressEvent(
                        scope.file_id,
                        page.page,
                        page.status,
                        page.cache_layer,
                        page.error,
                    )
            remaining = deadline - asyncio.get_running_loop().time()
            if complete or remaining <= 0:
                return
            interval = min(wait.poll_interval_seconds, remaining)
            if pubsub is None:
                await asyncio.sleep(interval)
                continue
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=interval
                    ),
                    timeout=interval,
                )
                if message is None:
                    await asyncio.sleep(min(0.01, interval))
            except asyncio.TimeoutError:
                pass
            except RedisError:
                logger.warning(
                    "Progress notifications unavailable; polling saved outcomes"
                )
                await _close_pubsub(pubsub)
                pubsub = None
    finally:
        if pubsub is not None:
            await _close_pubsub(pubsub)


async def _close_pubsub(pubsub) -> None:
    try:
        await asyncio.wait_for(pubsub.aclose(), timeout=1.0)
    except (RedisError, asyncio.TimeoutError):
        logger.warning("Could not close the extraction progress subscription")
