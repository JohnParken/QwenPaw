"""Focused wire and streaming tests for the standalone TL transport."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from pydantic import ValidationError

from qwenpaw.providers.tl_config import TLConfig
from qwenpaw.providers.tl_errors import TLError
import qwenpaw.providers.tl_transport as tl_transport_module
from qwenpaw.providers.tl_transport import TLTransport


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            await asyncio.sleep(0)
            yield chunk


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_debug_wire_logs_request_response_and_sse(caplog, stream):
    caplog.set_level(logging.DEBUG, logger="qwenpaw.providers.tl_wire")

    async def handler(request):
        if request.url.path.endswith("init_session"):
            return httpx.Response(200, json={"code": 0, "data": {"session_id": "s1"}})
        if not stream:
            return httpx.Response(200, json={"code": 0, "data": {"txt": "answer-marker"}})
        raw = ('event: chunk\ndata: {"content":"answer-marker"}\n\n'
               'event: done\ndata: {"finished":true}\n\n').encode()
        return httpx.Response(200, stream=_ChunkStream([raw[:20], raw[20:]]))

    async with _client(handler) as client:
        transport = TLTransport("http://tl", TLConfig(), api_key="hidden-key", client=client)
        result = [x async for x in transport.iter_text("system-marker", "user-marker", stream=stream)]
    assert result == ["answer-marker"]
    records = [json.loads(r.message.split("TL_WIRE ", 1)[1])
               for r in caplog.records if "TL_WIRE " in r.message]
    assert sum(r["event"] == "request" for r in records) == 2
    assert sum(r["event"] == "response_headers" for r in records) == 2
    assert any(r["event"] == "response_body" for r in records)
    assert sum(r["event"] == "sse_event" for r in records) == (2 if stream else 0)
    assert all(r.get("attempt_id") and r.get("request_id") for r in records)
    requests = [r for r in records if r["event"] == "request"]
    assert {r["url"] for r in requests} == {
        "http://tl/chatbbc/init_session",
        "http://tl/chatbbc/chat",
    }
    assert all(r["headers"]["authorization"] == "[REDACTED]" for r in requests)
    assert "system-marker" in caplog.text and "user-marker" in caplog.text
    assert "answer-marker" in caplog.text and "hidden-key" not in caplog.text


def test_debug_sse_callback_observes_invalid_event_before_rejection():
    seen = []
    parser = tl_transport_module._SSEParser(1024, lambda name, data: seen.append((name, data)))
    with pytest.raises(TLError):
        parser.feed(b'event: chunk\ndata: {broken}\n\n')
    assert seen == [("chunk", "{broken}")]


@pytest.mark.parametrize("event", ["", "done", "end", "message"])
def test_non_error_done_marker_compatibility(event):
    parser = tl_transport_module._SSEParser(max_event_bytes=1024)
    assert parser.feed(f"event: {event}\ndata: [DONE]\n\n".encode()) == [
        ("terminal", "[DONE]")
    ]


@pytest.mark.parametrize(
    "detail",
    [
        "Upstream connection reset (TypeError; cause: Error ECONNRESET)",
        "Upstream SSE data is not valid JSON",
        "Upstream stream ended before [DONE]",
    ],
)
def test_sse_error_preserves_proxy_diagnostic(detail):
    parser = tl_transport_module._SSEParser(max_event_bytes=4096)
    payload = json.dumps({"message": detail, "prompt": "private prompt"})
    with pytest.raises(TLError) as error:
        parser.feed(f"event: error\ndata: {payload}\n\n".encode())
    assert error.value.kind == "remote_error"
    assert error.value.stage == "sse_decode"
    assert error.value.message == f"TL SSE error event: {detail}"
    assert "private prompt" not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [
        "[DONE]", "{bad", "null", '"secret"', "[]",
        '{"message":123}', '{"message":"  "}',
        '{"error":{"message":"private prompt"}}',
    ],
)
def test_sse_error_malformed_diagnostic_keeps_fallback(payload):
    parser = tl_transport_module._SSEParser(max_event_bytes=4096)
    with pytest.raises(TLError) as error:
        parser.feed(f"event: error\ndata: {payload}\n\n".encode())
    assert error.value.message == "TL SSE error event"
    assert error.value.kind == "remote_error"


def test_sse_error_redacts_credentials_and_bounds_message():
    parser = tl_transport_module._SSEParser(max_event_bytes=8192)
    detail = (
        'ECONNRESET https://user:urlsecret@host/path?key=querysecret '
        'Bearer bearersecret; "api_key": "json secret"; '
        'password=passwordsecret; Cookie=session=cookiesecret; '
        '\n\x1b\x00' + 'x' * 1000
    )
    payload = json.dumps({"message": detail})
    with pytest.raises(TLError) as error:
        parser.feed(f"event: error\ndata: {payload}\n\n".encode())
    message = error.value.message
    assert "ECONNRESET" in message
    for secret in (
        "urlsecret", "querysecret", "bearersecret", "json secret",
        "passwordsecret", "cookiesecret", "\n", "\x1b", "\x00",
    ):
        assert secret not in message
    assert len(message) <= 512


@pytest.mark.asyncio
@pytest.mark.parametrize("success_code", [0, 0.0])
async def test_wire_shape_and_non_stream_text(success_code) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("init_session"):
            return httpx.Response(
                200,
                json={
                    "code": success_code,
                    "message": "success",
                    "data": {"session_id": "s1"},
                },
            )
        return httpx.Response(
            200,
            json={
                "code": success_code,
                "message": "success",
                "data": {"txt": "结果\n"},
            },
        )

    config = TLConfig(
        app_id="app",
        tr_code="chat",
        tr_version="2",
        system_prompt_variable_name="instructions",
    )
    client = _client(handler)
    transport = TLTransport(
        "http://tl.example/prefix/",
        config,
        api_key="secret",
        custom_headers={"X-Trace": "test"},
        client=client,
    )
    try:
        assert [
            text
            async for text in transport.iter_text(
                "system", "payload", stream=False
            )
        ] == ["结果\n"]
    finally:
        await client.aclose()

    assert [str(request.url) for request in requests] == [
        "http://tl.example/prefix/chatbbc/init_session",
        "http://tl.example/prefix/chatbbc/chat",
    ]
    init, chat = (json.loads(request.content) for request in requests)
    assert init["appId"] == "app"
    assert init["data"] == {
        "prompt_variables": [{"name": "instructions", "value": "system"}],
    }
    assert chat["data"] == {
        "session_id": "s1",
        "txt": "payload",
        "files": [],
        "stream": False,
    }
    assert init["requestId"] != chat["requestId"]
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert requests[1].headers["x-trace"] == "test"


@pytest.mark.asyncio
async def test_request_log_contains_final_url_and_redacted_headers(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="qwenpaw.providers.tl_wire")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("init_session"):
            return httpx.Response(200, json={"code": 0, "data": {"session_id": "s"}})
        return httpx.Response(200, json={"code": 0, "data": {"txt": "ok"}})

    async with _client(handler) as client:
        transport = TLTransport(
            "https://tl.example/base/",
            TLConfig(),
            api_key="api-secret",
            custom_headers={"Cookie": "session=cookie-secret", "X-Trace": "trace-secret"},
            client=client,
        )
        [text async for text in transport.iter_text("sys", "user", stream=False)]

    records = [json.loads(r.message.split("TL_WIRE ", 1)[1])
               for r in caplog.records
               if "TL_WIRE " in r.message
               and json.loads(r.message.split("TL_WIRE ", 1)[1])["event"] == "request"]
    assert len(records) == 2
    for record in records:
        assert record["url"] == "https://tl.example/base/chatbbc/" + (
            "init_session" if record["path"].endswith("init_session") else "chat"
        )
        assert record["headers"]["authorization"] == "[REDACTED]"
        assert record["headers"]["cookie"] == "[REDACTED]"
        assert record["headers"]["x-trace"] == "[REDACTED]"
        assert record["headers"]["content-type"] == "application/json"
        expected_length = len(
            json.dumps(
                record["payload"],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        assert record["headers"]["content-length"] == str(expected_length)
    rendered = caplog.text
    for secret in ("api-secret", "cookie-secret", "trace-secret"):
        assert secret not in rendered


@pytest.mark.asyncio
async def test_sse_parser_preserves_utf8_and_crlf_across_packets() -> None:
    requests: list[httpx.Request] = []
    sse = (
        ": keepalive\r\n"
        "event: chunk\r\n"
        'data: {"content":\r\n'
        'data: "前后  "}\r\n'
        "\r\n"
        "event: done\r\n"
        'data: {"finished":true}\r\n'
        "\r\n"
    ).encode("utf-8")

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("init_session"):
            return httpx.Response(
                200, json={"code": 0, "data": {"session_id": "s"}}
            )
        return httpx.Response(
            200,
            stream=_ChunkStream(
                [b"\xef\xbb", b"\xbf" + sse[:25], sse[25:40], sse[40:]]
            ),
            headers={"content-type": "text/event-stream"},
        )

    client = _client(handler)
    transport = TLTransport("http://tl", TLConfig(), client=client)
    try:
        result = [text async for text in transport.iter_text("sys", "user")]
    finally:
        await client.aclose()
    assert result == ["前后  "]
    assert transport.last_termination == "done"
    assert requests[1].headers["accept"] == "text/event-stream"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        ('event: done\ndata: {"finished":false}\n\n', "unfinished"),
        ("event: error\ndata: [DONE]\n\n", "remote_error"),
        ("event: strange\ndata: {}\n\n", "unknown_event"),
        ("event: chunk\ndata: {bad}\n\n", "invalid_json"),
        ('event: chunk\ndata: {"content":"x"}\n', "unexpected_eof"),
    ],
)
async def test_sse_failures_never_return_partial_success(
    payload: str, kind: str
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("init_session"):
            return httpx.Response(
                200, json={"code": 0, "data": {"session_id": "s"}}
            )
        return httpx.Response(
            200,
            content=payload.encode(),
            headers={"content-type": "text/event-stream"},
        )

    client = _client(handler)
    transport = TLTransport("http://tl", TLConfig(), client=client)
    try:
        with pytest.raises(TLError) as error:
            [text async for text in transport.iter_text("sys", "user")]
    finally:
        await client.aclose()
    assert error.value.stage == "sse_decode"
    assert error.value.kind == kind


@pytest.mark.asyncio
async def test_response_budget_counts_all_sse_content_bytes() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("init_session"):
            return httpx.Response(
                200, json={"code": 0, "data": {"session_id": "s"}}
            )
        payload = (
            'event: chunk\ndata: {"content":"1234"}\n\n'
            'event: chunk\ndata: {"content":"5678"}\n\n'
            'event: done\ndata: {"finished":true}\n\n'
        )
        return httpx.Response(
            200,
            content=payload.encode(),
            headers={"content-type": "text/event-stream"},
        )

    client = _client(handler)
    transport = TLTransport(
        "http://tl", TLConfig(max_response_bytes=6), client=client
    )
    try:
        with pytest.raises(TLError, match="max_response_bytes") as error:
            [text async for text in transport.iter_text("sys", "user")]
    finally:
        await client.aclose()
    assert error.value.kind == "response_budget"


@pytest.mark.asyncio
async def test_request_budget_is_checked_before_network() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    config = TLConfig(max_request_bytes=64)
    client = _client(handler)
    transport = TLTransport("http://tl", config, client=client)
    try:
        with pytest.raises(TLError, match="max_request_bytes") as error:
            await transport.init_session("x" * 200)
    finally:
        await client.aclose()
    assert error.value.stage == "init_session"
    assert calls == 0


@pytest.mark.asyncio
async def test_owned_client_is_private_to_each_attempt_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[httpx.AsyncClient] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("init_session"):
            return httpx.Response(
                200, json={"code": 0, "data": {"session_id": "s"}}
            )
        return httpx.Response(200, json={"code": 0, "data": {"txt": "ok"}})

    real_client = httpx.AsyncClient

    def make_client(**kwargs):
        client = real_client(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(tl_transport_module.httpx, "AsyncClient", make_client)
    transport = TLTransport("http://tl", TLConfig())

    async def collect(payload: str) -> list[str]:
        return [
            text
            async for text in transport.iter_text("sys", payload, stream=False)
        ]

    assert await asyncio.gather(collect("one"), collect("two")) == [
        ["ok"],
        ["ok"],
    ]
    assert len(clients) == 2
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
async def test_deadline_survives_resuming_generator_in_new_task() -> None:
    release = asyncio.Event()

    class HangingStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'event: chunk\ndata: {"content":"A"}\n\n'
            await release.wait()

        async def aclose(self) -> None:
            self.closed = True

    stream = HangingStream()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("init_session"):
            return httpx.Response(
                200, json={"code": 0, "data": {"session_id": "s"}}
            )
        return httpx.Response(
            200,
            stream=stream,
            headers={"content-type": "text/event-stream"},
        )

    client = _client(handler)
    transport = TLTransport(
        "http://tl",
        TLConfig(timeout_seconds=0.05),
        client=client,
    )
    iterator = transport.iter_text("sys", "user")
    try:
        assert await asyncio.create_task(iterator.__anext__()) == "A"
        with pytest.raises(TLError) as error:
            await asyncio.create_task(iterator.__anext__())
        assert error.value.kind == "timeout"
        assert error.value.stage == "sse_decode"
    finally:
        await iterator.aclose()
        await client.aclose()
    assert stream.closed


def test_config_rejects_invalid_mode_limits_and_non_finite_timeouts() -> None:
    with pytest.raises(ValidationError):
        TLConfig(tool_calling_mode="native")
    with pytest.raises(ValidationError):
        TLConfig(max_response_bytes=0)
    with pytest.raises(ValidationError):
        TLConfig(json_correction_max_attempts=2)
    with pytest.raises(ValidationError):
        TLConfig(timeout_seconds=float("inf"))
    with pytest.raises(ValidationError):
        TLConfig(max_request_bytes=True)
