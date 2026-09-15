"""Preview lifecycle is independent of authoritative model commits."""

import asyncio

import pytest
from agentscope.message import UserMsg

from qwenpaw.providers.tl_chat_model import TLChatModel
from qwenpaw.providers.tl_config import TLConfig
from qwenpaw.providers.tl_errors import TLError
from qwenpaw.providers.tl_preview import preview_scope

TOOLS = [
    {
        "type": "function",
        "function": {"name": "noop", "parameters": {"type": "object"}},
    }
]
FINAL = '{"version":1,"type":"final","content":"你好🌟"}'


class PausedTransport:
    config = TLConfig()

    def __init__(self, *, failure=None):
        self.paused = asyncio.Event()
        self.resume = asyncio.Event()
        self.failure = failure
        self.closed = False

    async def iter_text(self, *args, **kwargs):
        try:
            yield FINAL[:-2]
            self.paused.set()
            await self.resume.wait()
            if self.failure:
                raise self.failure
            yield FINAL[-2:]
        finally:
            self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["commit", "cancel", "error"])
async def test_partial_preview_precedes_model_commit_and_always_clears(
    outcome,
):
    transport = PausedTransport(
        failure=(
            TLError("sse_decode", "EOF", "eof") if outcome == "error" else None
        )
    )
    model = TLChatModel("local", transport)
    with preview_scope(run_id="run") as scope:
        stream = await model([UserMsg("u", "hello")], TOOLS)
        pending = asyncio.create_task(anext(stream))
        await asyncio.wait_for(transport.paused.wait(), timeout=2)
        assert not pending.done(), "No model block before transport completion"
        early = scope.drain_nowait()
        assert early[0].type == "preview_start"
        assert early[-1].type == "preview_update"
        assert early[-1].text == "你好🌟"
        if outcome == "cancel":
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            transport.resume.set()
            if outcome == "error":
                with pytest.raises(TLError):
                    await pending
            else:
                response = await pending
                assert response.content[0].text == "你好🌟"
                assert not response.is_last
        events = scope.drain_nowait()
        assert events[-1].type == "preview_clear"
        assert events[-1].reason == outcome
        assert not scope.attempts
        assert transport.closed
        await stream.aclose()


@pytest.mark.asyncio
async def test_correction_uses_fresh_attempt_and_clears_before_restart():
    observed = []

    class CorrectionTransport:
        config = TLConfig()
        count = 0

        async def iter_text(self, *args, **kwargs):
            self.count += 1
            yield FINAL[:-2] if self.count == 1 else FINAL

    transport = CorrectionTransport()
    with preview_scope(run_id="run", sink=observed.append):
        model = TLChatModel("local", transport)
        blocks = [
            response
            async for response in await model([UserMsg("u", "hi")], TOOLS)
        ]
    assert len(blocks) == 2
    assert transport.count == 2
    lifecycle = [e for e in observed if e.type != "preview_update"]
    assert [e.type for e in lifecycle] == [
        "preview_start",
        "preview_clear",
        "preview_start",
        "preview_clear",
    ]
    assert [e.reason for e in lifecycle if e.type == "preview_clear"] == [
        "correction",
        "commit",
    ]
    assert lifecycle[0].attempt_id != lifecycle[2].attempt_id
    assert lifecycle[0].invocation_id == lifecycle[2].invocation_id


@pytest.mark.asyncio
async def test_preview_disabled_does_not_change_tool_mode_response():
    transport = PausedTransport()
    transport.resume.set()
    with preview_scope(enabled=False) as scope:
        model = TLChatModel("local", transport)
        responses = [
            item async for item in await model([UserMsg("u", "hi")], TOOLS)
        ]
        assert not scope.drain_nowait()
        assert len(responses) == 2
