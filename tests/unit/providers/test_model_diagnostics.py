"""Model diagnostics are opt-in, task-scoped, redacted and bounded."""

import asyncio
import json
import logging

import pytest

from qwenpaw.providers.tl_preview import PreviewQueue, begin_preview, preview_scope
from qwenpaw.providers.tl_wire_log import log_wire


def test_requires_operator_and_request_opt_in(monkeypatch):
    monkeypatch.delenv("QWENPAW_MODEL_DEBUG", raising=False)
    with preview_scope(model_debug=True) as scope:
        log_wire(event="request", payload={"text": "hidden"})
        assert scope.queue.empty()
    monkeypatch.setenv("QWENPAW_MODEL_DEBUG", "1")
    with preview_scope() as scope:
        log_wire(event="request", payload={"text": "hidden"})
        assert scope.queue.empty()


@pytest.mark.asyncio
async def test_interleaved_identity_redaction_and_context_reset(monkeypatch):
    monkeypatch.setenv("QWENPAW_MODEL_DEBUG", "1")

    async def run(user):
        with preview_scope(run_id=user, model_debug=True) as scope:
            await asyncio.sleep(0)
            log_wire(
                event="request",
                payload={"text": user + " known-key", "api_key": "secret"},
                secrets=("known-key",),
            )
            await asyncio.sleep(0)
            records = scope.drain_nowait()
            assert len(records) == 1
            assert records[0]["run_id"] == user
            assert records[0]["payload"]["text"] == user + " [REDACTED]"
            assert records[0]["payload"]["api_key"] == "[REDACTED]"
            return scope

    a, b = await asyncio.gather(run("alice"), run("bob"))
    log_wire(event="request", payload="outside")
    assert a.queue.empty() and b.queue.empty()


def test_sse_sampling_retains_final_response_and_errors(monkeypatch, caplog):
    monkeypatch.setenv("QWENPAW_MODEL_DEBUG", "1")
    caplog.set_level(logging.ERROR, logger="qwenpaw.providers.tl_wire")
    with preview_scope(model_debug=True) as scope:
        for i in range(200):
            log_wire(event="sse_event", payload={"delta": i}, request_id="req")
        log_wire(event="model_response", payload={"text": "complete"})
        log_wire(event="error", payload={"stage": "sse_decode", "password": "secret"})
        records = scope.drain_nowait()
        assert len(records) == 10
        assert records[-2]["event"] == "model_response"
        assert records[-1]["level"] == "ERROR"
        assert all(r["sampled"] for r in records[:8])
    assert caplog.records[-1].levelno == logging.ERROR
    assert '"password":"secret"' not in caplog.text


def test_diagnostics_cannot_exhaust_preview_lifecycle_capacity(monkeypatch):
    monkeypatch.setenv("QWENPAW_MODEL_DEBUG", "1")
    with preview_scope(queue=PreviewQueue(maxsize=8), model_debug=True) as scope:
        for i in range(30):
            log_wire(event="request", payload={"i": i})
        attempt = begin_preview()
        attempt.feed('{"type":"final","content":"hello"}')
        attempt.clear("commit")
        events = [
            e if isinstance(e, dict) else e.as_dict() for e in scope.drain_nowait()
        ]
        assert len(events) <= 8
        assert any(e["type"] == "preview_start" for e in events)
        assert any(e["type"] == "preview_clear" for e in events)


