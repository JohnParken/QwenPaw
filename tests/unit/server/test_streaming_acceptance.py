"""Real socket SSE acceptance with deterministic, explicitly gated inference."""

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import uvicorn

from qwenpaw.server.agent import current_tool_call
from qwenpaw.server.api import create_api
from qwenpaw.server.contracts import RuntimeServices, NotFound
from qwenpaw.server.tools import ToolGateway
from qwenpaw.server.worker import Worker
from tests.unit.server.test_service import config, store  # noqa: F401


@asynccontextmanager
async def listening(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


def gated_model(model_gate, command="echo hello"):
    from agentscope.model import OpenAIChatModel
    from agentscope.credential import OpenAICredential

    def wire(delta, reason=None):
        return (
            "data: "
            + json.dumps(
                {
                    "id": "model",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
                }
            )
            + "\n\n"
        ).encode()

    class Stream(httpx.AsyncByteStream):
        def __init__(self, final):
            self.final = final

        async def __aiter__(self):
            if self.final:
                yield wire({"role": "assistant", "content": "检查完成。"})
                yield wire({}, "stop")
            else:
                yield wire({"role": "assistant", "content": "先检查。"})
                await model_gate.wait()
                yield wire(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "model-call-1",
                                "type": "function",
                                "function": {
                                    "name": "shell",
                                    "arguments": json.dumps({"command": command}),
                                },
                            }
                        ]
                    }
                )
                yield wire({}, "tool_calls")
            yield b"data: [DONE]\n\n"

    async def handler(request):
        body = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Stream(any(m["role"] == "tool" for m in body["messages"])),
        )

    return lambda _: OpenAIChatModel(
        credential=OpenAICredential(api_key="mock", base_url="https://mock.invalid/v1"),
        model="test",
        max_retries=0,
        client_kwargs={
            "http_client": httpx.AsyncClient(transport=httpx.MockTransport(handler))
        },
    )


