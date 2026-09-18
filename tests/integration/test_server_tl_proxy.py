"""Real HTTP: shared Worker -> Node TL proxy -> fake model, with approval.

Build test-tools/tl-llm-proxy before running. Never uses external model keys.
"""

import asyncio
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic import ValidationError

from qwenpaw.server.agent import create_model_factory
from qwenpaw.server.config import AssistantDefinition, ServerConfig
from qwenpaw.server.contracts import RuntimeServices
from qwenpaw.server.memory import MemoryService
from qwenpaw.server.storage import MemoryRepository
from qwenpaw.server.worker import Worker

ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def proxy():
    entry = ROOT / "test-tools/tl-llm-proxy/dist/index.js"
    node = shutil.which("node")
    if not node or not entry.exists():
        pytest.skip("Build the Node TL proxy to run the real HTTP integration")
    seen = []
    responses = [
        {
            "version": 1,
            "type": "tool_calls",
            "calls": [{"name": "add", "arguments": {"a": 2, "b": 3}}],
        },
        {"version": 1, "type": "final", "content": "The result is 5."},
        {"facts": ["likes arithmetic"]},
    ]

    class FakeModel(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(payload)
            body = json.dumps(responses.pop(0))
            if payload.get("stream"):
                event = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": body},
                            "finish_reason": "stop",
                        }
                    ]
                }
                content = (
                    "data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"
                ).encode()
                mime = "text/event-stream"
            else:
                content = json.dumps(
                    {
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": body},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ).encode()
                mime = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    code = f"""const {{createProxy,loadConfig}} = await import({json.dumps(entry.as_uri())});
const proxy = createProxy(loadConfig());
const addr = await proxy.start();
console.log(addr.port);
process.on('SIGTERM', async () => {{await proxy.close(); process.exit(0);}});"""
    env = {
        "UPSTREAM_PROVIDER": "openai-compatible",
        "UPSTREAM_MODEL": "fake-local",
        "UPSTREAM_BASE_URL": f"http://127.0.0.1:{upstream.server_port}/v1",
        "UPSTREAM_API_KEY": "synthetic-key",
        "TL_PROXY_HOST": "127.0.0.1",
        "TL_PROXY_PORT": "0",
        "LOG_LEVEL": "error",
        "AUTH_MODE": "local",
    }
    process = await asyncio.create_subprocess_exec(
        node,
        "--input-type=module",
        "-e",
        code,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        line = await asyncio.wait_for(process.stdout.readline(), 10)
        assert line, (await process.stderr.read()).decode()
        yield f"http://127.0.0.1:{int(line)}", seen
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        await asyncio.to_thread(upstream.shutdown)
        upstream.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_server_tl_remote_tool_approval_and_memory(proxy, tmp_path):
    url, seen = proxy
    definition = AssistantDefinition(
        version="tl-server-test",
        system_prompt="Use add to calculate.",
        model="local-label",
        model_protocol="tl",
        base_url=url,
        tools=(
            {
                "name": "add",
                "description": "add numbers",
                "approval": True,
                "input_schema": {
                    "type": "object",
                    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a", "b"],
                    "additionalProperties": False,
                },
            },
        ),
    )
    path = tmp_path / "definition.json"
    path.write_text(definition.model_dump_json())
    config = ServerConfig(
        database_url="memory://",
        service_token="s" * 32,
        internal_token="i" * 32,
        definition_path=path,
    )
    repo = MemoryRepository()
    await repo.migrate()
    await repo.put_definition(definition.version, definition.model_dump(mode="json"))
    factory = create_model_factory(config)
    remote = SimpleNamespace(
        invoke=AsyncMock(return_value={"status": "ok", "result": 5}),
        stop_session=AsyncMock(),
    )
    worker = Worker(config, RuntimeServices(repo, remote, factory))
    await repo.submit(
        "alice",
        "web",
        "same",
        "r",
        {"message": "Calculate 2+3. I like arithmetic."},
        definition.version,
    )
    run = await repo.claim(worker.id, 60, 4)
    task = asyncio.create_task(worker.execute(run))
    try:
        async with asyncio.timeout(10):
            while True:
                events = await repo.events("alice", run["id"], 0)
                approval = next(
                    (
                        e["payload"]["approval"]
                        for e in events
                        if e["payload"].get("type") == "approval"
                    ),
                    None,
                )
                if approval:
                    break
                if task.done():
                    await task
                    pytest.fail("Worker terminated before approval")
                await asyncio.sleep(0.01)
            remote.invoke.assert_not_awaited()
            await repo.decide("alice", approval["id"], True)
            await task
        assert (await repo.get_run("alice", run["id"]))["status"] == "completed"
        remote.invoke.assert_awaited_once()
        assert await MemoryService(config, repo).step()
        assert (await repo.memories("alice", "arithmetic"))[0][
            "text"
        ] == "likes arithmetic"
        assert await repo.memories("bob", "arithmetic") == []
        assert len(seen) == 3
        for request in seen:
            assert request["model"] == "fake-local"
            assert not set(request) & {
                "tools",
                "tool_choice",
                "functions",
                "function_call",
                "response_format",
            }
            assert [m["role"] for m in request["messages"]] == ["system", "user"]
        assert "tool_result" in seen[1]["messages"][1]["content"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await factory.close()
        await repo.close()


def test_server_tl_configuration_is_explicit():
    base = {
        "version": "v",
        "model": "label",
        "system_prompt": "help",
        "model_protocol": "tl",
    }
    with pytest.raises(ValidationError):
        AssistantDefinition(**base)
    with pytest.raises(ValidationError):
        AssistantDefinition(
            **base, base_url="https://tl.example", embedding_model="unsupported"
        )
    with pytest.raises(ValidationError):
        AssistantDefinition(**base, base_url="https://user:password@tl.example")
    assert (
        AssistantDefinition(**base, base_url="https://tl.example/company").tl_config
        is None
    )