def test_total_capture_is_bounded_even_when_drained(monkeypatch):
    monkeypatch.setenv("QWENPAW_MODEL_DEBUG", "1")
    with preview_scope(model_debug=True) as scope:
        records = []
        for i in range(300):
            log_wire(event="request", payload="x" * 40000)
            records.extend(scope.drain_nowait())
        assert len(records) == 128
        assert all(e["truncated"] for e in records)
        assert len(json.dumps(records)) < 5_000_000


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "sse"])
async def test_transport_failures_have_error_stage_and_request_ids(
    monkeypatch, caplog, failure
):
    import httpx
    from qwenpaw.providers.tl_config import TLConfig
    from qwenpaw.providers.tl_errors import TLError
    from qwenpaw.providers.tl_transport import TLTransport

    monkeypatch.setenv("QWENPAW_MODEL_DEBUG", "1")
    caplog.set_level(logging.ERROR, logger="qwenpaw.providers.tl_wire")
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("init_session"):
            return httpx.Response(200, json={"code": 0, "data": {"session_id": "s"}})
        if failure == "http":
            return httpx.Response(502)
        return httpx.Response(
            200,
            content=b"event: chunk\ndata: {bad}\n\n",
            headers={"content-type": "text/event-stream"},
        )

    with preview_scope(run_id="owned-run", model_debug=True) as scope:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = TLTransport(
                "http://tl", TLConfig(), api_key="test-secret", client=client
            )
            with pytest.raises(TLError):
                _ = [chunk async for chunk in transport.iter_text("system", "user")]
        records = scope.drain_nowait()
    errors = [r for r in records if r["level"] == "ERROR"]
    assert errors and all(r["run_id"] == "owned-run" for r in errors)
    assert any(r.get("request_id") and r.get("attempt_id") for r in errors)
    assert all(r["payload"]["stage"] for r in errors)
    assert len(calls) == 2  # Diagnostics must never retry an external operation.
    assert "test-secret" not in json.dumps(records)
    assert any(r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_personal_console_side_channel_carries_model_logs():
    from qwenpaw.app.task_tracker import _TrackedQueue, _preview_payload

    record = {"type": "model_log", "event": "request", "payload": {"text": "hello"}}
    sse = "data: " + json.dumps(record) + "\n\n"
    queue = _TrackedQueue()
    assert queue.put_preview_nowait(sse)
    assert _preview_payload(await queue.preview_queue.get()) == record
    assert queue.empty()  # Do not retain diagnostics in personal chat history.


@pytest.mark.asyncio
async def test_executor_delivers_error_diagnostics_before_raising(monkeypatch):
    from qwenpaw.runtime.executor import AgentExecutor
    from tests.unit.runtime.test_executor_preview import _Envelope, _ScriptAgent

    monkeypatch.setenv("QWENPAW_MODEL_DEBUG", "1")

    async def fail():
        log_wire(event="error", payload={"stage": "sse_decode"})
        raise ValueError("bad model stream")
        yield  # pragma: no cover

    events = []
    with preview_scope(run_id="r", model_debug=True):
        with pytest.raises(ValueError):
            async for event in AgentExecutor(_ScriptAgent(fail), _Envelope()).run([]):
                events.append(event)
    assert any(e["type"] == "model_log" and e["level"] == "ERROR" for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("debug", [False, True])
async def test_personal_channel_debug_capability_serializes_actual_diagnostics(
    monkeypatch, tmp_path, debug
):
    from qwenpaw.app.channels.console.channel import ConsoleChannel
    from qwenpaw.providers.tl_preview import current_preview_scope
    from qwenpaw.schemas import TextContent

    monkeypatch.setenv("QWENPAW_MODEL_DEBUG", "1")

    async def process(request):
        log_wire(event="request", payload={"actual_model_prompt": "private prompt"})
        for record in current_preview_scope().drain_nowait():
            yield record

    channel = ConsoleChannel(
        process=process, enabled=True, bot_prefix="", media_dir=str(tmp_path)
    )
    payload = {
        "sender_id": "alice",
        "content_parts": [TextContent(text="hi")],
        "meta": {
            "session_id": "local-session",
            "request_context": {
                "capabilities": {"model_debug": debug, "tl_preview": True},
            },
        },
    }
    events = [line async for line in channel.stream_one(payload)]
    diagnostics = [json.loads(line[5:]) for line in events if '"model_log"' in line]
    assert len(diagnostics) == int(debug)
    if debug:
        assert diagnostics[0]["payload"]["actual_model_prompt"] == "private prompt"
        assert diagnostics[0]["run_id"] == "local-session"
