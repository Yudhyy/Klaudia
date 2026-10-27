"""Controlled notification timing for queued extraction."""

import asyncio
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError

from app.services.extraction.outcomes import ExtractionScope, FileOutcome, PageOutcome
from app.services.extraction.queue.progress import ProgressWait, stream_progress

SCOPE = ExtractionScope(1, 3, 7)


class SavedOutcomes:
    def __init__(self, statuses=("extracted", "pending")):
        self.statuses = list(statuses)
        self.reads = 0
        self.on_read = None

    async def read(self, scope, *, timeout_seconds=None):
        assert scope == SCOPE
        self.reads += 1
        if self.on_read:
            self.on_read(self.reads)
        return FileOutcome(
            scope,
            "receipt.pdf",
            tuple(
                PageOutcome(page, status)
                for page, status in enumerate(self.statuses, 1)
            ),
            "pending" if "pending" in self.statuses else "completed",
            "",
        )


class FakePubSub:
    def __init__(self, pages=(), on_message=None):
        self.pages = deque(pages)
        self.on_message = on_message
        self.subscribe = AsyncMock()
        self.aclose = AsyncMock()
        self.message_reads = 0

    async def get_message(self, **kwargs):
        self.message_reads += 1
        if self.pages:
            page = self.pages.popleft()
            if self.on_message:
                self.on_message(page)
            return {
                "type": "message",
                "data": json.dumps({"file_id": 7, "page": page, "status": "extracted"}),
            }
        await asyncio.sleep(kwargs.get("timeout", 0))
        return None


def waiting(timeout=0.1):
    return ProgressWait({SCOPE: (1, 2)}, timeout, 0.001)


async def collect(saved, pubsub=None, timeout=0.1):
    redis = SimpleNamespace(pubsub=lambda: pubsub) if pubsub is not None else None
    return [event async for event in stream_progress(redis, saved, waiting(timeout))]


async def test_duplicate_notifications_do_not_complete_distinct_pages():
    saved = SavedOutcomes()

    def finish(page):
        if page == 2:
            saved.statuses[1] = "extracted"

    pubsub = FakePubSub((1, 1, 2), finish)
    events = await collect(saved, pubsub)
    assert [event.page for event in events] == [1, 2]
    assert pubsub.message_reads == 3


@pytest.mark.parametrize("when", ["before", "during"])
async def test_completion_before_or_during_subscription_needs_no_notification(when):
    saved = SavedOutcomes()
    pubsub = FakePubSub()
    if when == "before":
        saved.statuses[1] = "extracted"
    else:

        async def complete(*channels):
            saved.statuses[1] = "extracted"

        pubsub.subscribe.side_effect = complete
    events = await collect(saved, pubsub)
    assert [event.page for event in events] == [1, 2]
    assert pubsub.message_reads == 0
    pubsub.aclose.assert_awaited_once()


@pytest.mark.parametrize("redis_available", [True, False])
async def test_missing_notifications_complete_from_saved_outcomes(redis_available):
    saved = SavedOutcomes()

    def finish(reads):
        if reads == 2:
            saved.statuses[1] = "failed"

    saved.on_read = finish
    events = await collect(saved, FakePubSub() if redis_available else None)
    assert [(event.page, event.status) for event in events] == [
        (1, "extracted"),
        (2, "failed"),
    ]


async def test_notifications_cannot_turn_pending_rows_into_success():
    saved = SavedOutcomes()
    events = await collect(saved, FakePubSub((2, 2, 99)), timeout=0.01)
    assert [event.page for event in events] == [1]
    assert saved.statuses == ["extracted", "pending"]


async def test_subscription_failure_falls_back_to_database():
    saved = SavedOutcomes(("extracted", "extracted"))
    pubsub = FakePubSub()
    pubsub.subscribe.side_effect = ConnectionError("offline")
    assert len(await collect(saved, pubsub)) == 2
    pubsub.aclose.assert_awaited_once()


async def test_client_stopping_after_page_event_closes_subscription():
    saved = SavedOutcomes()
    pubsub = FakePubSub()
    stream = stream_progress(SimpleNamespace(pubsub=lambda: pubsub), saved, waiting())
    assert (await anext(stream)).page == 1
    await stream.aclose()
    pubsub.aclose.assert_awaited_once()


async def test_expected_page_identity_must_match_saved_manifest():
    saved = SavedOutcomes()
    with pytest.raises(ValueError, match="manifest"):
        await anext(stream_progress(None, saved, ProgressWait({SCOPE: (1, 3)}, 0)))


async def test_redis_close_failure_does_not_hide_saved_completion():
    saved = SavedOutcomes(("extracted", "extracted"))
    pubsub = FakePubSub()
    pubsub.subscribe.side_effect = ConnectionError("offline")
    pubsub.aclose.side_effect = ConnectionError("close failed")
    assert len(await collect(saved, pubsub)) == 2
