"""Service integration tests using the memory repository and a deterministic model."""

import asyncio
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from qwenpaw.server.api import create_api
from qwenpaw.server.config import ServerConfig
from qwenpaw.server.contracts import (
    RuntimeServices,
    ExecutionContext,
    current_execution,
)
from qwenpaw.server.worker import Worker


@pytest_asyncio.fixture
async def store():
    from qwenpaw.server.storage import MemoryRepository

    offset = [0.0]
    repo = MemoryRepository(
        clock=lambda: time.time() + offset[0],
        prefix="qp_test_service_",
    )
    await repo.open()
    await repo.migrate()
    repo._test_clock_offset = offset
    try:
        yield repo
    finally:
        await repo.close()


@pytest.fixture
def config(tmp_path):
    definition = tmp_path / "assistant.json"
    definition.write_text(
        json.dumps(
            {
                "version": "test-v1",
                "name": "test",
                "system_prompt": "Help",
                "model": "test",
                "auto_memory": False,
            }
        )
    )
    return ServerConfig(
        database_url="unused",
        service_token="a" * 32,
        internal_token="b" * 32,
        definition_path=definition,
        model_api_key="mock",
    )


def model_factory(_definition):
    from agentscope.model import OpenAIChatModel
    from agentscope.credential import OpenAICredential

    async def handler(request):
        body = json.loads(request.content)
        content = next(
            item["content"]
            for item in reversed(body["messages"])
            if item["role"] == "user"
        )
        text = "answer:" + (
            content
            if isinstance(content, str)
            else "".join(part.get("text", "") for part in content)
        )
        chunks = [
            {
                "id": "chat-test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": text},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chat-test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        ]
        content = (
            "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, text=content, headers={"content-type": "text/event-stream"}
        )

    return OpenAIChatModel(
        credential=OpenAICredential(api_key="mock", base_url="https://mock.invalid/v1"),
        model="test",
        max_retries=0,
        client_kwargs={
            "http_client": httpx.AsyncClient(transport=httpx.MockTransport(handler))
        },
    )