@pytest.mark.asyncio
async def test_live_text_tool_approval_reconnect_and_durable_history(store, config):
    body = json.loads(config.definition_path.read_text())
    body["tools"] = [
        {
            "name": "shell",
            "description": "check",
            "approval": True,
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        }
    ]
    config.definition_path.write_text(json.dumps(body))
    model_gate, tool_gate = asyncio.Event(), asyncio.Event()
    dispatched = []

    async def invoke(ctx, call_id, name, arguments, timeout):
        dispatched.append(call_id)
        await store.append(
            ctx,
            [
                {
                    "type": "tool_output",
                    "tool_call_id": call_id,
                    "stream": "stdout",
                    "text": "hello\n",
                }
            ],
        )
        await tool_gate.wait()
        return {
            "status": "ok",
            "result": {"stdout": "hello\n", "stderr": "", "exit_code": 0},
        }

    worker = Worker(
        config,
        RuntimeServices(
            store,
            SimpleNamespace(invoke=invoke, stop_session=AsyncMock()),
            gated_model(model_gate),
        ),
    )
    headers = {"Authorization": "Bearer " + "a" * 32, "X-QwenPaw-User": "alice"}
    app = create_api(config, store, None)
    seen, received = [], asyncio.Queue()
    run_task = reader = None

    async with (
        listening(app) as base,
        httpx.AsyncClient(base_url=base, headers=headers, timeout=15) as client,
    ):
        response = await client.post(
            "/v1/runs",
            json={
                "usrid": "alice",
                "sessionid": "same",
                "channelid": "web",
                "request_id": "one",
                "message": "检查",
            },
        )
        assert response.status_code == 202
        run = response.json()

        async def read(after=0):
            cursor = after
            async with client.stream(
                "GET",
                f"/v1/runs/{run['id']}/events",
                headers={"Last-Event-ID": str(after)},
            ) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("id: "):
                        cursor = int(line[4:])
                    if line.startswith("data: "):
                        value = json.loads(line[6:])
                        seen.append((cursor, value))
                        await received.put((cursor, value))

        async def until(predicate):
            async with asyncio.timeout(10):
                while True:
                    item = await received.get()
                    if predicate(item[1]):
                        return item

        try:
            reader = asyncio.create_task(read())
            run_task = asyncio.create_task(
                worker.execute(await store.claim(worker.id, 60, 4))
            )
            await until(lambda e: e.get("text") == "先检查。")
            assert not run_task.done() and not model_gate.is_set()
            model_gate.set()
            cursor, event = await until(lambda e: e.get("type") == "approval")
            assert event["tool_call_id"] == "model-call-1" and not dispatched
            approval = event["approval"]["id"]
            assert (
                await client.post(
                    f"/v1/approvals/{approval}/decision",
                    headers={"X-QwenPaw-User": "bob"},
                    json={"approved": True},
                )
            ).status_code == 404
            # Disconnecting transport does not cancel the task or approve tools.
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            assert not (await store.get_run("alice", run["id"]))["cancel_requested"]
            while not received.empty():
                received.get_nowait()
            seen.clear()
            reader = asyncio.create_task(read(cursor))
            assert (
                await client.post(
                    f"/v1/approvals/{approval}/decision", json={"approved": True}
                )
            ).status_code == 200
            await until(lambda e: e.get("type") == "tool_output")
            assert not run_task.done() and dispatched == ["model-call-1"]
            tool_gate.set()
            await asyncio.wait_for(run_task, 10)
            await asyncio.wait_for(reader, 10)
            assert (await store.get_run("alice", run["id"]))["status"] == "completed"
            persisted = [seq for seq, e in seen if e.get("type") or e.get("object")]
            assert persisted == sorted(set(persisted)) and min(persisted) > cursor
            states = [e["status"] for _, e in seen if e.get("type") == "tool"]
            assert states == ["running", "completed"]
            assert current_tool_call.get() is None
            # Compact history outlives detailed replay journal retention.
            store._test_clock_offset[0] += 8 * 86400
            await store.prune_events()
            assert not await store.events("alice", run["id"], 0)
            rows = (
                await client.get(f"/v1/sessions/{run['session_id']}/messages")
            ).json()
            timeline = next(
                r["payload"] for r in rows if r["payload"].get("type") == "run_timeline"
            )
            assert "检查完成。" in json.dumps(timeline, ensure_ascii=False)
            tool = next(e for e in timeline["events"] if e.get("type") == "tool")
            assert (
                tool["tool_call_id"] == "model-call-1" and tool["status"] == "completed"
            )
            assert not any(e.get("object") == "preview" for e in timeline["events"])
        finally:
            model_gate.set()
            tool_gate.set()
            for task in (reader, run_task):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(t for t in (reader, run_task) if t), return_exceptions=True
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,expected",
    [
        ({"status": "error", "error": "bad"}, "failed"),
        ({"status": "ok", "result": {"exit_code": 7}}, "failed"),
        ({"status": "ok", "result": {"exit_code": 0}}, "completed"),
    ],
)
async def test_tool_status_is_durable_and_duplicate_never_dispatches(
    store, config, result, expected
):
    from qwenpaw.server.config import ToolDefinition
    from qwenpaw.server.contracts import ExecutionContext

    definition = config.definition().model_copy(
        update={
            "tools": (
                ToolDefinition(
                    name="shell", description="test", input_schema={"type": "object"}
                ),
            )
        }
    )
    await store.submit("alice", "web", "s", "r", {"message": "hi"}, definition.version)
    run = await store.claim("worker", 60, 1)
    ctx = ExecutionContext(
        "alice",
        run["session_id"],
        "web",
        run["id"],
        definition.version,
        run["epoch"],
        "worker",
    )
    remote = SimpleNamespace(invoke=AsyncMock(return_value=result))
    gateway = ToolGateway(RuntimeServices(store, remote, None), definition, config)
    await gateway.invoke(ctx, "call", "shell", {})
    await gateway.invoke(ctx, "call", "shell", {})
    remote.invoke.assert_awaited_once()
    events = await store.events("alice", run["id"], 0)
    assert [e["payload"]["status"] for e in events] == [
        "preparing",
        "running",
        expected,
    ]
    with pytest.raises(NotFound):
        await store.events("bob", run["id"], 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["commit", "error", "cancel"])
