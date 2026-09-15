from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from qwenpaw.app.task_tracker import TaskTracker, _TrackedQueue
from qwenpaw.providers.tl_preview import PreviewQueue


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@pytest.mark.asyncio
async def test_reconnect_drops_preview_and_original_subscriber_coalesces():
    tracker = TaskTracker()
    reached_live_edge = asyncio.Event()
    release = asyncio.Event()
    preview_start = _sse(
        {
            "type": "preview_start",
            "run_id": "run",
            "invocation_id": "inv",
            "attempt_id": "attempt",
        },
    )

    async def source(_payload):
        yield preview_start
        yield _sse({"object": "message", "text": "durable-1"})
        reached_live_edge.set()
        await release.wait()
        for revision, text in ((1, "a"), (2, "ab")):
            yield _sse(
                {
                    "type": "preview_update",
                    "run_id": "run",
                    "invocation_id": "inv",
                    "attempt_id": "attempt",
                    "revision": revision,
                    "kind": "final_text",
                    "item_index": 0,
                    "text": text,
                },
            )
        yield _sse({"object": "message", "text": "durable-2"})

    queue, is_new = await tracker.attach_or_start("run", {}, source)
    assert is_new is True
    await reached_live_edge.wait()
    reconnect_queue = await tracker.attach("run")
    assert reconnect_queue is not None
    release.set()

    events = [
        event
        async for event in tracker.stream_from_queue(reconnect_queue, "run")
    ]
    assert not any('"type": "preview_start"' in event for event in events)
    assert any("durable-1" in event for event in events)
    assert not any('"type": "preview_update"' in event for event in events)
    assert not any('"text": "a"' in event for event in events)
    assert any("durable-2" in event for event in events)

    original = [
        event async for event in tracker.stream_from_queue(queue, "run")
    ]
    assert any('"text": "ab"' in event for event in original)
    assert not any('"text": "a"' in event for event in original)


class _ObservedPreviewQueue(PreviewQueue):
    def __init__(self):
        super().__init__()
        self.waiting = asyncio.Event()
        self.readers = set()

    async def get(self):
        task = asyncio.current_task()
        self.readers.add(task)
        self.waiting.set()
        try:
            return await super().get()
        finally:
            self.readers.remove(task)


class _ObservedTrackedQueue(_TrackedQueue):
    def __init__(self):
        super().__init__()
        self.preview_queue = _ObservedPreviewQueue()
        self.waiting = asyncio.Event()
        self.readers = set()

    async def get(self):
        task = asyncio.current_task()
        self.readers.add(task)
        self.waiting.set()
        try:
            return await super().get()
        finally:
            self.readers.remove(task)


def _preview(queue, kind):
    # This test inbox uses typed payloads instead of serialized SSE.
    event = dict(type=kind, run_id="r", invocation_id="i", attempt_id="a")
    assert queue.preview_queue.put_preview_nowait(event)
    return event


@pytest.mark.asyncio
@pytest.mark.parametrize("close_pending", [False, True])
async def test_simultaneous_readiness_preserves_or_closes_pending_durable(
    close_pending,
):
    tracker = TaskTracker()
    tracker.detach_subscriber = AsyncMock()
    queue = _ObservedTrackedQueue()
    stream = tracker.stream_from_queue(queue, "run")
    pending = asyncio.create_task(anext(stream))
    await asyncio.wait_for(queue.waiting.wait(), 1)
    await asyncio.wait_for(queue.preview_queue.waiting.wait(), 1)
    start = _preview(queue, "preview_start")
    queue.put_nowait("durable")
    queue.put_nowait(None)
    assert await asyncio.wait_for(pending, 1) == start
    if close_pending:
        await stream.aclose()
    else:
        clear = _preview(queue, "preview_clear")
        assert [e async for e in stream] == [clear, "durable"]
    assert not queue.readers and not queue.preview_queue.readers
    tracker.detach_subscriber.assert_awaited_once_with("run", queue)


@pytest.mark.asyncio
async def test_clear_is_delivered_before_simultaneous_sentinel():
    tracker = TaskTracker()
    queue = _ObservedTrackedQueue()
    start = _preview(queue, "preview_start")
    stream = tracker.stream_from_queue(queue, "run")
    assert await anext(stream) == start
    pending = asyncio.create_task(anext(stream))
    await asyncio.wait_for(queue.waiting.wait(), 1)
    await asyncio.wait_for(queue.preview_queue.waiting.wait(), 1)
    clear = _preview(queue, "preview_clear")
    queue.put_nowait(None)
    assert await asyncio.wait_for(pending, 1) == clear
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), 1)
    assert not queue.readers and not queue.preview_queue.readers


@pytest.mark.asyncio
async def test_cancellation_while_both_queues_empty_releases_readers():
    tracker = TaskTracker()
    tracker.detach_subscriber = AsyncMock()
    queue = _ObservedTrackedQueue()
    stream = tracker.stream_from_queue(queue, "run")
    pending = asyncio.create_task(anext(stream))
    await asyncio.wait_for(queue.waiting.wait(), 1)
    await asyncio.wait_for(queue.preview_queue.waiting.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not queue.readers and not queue.preview_queue.readers
    tracker.detach_subscriber.assert_awaited_once_with("run", queue)


@pytest.mark.asyncio
async def test_preview_reader_cannot_steal_clear_at_sentinel():
    class DelayedQueue(_ObservedPreviewQueue):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()

        async def get(self):
            self.waiting.set()
            await self.release.wait()
            return await super().get()

    queue = _ObservedTrackedQueue()
    queue.preview_queue = DelayedQueue()
    tracker = TaskTracker()
    stream = tracker.stream_from_queue(queue, "run")
    pending = asyncio.create_task(anext(stream))
    await asyncio.wait_for(queue.waiting.wait(), 1)
    await asyncio.wait_for(queue.preview_queue.waiting.wait(), 1)
    start = _preview(queue, "preview_start")
    clear = _preview(queue, "preview_clear")
    queue.put_nowait(None)
    assert await asyncio.wait_for(pending, 1) == start
    queue.preview_queue.release.set()
    await asyncio.sleep(0)
    try:
        assert await asyncio.wait_for(anext(stream), 1) == clear
    finally:
        await stream.aclose()