@pytest.mark.asyncio
async def test_api_worker_ownership_replay_and_state(store, config):
    tools = SimpleNamespace(stop_session=AsyncMock())
    app = create_api(config, store, None)
    headers = {"Authorization": "Bearer " + "a" * 32, "X-QwenPaw-User": "alice"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        payload = {
            "usrid": "alice",
            "sessionid": "same",
            "channelid": "web",
            "request_id": "1",
            "message": "hello",
        }
        assert (await client.post("/v1/runs", json=payload)).status_code == 401
        created = await client.post("/v1/runs", headers=headers, json=payload)
        assert created.status_code == 202, created.text
        run = created.json()
        repeat = await client.post("/v1/runs", headers=headers, json=payload)
        assert repeat.json()["id"] == run["id"]
        other_headers = {**headers, "X-QwenPaw-User": "bob"}
        assert (
            await client.get(f"/v1/runs/{run['id']}", headers=other_headers)
        ).status_code == 404
        worker = Worker(config, RuntimeServices(store, tools, model_factory))
        claimed = await store.claim(worker.id, 60, 4)
        await worker.execute(claimed)
        assert current_execution.get() is None
        finished = await store.get_run("alice", run["id"])
        assert finished["status"] == "completed"
        session = await store.session("alice", run["session_id"])
        assert session["state"]
        response = await client.get(f"/v1/runs/{run['id']}/events", headers=headers)
        assert "answer:hello" in response.text
        events = await store.events("alice", run["id"], 0, 1000)
        replay = await client.get(
            f"/v1/runs/{run['id']}/events",
            headers={**headers, "Last-Event-ID": str(events[-1]["seq"])},
        )
        assert "event: end" in replay.text and "answer:hello" not in replay.text


@pytest.mark.asyncio
async def test_context_and_users_are_isolated(store, config):
    definition = config.definition()
    await store.put_definition(definition.version, definition.model_dump(mode="json"))
    for user in ("a", "b"):
        await store.submit(
            user, "web", "same", "req", {"message": user}, definition.version
        )
    workers = [
        Worker(
            config,
            RuntimeServices(
                store, SimpleNamespace(stop_session=AsyncMock()), model_factory
            ),
        )
        for _ in range(2)
    ]
    runs = [await store.claim(worker.id, 60, 4) for worker in workers]
    assert runs[0]["session_id"] != runs[1]["session_id"]
    await asyncio.gather(*(worker.execute(run) for worker, run in zip(workers, runs)))
    for run in runs:
        session = await store.session(run["user_id"], run["session_id"])
        assert (await store.get_run(run["user_id"], run["id"]))["status"] == "completed"
        assert "answer:" + run["user_id"] in json.dumps(session["state"])
    assert current_execution.get() is None


@pytest.mark.asyncio
async def test_concurrent_idempotency_and_user_quota(store):
    payload = {"message": "same"}
    duplicates = await asyncio.gather(
        *(store.submit("a", "web", "s", "same-key", payload, "v") for _ in range(12))
    )
    assert len({item["id"] for item in duplicates}) == 1
    for i in range(8):
        await store.submit("a", "web", f"s{i}", f"r{i}", payload, "v")
    claims = await asyncio.gather(
        *(store.claim(f"worker-{i}", 60, 2) for i in range(8))
    )
    assert sum(item is not None for item in claims) <= 2
    await store.submit("b", "web", "s", "r", payload, "v")
    next_run = await store.claim("another-worker", 60, 2)
    assert next_run["user_id"] == "b"


@pytest.mark.asyncio
async def test_memory_scope_vectors_and_delete_cancels_jobs(store, config):
    from qwenpaw.server.memory import MemoryService

    definition = config.definition().model_copy(
        update={"embedding_model": "mock-embedding", "auto_memory": True}
    )
    await store.put_definition(definition.version, definition.model_dump(mode="json"))
    fake = SimpleNamespace(
        embeddings=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    data=[SimpleNamespace(embedding=[1.0, 0.0, 0.0])]
                )
            )
        ),
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(
                                    content='{"facts":["likes tea"]}'
                                )
                            )
                        ]
                    )
                )
            )
        ),
    )
    memory = MemoryService(config, store, fake)
    for user in ("a", "b"):
        await store.submit(
            user, "web", "s", "r", {"message": "likes tea"}, definition.version
        )
        run = await store.claim("worker", 60, 4)
        ctx = ExecutionContext(
            user,
            run["session_id"],
            "web",
            run["id"],
            definition.version,
            run["epoch"],
            "worker",
        )
        await memory.remember(ctx, "private " + user, definition)
        await store.finish(ctx, "completed", {})
    result = await memory.search("a", "private", definition)
    assert len(result) == 1 and result[0]["text"] == "private a"
    await store.delete_memories("a")
    assert await memory.search("a", "private", definition) == []
    assert await memory.step()  # Only b's extraction remains eligible.
    assert not await memory.step()
    assert any(
        item["text"] == "likes tea"
        for item in await memory.search("b", "tea", definition)
    )
    assert await store.memories("a", "") == []


@pytest.mark.asyncio
async def test_files_ownership_and_import_requires_idle_session(
    store, config, tmp_path
):
    from qwenpaw.server.controller import SandboxController
    from qwenpaw.server.contracts import Conflict, NotFound

    run = await store.submit("a", "web", "s", "r", {"message": "hi"}, "v")
    file_id = str(uuid.uuid4())
    record = await store.add_file("a", file_id, "../../display.txt", "key", 3)
    with pytest.raises(NotFound):
        await store.file("b", record["id"])
    controller = SandboxController(
        config.model_copy(update={"workspace_root": tmp_path}),
        store,
        None,
        SimpleNamespace(get=AsyncMock(return_value=b"abc")),
    )
    await controller.initialize()
    with pytest.raises(Conflict):
        await controller.import_file(
            run["session_id"], {"user_id": "a", "file_id": file_id}
        )
    await store.cancel("a", run["id"])
    imported = await controller.import_file(
        run["session_id"], {"user_id": "a", "file_id": file_id}
    )
    assert (tmp_path / run["session_id"] / imported["path"]).read_bytes() == b"abc"