async def test_worker_streams_tl_preview_without_committing_unvalidated_text(
    store, config, outcome
):
    from qwenpaw.providers.tl_chat_model import TLChatModel
    from qwenpaw.providers.tl_errors import TLError
    from qwenpaw.providers.tl_preview import current_preview_scope
    from tests.unit.providers.test_tl_preview_model import PausedTransport

    body = json.loads(config.definition_path.read_text())
    body["tools"] = [
        {"name": "noop", "description": "unused", "input_schema": {"type": "object"}}
    ]
    config.definition_path.write_text(json.dumps(body))
    definition = config.definition()
    await store.put_definition(definition.version, definition.model_dump(mode="json"))
    transport = PausedTransport(
        failure=TLError("sse_decode", "EOF", "eof") if outcome == "error" else None
    )
    worker = Worker(
        config,
        RuntimeServices(
            store,
            SimpleNamespace(stop_session=AsyncMock()),
            lambda _: TLChatModel("local", transport),
        ),
    )
    await store.submit(
        "alice", "web", "same", "r", {"message": "hi"}, definition.version
    )
    run = await store.claim(worker.id, 60, 4)
    task = asyncio.create_task(worker.execute(run))
    try:
        await asyncio.wait_for(transport.paused.wait(), 5)
        async with asyncio.timeout(5):
            while True:
                events = [
                    e["payload"]
                    for e in await store.events("alice", run["id"], 0, 1000)
                ]
                if any(e.get("type") == "preview_update" for e in events):
                    break
                await asyncio.sleep(0.01)
        assert not task.done()
        assert not any(e.get("object") == "content" and e.get("text") for e in events)
        if outcome == "cancel":
            await store.cancel("alice", run["id"])
        else:
            transport.resume.set()
        await asyncio.wait_for(task, 5)
        events = [e["payload"] for e in await store.events("alice", run["id"], 0, 1000)]
        expected = {"commit": "completed", "error": "failed", "cancel": "cancelled"}[
            outcome
        ]
        assert events[-1] == {"type": "terminal", "status": expected}
        assert current_preview_scope() is None
        if outcome == "commit":
            clear = next(
                i for i, e in enumerate(events) if e.get("type") == "preview_clear"
            )
            formal = next(
                i
                for i, e in enumerate(events)
                if e.get("object") == "content" and e.get("text")
            )
            assert clear < formal
        rows = await store.messages("alice", run["session_id"])
        assert all("preview_update" not in json.dumps(r["payload"]) for r in rows)
    finally:
        transport.resume.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_during_text_does_not_deadlock_cleanup_barrier(store, config):
    definition = config.definition()
    await store.put_definition(definition.version, definition.model_dump(mode="json"))
    gate = asyncio.Event()
    tools = SimpleNamespace(stop_session=AsyncMock())
    worker = Worker(config, RuntimeServices(store, tools, gated_model(gate)))
    await store.submit("alice", "web", "s", "r", {"message": "hi"}, definition.version)
    run = await store.claim(worker.id, 60, 4)
    task = asyncio.create_task(worker.execute(run))
    try:
        async with asyncio.timeout(5):
            while True:
                events = [
                    e["payload"] for e in await store.events("alice", run["id"], 0)
                ]
                if any(e.get("text") == "先检查。" for e in events):
                    break
                await asyncio.sleep(0.01)
        await store.cancel("alice", run["id"])
        await asyncio.wait_for(task, 5)
        assert (await store.get_run("alice", run["id"]))["status"] == "cancelled"
        rows = await store.messages("alice", run["session_id"])
        timeline = next(
            r["payload"] for r in rows if r["payload"].get("type") == "run_timeline"
        )
        assert "先检查。" in json.dumps(timeline, ensure_ascii=False)
        tools.stop_session.assert_awaited_once()
    finally:
        gate.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_timeline_ignores_runtime_heartbeat_without_message_id():
    from qwenpaw.server.timeline import compact_timeline

    result = compact_timeline(
        [
            {"object": "message", "type": "heartbeat", "data": None},
            {
                "object": "message",
                "id": "m",
                "role": "assistant",
                "content": [{"type": "text", "text": "done"}],
            },
        ],
        "run",
        "completed",
    )
    assert result is not None
    assert len(result["events"]) == 1
    assert result["events"][0]["id"] == "m"
