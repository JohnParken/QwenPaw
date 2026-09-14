from __future__ import annotations

import asyncio

import pytest

from qwenpaw.providers.tl_preview import (
    PREVIEW_START,
    PREVIEW_CLEAR,
    PreviewQueue,
    PreviewEvent,
    begin_preview,
    preview_scope,
)
from qwenpaw.runtime.executor import AgentExecutor


class _PausedAgent:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def reply_stream(self, *, inputs):
        del inputs
        await self.release.wait()
        yield object()


class _Envelope:
    async def heartbeat(self):
        yield "heartbeat"

    async def translate_event(self, event):
        yield event


@pytest.mark.asyncio
async def test_preview_is_delivered_before_heartbeat_while_agent_is_paused():
    agent = _PausedAgent()
    stream = AgentExecutor(agent, _Envelope()).run([])
    with preview_scope(run_id="run", invocation_id="inv"):
        begin_preview(attempt_id="attempt")
        next_event = asyncio.create_task(stream.__anext__())
        event = await asyncio.wait_for(next_event, timeout=0.2)
        assert isinstance(event, PreviewEvent)
        assert event.type == PREVIEW_START

        agent.release.set()
        await stream.aclose()


class _ObservedQueue(PreviewQueue):
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


class _ScriptAgent:
    def __init__(self, script):
        self.script = script
        self.closed = False

    async def reply_stream(self, *, inputs):
        try:
            async for event in self.script():
                yield event
        finally:
            self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("emit", [False, True])
async def test_empty_preview_queue_does_not_hold_agent_completion(emit):
    async def script():
        if emit:
            yield "formal"

    agent = _ScriptAgent(script)
    queue = _ObservedQueue()
    with preview_scope(queue=queue):
        stream = AgentExecutor(agent, _Envelope()).run([])

        async def collect():
            return [event async for event in stream]

        assert await asyncio.wait_for(collect(), 1) == (
            ["formal"] if emit else []
        )
    assert agent.closed
    assert not queue.readers


@pytest.mark.asyncio
async def test_simultaneous_preview_clear_precedes_formal_event():
    async def script():
        attempt = begin_preview()
        attempt.feed('{"type":"final","content":"draft"}')
        attempt.clear("commit")
        yield "formal"

    queue = _ObservedQueue()
    agent = _ScriptAgent(script)
    with preview_scope(queue=queue) as scope:
        events = [e async for e in AgentExecutor(agent, _Envelope()).run([])]
        assert [
            e.type if isinstance(e, PreviewEvent) else e for e in events
        ] == [PREVIEW_START, PREVIEW_CLEAR, "formal"]
        assert not scope.attempts
    assert agent.closed
    assert not queue.readers


@pytest.mark.asyncio
async def test_agent_error_closes_pending_preview_reader():
    async def script():
        begin_preview()
        raise ValueError("agent failed")
        yield  # pragma: no cover

    queue = _ObservedQueue()
    agent = _ScriptAgent(script)
    with preview_scope(queue=queue) as scope:
        with pytest.raises(ValueError, match="agent failed"):
            async for _ in AgentExecutor(agent, _Envelope()).run([]):
                pass
        assert not scope.attempts
    assert agent.closed
    assert not queue.readers


@pytest.mark.asyncio
@pytest.mark.parametrize("close_at_yield", [False, True])
async def test_cancellation_and_close_cleanup_both_sources(close_at_yield):
    started = asyncio.Event()

    async def script():
        started.set()
        await asyncio.Event().wait()
        yield  # pragma: no cover

    queue = _ObservedQueue()
    agent = _ScriptAgent(script)
    with preview_scope(queue=queue) as scope:
        stream = AgentExecutor(agent, _Envelope()).run([])
        pending = asyncio.create_task(anext(stream))
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(queue.waiting.wait(), 1)
        if close_at_yield:
            begin_preview()
            assert (await asyncio.wait_for(pending, 1)).type == PREVIEW_START
            await stream.aclose()
        else:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        assert not scope.attempts
    assert agent.closed
    assert not queue.readers


@pytest.mark.asyncio
async def test_preview_reader_cannot_steal_clear_while_draining():
    class DelayedQueue(PreviewQueue):
        def __init__(self):
            super().__init__()
            self.release = asyncio.Event()

        async def get(self):
            await self.release.wait()
            return await super().get()

    async def script():
        attempt = begin_preview()
        attempt.clear("commit")
        yield "formal"

    queue = DelayedQueue()
    agent = _ScriptAgent(script)
    with preview_scope(queue=queue):
        stream = AgentExecutor(agent, _Envelope()).run([])
        assert (await asyncio.wait_for(anext(stream), 1)).type == PREVIEW_START
        queue.release.set()
        await asyncio.sleep(0)
        try:
            clear = await asyncio.wait_for(anext(stream), 1)
            assert isinstance(clear, PreviewEvent)
            assert clear.type == PREVIEW_CLEAR
            assert await anext(stream) == "formal"
        finally:
            await stream.aclose()