@pytest.mark.asyncio
async def test_delayed_reaper_cannot_stop_new_run(store, config, tmp_path):
    from qwenpaw.server.controller import SandboxController

    first = await store.submit("a", "web", "s", "r1", {"message": "hi"}, "v")
    claimed = await store.claim("w", 60, 4)
    ctx = ExecutionContext(
        "a", claimed["session_id"], "web", claimed["id"], "v", claimed["epoch"], "w"
    )
    await store.finish(ctx, "completed", {})
    await store.submit("a", "web", "s", "r2", {"message": "next"}, "v")
    second = await store.claim("w2", 60, 4)
    kube = SimpleNamespace(stop=AsyncMock())
    controller = SandboxController(
        config.model_copy(update={"workspace_root": tmp_path}), store, kube, None
    )
    await controller.initialize()
    await controller.stop(
        first["session_id"], {"run_id": first["id"], "epoch": claimed["epoch"]}
    )
    kube.stop.assert_not_awaited()
    assert second["epoch"] > claimed["epoch"]


@pytest.mark.asyncio
async def test_cancel_wins_finish_and_pending_tools_become_unknown(store):
    from qwenpaw.server.contracts import Conflict

    run = await store.submit("a", "web", "s", "r", {"message": "hi"}, "v")
    claimed = await store.claim("w", 60, 4)
    ctx = ExecutionContext(
        "a", run["session_id"], "web", run["id"], "v", claimed["epoch"], "w"
    )
    await store.begin_tool(ctx, "call", "shell", {"command": "do-something"})
    approval = await store.request_approval(ctx, "call", 60)
    await store.cancel("a", run["id"])
    with pytest.raises(Conflict):
        await store.begin_tool(ctx, "new-call", "shell", {})
    with pytest.raises(Conflict):
        await store.finish(ctx, "completed", {})
    async with store.sandbox_stop_guard(
        ctx.session_id, {"run_id": ctx.run_id, "epoch": ctx.epoch}
    ) as allowed:
        assert allowed
    await store.finish(ctx, "completed", {})
    assert (await store.get_run("a", run["id"]))["status"] == "cancelled"
    async with store._db.connection() as conn:
        result = await conn.execute(
            f"SELECT status FROM {store.table('tool_calls')} WHERE run_id=%s",
            (run["id"],),
        )
        assert (await result.fetchone())["status"] == "unknown"
        result = await conn.execute(
            f"SELECT status FROM {store.table('approvals')} WHERE id=%s",
            (approval["id"],),
        )
        assert (await result.fetchone())["status"] == "expired"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["model", "tool", "save"])
