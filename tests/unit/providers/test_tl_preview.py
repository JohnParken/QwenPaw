from __future__ import annotations

import json
import asyncio

import pytest

from qwenpaw.providers.tl_preview import (
    PREVIEW_CLEAR,
    PREVIEW_START,
    PREVIEW_UPDATE,
    begin_preview,
    preview_scope,
    PreviewQueue,
)


def _drain(scope):
    events = []
    while True:
        try:
            events.append(scope.queue.get_nowait())
        except asyncio.QueueEmpty:
            return events


def test_preview_is_noop_without_a_scope():
    preview = begin_preview()
    assert preview.feed('{"type":"final","content":"ignored"}') == []
    assert preview.clear("cancel") is None


def test_parser_handles_reordered_escaped_unicode_chunks():
    text = '{"content":"你\\ud83d\\ude00\\n","type":"final"}'
    with preview_scope(run_id="run", invocation_id="inv") as scope:
        preview = begin_preview(attempt_id="attempt")
        for index in range(0, len(text), 3):
            end = index + 3
            preview.feed(text[index:end])
        assert preview.finish() is True
        events = _drain(scope)

    assert events[0].type == PREVIEW_START
    updates = [event for event in events if event.type == PREVIEW_UPDATE]
    assert updates
    assert updates[-1].text == "你😀\n"
    assert all(event.kind == "final_text" for event in updates)


def test_duplicate_key_clears_preview_and_late_feed_is_ignored():
    with preview_scope(run_id="run", invocation_id="inv") as scope:
        preview = begin_preview(attempt_id="attempt")
        preview.feed('{"type":"final","content":"a","content":"b"}')
        events = _drain(scope)
        assert events[-1].type == PREVIEW_CLEAR
        assert events[-1].reason == "invalid"
        assert preview.feed('{"type":"final","content":"later"}') == []


def test_preview_updates_coalesce_without_affecting_lifecycle_edges():
    with preview_scope(run_id="run", invocation_id="inv") as scope:
        preview = begin_preview(attempt_id="attempt")
        preview.feed('{"type":"final","content":"one"}')
        preview.feed("}")
        preview.clear("commit")
        events = _drain(scope)

    assert [event.type for event in events] == [PREVIEW_START, PREVIEW_CLEAR]


@pytest.mark.parametrize("serialized", [False, True])
def test_slow_client_lifecycle_admission_stays_bounded(serialized):
    if serialized:
        from qwenpaw.app.task_tracker import _TrackedQueue

        target = _TrackedQueue()
        queue = target.preview_queue
    else:
        target = queue = PreviewQueue(maxsize=8)

    def event(kind, attempt):
        data = dict(
            type=kind, run_id="run", invocation_id="inv", attempt_id=attempt
        )
        return "data: " + json.dumps(data) + "\n\n" if serialized else data

    admitted = []
    for index in range(500):
        attempt = str(index)
        accepted = target.put_preview_nowait(event(PREVIEW_START, attempt))
        if accepted:
            admitted.append(attempt)
        assert (
            target.put_preview_nowait(event(PREVIEW_CLEAR, attempt))
            is accepted
        )
        assert queue.qsize() <= queue.maxsize
    assert len(admitted) == queue.maxsize // 2
    seen = []
    while not queue.empty():
        payload = queue.get_nowait()
        seen.append(json.loads(payload[5:]) if serialized else payload)
    assert len(seen) == len(admitted) * 2
    assert [e["type"] for e in seen] == [PREVIEW_START, PREVIEW_CLEAR] * len(
        admitted
    )
    assert target.put_preview_nowait(event(PREVIEW_START, "next"))


def test_clear_is_reserved_for_starts_already_seen_by_client():
    queue = PreviewQueue(maxsize=4)
    with preview_scope(queue=queue) as scope:
        first = begin_preview(attempt_id="first")
        assert scope.queue.get_nowait().type == PREVIEW_START
        second = begin_preview(attempt_id="second")
        rejected = begin_preview(attempt_id="rejected")
        assert not rejected.active
        first.clear("commit")
        second.clear("cancel")
        assert queue.qsize() <= queue.maxsize
        assert [e.type for e in scope.drain_nowait()] == [
            PREVIEW_START,
            PREVIEW_CLEAR,
            PREVIEW_CLEAR,
        ]


@pytest.mark.asyncio
async def test_waiter_cancellation_does_not_consume_or_lose_wakeup():
    queue = PreviewQueue()
    canceled = asyncio.create_task(queue.get())
    await asyncio.sleep(0)
    canceled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await canceled
    first = asyncio.create_task(queue.get())
    second = asyncio.create_task(queue.get())
    await asyncio.sleep(0)
    with preview_scope(queue=queue):
        attempt = begin_preview()
        assert (await asyncio.wait_for(first, 1)).type == PREVIEW_START
        assert not second.done()
        assert queue.empty()
        attempt.clear("commit")
        assert (await asyncio.wait_for(second, 1)).type == PREVIEW_CLEAR
    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()


@pytest.mark.asyncio
async def test_cancel_after_notification_keeps_the_queued_event():
    queue = PreviewQueue()
    waiter = asyncio.create_task(queue.get())
    await asyncio.sleep(0)
    with preview_scope(queue=queue):
        begin_preview()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert (await asyncio.wait_for(queue.get(), 1)).type == PREVIEW_START