async def test_worker_lease_loss_fences_every_stage(store, config, monkeypatch, stage):
    from qwenpaw.server.contracts import LeaseLost
    import qwenpaw.runtime.runtime as runtime_module

    definition = config.definition()
    await store.put_definition(definition.version, definition.model_dump(mode="json"))
    run = await store.submit(
        "a", "web", "s", "r", {"message": "hi"}, definition.version
    )
    entered = asyncio.Event()
    tools = SimpleNamespace(stop_session=AsyncMock())
    worker = Worker(config, RuntimeServices(store, tools, model_factory))
    claimed = await store.claim(worker.id, 60, 4)
    ctx = ExecutionContext(
        "a",
        run["session_id"],
        "web",
        run["id"],
        definition.version,
        claimed["epoch"],
        worker.id,
    )

    class BlockedRuntime:
        def __init__(self, **kwargs):
            pass

        async def run(self, request):
            if stage == "tool":
                await store.begin_tool(ctx, "side-effect", "shell", {})
            if stage == "save":
                yield {"type": "partial", "text": "durable before failure"}
            entered.set()
            await asyncio.Event().wait()
            yield {}

    monkeypatch.setattr(runtime_module, "Runtime", BlockedRuntime)
    execution = asyncio.create_task(worker.execute(claimed))
    await asyncio.wait_for(entered.wait(), 5)
    if stage == "save":
        for _ in range(100):
            if await store.events("a", run["id"], 0, 100):
                break
            await asyncio.sleep(0.01)
        assert await store.events("a", run["id"], 0, 100)
    store._test_clock_offset[0] += 61
    await asyncio.wait_for(execution, 5)
    tools.stop_session.assert_awaited_once()
    with pytest.raises(LeaseLost):
        await store.append(ctx, [{"type": "stale"}])
    await store.submit("a", "web", "s", "r2", {"message": "next"}, definition.version)
    assert await store.claim("next", 60, 4) is None
    async with store.sandbox_stop_guard(
        run["session_id"],
        expected={"run_id": run["id"], "epoch": claimed["epoch"]},
    ) as allowed:
        assert allowed
    await store.interrupt(run["id"], claimed["epoch"])
    next_run = await store.claim("next", 60, 4)
    assert next_run and next_run["epoch"] > claimed["epoch"]
    events = await store.events("a", run["id"], 0, 100)
    assert events[-1]["payload"]["status"] == "interrupted"
    assert current_execution.get() is None


@pytest.mark.asyncio
async def test_redis_outage_does_not_break_persisted_replay(store, config):
    from qwenpaw.server.notifications import Notifications

    notifier = Notifications("redis://127.0.0.1:1/0")
    await notifier.start()
    app = create_api(config, store, None, notifier)
    run = await store.submit("a", "web", "s", "r", {"message": "hello"}, "v")
    claimed = await store.claim("w", 60, 4)
    ctx = ExecutionContext(
        "a", run["session_id"], "web", run["id"], "v", claimed["epoch"], "w"
    )
    await store.append(ctx, [{"type": "text", "text": "persisted"}])
    await store.finish(ctx, "completed", {})
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/v1/runs/{run['id']}/events",
                headers={"Authorization": "Bearer " + "a" * 32, "X-QwenPaw-User": "a"},
            )
            assert response.status_code == 200
            assert "persisted" in response.text and "event: end" in response.text
    finally:
        await notifier.close()


@pytest.mark.asyncio
async def test_artifact_reference_is_private_and_memory_index_retries(
    store, config, tmp_path
):
    from qwenpaw.server.controller import SandboxController
    from qwenpaw.server.memory import MemoryService
    from qwenpaw.server.contracts import NotFound

    definition = config.definition().model_copy(
        update={
            "embedding_model": "test-embedding",
            "auto_memory": True,
            "public_knowledge": ("public manual",),
        }
    )
    await store.put_definition(definition.version, definition.model_dump(mode="json"))
    run = await store.submit(
        "a", "web", "s", "r", {"message": "likes tea"}, definition.version
    )
    claimed = await store.claim("w", 60, 4)
    ctx = ExecutionContext(
        "a",
        run["session_id"],
        "web",
        run["id"],
        definition.version,
        claimed["epoch"],
        "w",
    )
    objects = SimpleNamespace(put=AsyncMock(), delete=AsyncMock())
    controller = SandboxController(config, store, None, objects)
    record = await controller.publish(ctx, "output.txt", b"private")
    assert (await store.file("a", record["id"]))["size"] == 7
    with pytest.raises(NotFound):
        await store.file("b", record["id"])
    await store.finish(ctx, "completed", {})
    fake = SimpleNamespace(
        embeddings=SimpleNamespace(
            create=AsyncMock(side_effect=TimeoutError("embedding unavailable"))
        ),
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(
                                    content='{"facts":["likes tea"]}'
                                )
                            )
                        ]
                    )
                )
            )
        ),
    )
    memory = MemoryService(config, store, fake)
    assert await memory.step()
    fake.embeddings.create.side_effect = None
    fake.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(embedding=[1.0, 0.0, 0.0])]
    )
    assert await memory.step()
    assert not await memory.step()
    assert len(await memory.search("a", "tea", definition)) == 1
    assert await memory.knowledge("manual", definition) == ["public manual"]


@pytest.mark.asyncio
async def test_durable_gateway_approval_and_expiry(store, config):
    from qwenpaw.server.tools import ToolGateway
    from qwenpaw.server.contracts import NotFound

    definition = config.definition().model_copy(update={"tools": ()})
    from qwenpaw.server.config import ToolDefinition

    definition = definition.model_copy(
        update={
            "tools": (
                ToolDefinition(
                    name="shell",
                    description="test",
                    input_schema={"type": "object"},
                    approval=True,
                ),
            )
        }
    )
    run = await store.submit(
        "a", "web", "s", "r", {"message": "hi"}, definition.version
    )
    claimed = await store.claim("w", 60, 4)
    ctx = ExecutionContext(
        "a",
        run["session_id"],
        "web",
        run["id"],
        definition.version,
        claimed["epoch"],
        "w",
    )
    remote = SimpleNamespace(invoke=AsyncMock(return_value={"status": "ok"}))
    gateway = ToolGateway(
        RuntimeServices(store, remote, None),
        definition,
        SimpleNamespace(approval_timeout=5),
    )
    call = asyncio.create_task(gateway.invoke(ctx, "call", "shell", {}))
    for _ in range(100):
        events = await store.events("a", run["id"], 0, 100)
        if any(e["payload"].get("type") == "approval" for e in events):
            break
        await asyncio.sleep(0.01)
    approval = next(
        e["payload"]["approval"]
        for e in events
        if e["payload"].get("type") == "approval"
    )
    with pytest.raises(NotFound):
        await store.decide("b", approval["id"], True)
    await store.decide("a", approval["id"], True)
    assert (await asyncio.wait_for(call, 3))["status"] == "ok"
    with pytest.raises(asyncio.CancelledError):
        await gateway.invoke(ctx, "expired", "shell", {})
    assert (await store.get_run("a", run["id"]))["cancel_requested"]
    remote.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_tool_result_stops_agent_without_model_retry(store, config):
    from agentscope.model import OpenAIChatModel
    from agentscope.credential import OpenAICredential

    body = json.loads(config.definition_path.read_text())
    body["tools"] = [
        {"name": "shell", "description": "test", "input_schema": {"type": "object"}}
    ]
    config.definition_path.write_text(json.dumps(body))
    definition = config.definition()
    await store.put_definition(definition.version, definition.model_dump(mode="json"))
    requests = []

    async def handler(request):
        requests.append(request)
        chunks = [
            {
                "id": "tool",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call",
                                    "type": "function",
                                    "function": {"name": "shell", "arguments": "{}"},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "tool",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        return httpx.Response(
            200,
            text="".join("data: " + json.dumps(item) + "\n\n" for item in chunks)
            + "data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    def factory(_definition):
        return OpenAIChatModel(
            credential=OpenAICredential(api_key="mock"),
            model="test",
            max_retries=0,
            client_kwargs={
                "http_client": httpx.AsyncClient(transport=httpx.MockTransport(handler))
            },
        )

    async def stop_session(session_id, run_id=None, epoch=None):
        async with store.sandbox_stop_guard(
            session_id, {"run_id": run_id, "epoch": epoch}
        ) as allowed:
            assert allowed

    remote = SimpleNamespace(
        invoke=AsyncMock(return_value={"status": "unknown"}),
        stop_session=AsyncMock(side_effect=stop_session),
    )
    worker = Worker(config, RuntimeServices(store, remote, factory))
    run = await store.submit(
        "a", "web", "s", "r", {"message": "test"}, definition.version
    )
    claimed = await store.claim(worker.id, 60, 4)
    await asyncio.wait_for(worker.execute(claimed), 5)
    assert (await store.get_run("a", run["id"]))["status"] == "interrupted"
    assert len(requests) == 1
    remote.invoke.assert_awaited_once()
