import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import httpx
import pytest

from qwenpaw.server.contracts import Conflict, ExecutionContext
from qwenpaw.server.controller import SandboxController
from qwenpaw.server.sandbox_app import create_sandbox_app
from qwenpaw.server.sandbox_tools import MAX_OUTPUT, SandboxTools


def python_command(source: str) -> str:
    """Return a shell command that runs *source* on this interpreter.

    Both argv entries are double-quoted, a form POSIX ``sh`` and Windows
    ``cmd.exe`` both accept, so the sandbox tests do not depend on a
    POSIX-only shell.  *source* must therefore avoid double quotes.
    """
    if '"' in source:
        raise ValueError("source must not contain double quotes")
    return f'"{sys.executable}" -c "{source}"'


def _events(response):
    return [json.loads(line) for line in response.text.splitlines()]


@pytest.fixture
def client(tmp_path: Path):
    app = create_sandbox_app(tmp_path, "secret")
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sandbox"
    )


@pytest.mark.asyncio
async def test_shell_streams_output_and_deduplicates(client):
    payload = {
        "call_id": "stream-1",
        "name": "shell",
        "stream": True,
        "arguments": {
            "command": python_command(
                "import sys,time;"
                "sys.stdout.buffer.write(b'before');sys.stdout.buffer.flush();"
                "time.sleep(0.02);"
                "sys.stdout.buffer.write(bytes([0o342,0o200,0o235]));"
                "sys.stdout.buffer.flush();"
                "sys.exit(7)"
            )
        },
    }
    async with client as c:
        first = await c.post(
            "/invoke", headers={"Authorization": "Bearer secret"}, json=payload
        )
        second = await c.post(
            "/invoke", headers={"Authorization": "Bearer secret"}, json=payload
        )

    events = _events(first)
    assert [event["type"] for event in events] == ["output", "output", "result"]
    assert events[0]["text"] == "before"
    assert events[1]["text"] == "”"
    assert events[-1]["result"]["result"]["exit_code"] == 7
    assert _events(second) == [events[-1]]


@pytest.mark.asyncio
async def test_shell_stream_is_bounded_and_timeout_is_unknown(client):
    bounded_command = python_command("import sys; sys.stdout.write('x' * 1100000)")
    payload = {
        "call_id": "bounded",
        "name": "shell",
        "stream": True,
        "arguments": {"command": bounded_command},
    }
    async with client as c:
        response = await c.post(
            "/invoke", headers={"Authorization": "Bearer secret"}, json=payload
        )
        events = _events(response)
        assert len(events[-1]["result"]["result"]["stdout"]) == MAX_OUTPUT
        assert (
            sum(
                len(event.get("text", "").encode())
                for event in events
                if event["type"] == "output" and event["stream"] == "stdout"
            )
            <= MAX_OUTPUT
        )

        timeout = await c.post(
            "/invoke",
            headers={"Authorization": "Bearer secret"},
            json={
                "call_id": "timeout",
                "name": "shell",
                "stream": True,
                "timeout": 0.05,
                "arguments": {"command": python_command("import time; time.sleep(1)")},
            },
        )
    assert _events(timeout)[-1]["result"]["error"] == "timeout"


@pytest.mark.asyncio
async def test_stop_cancels_stream(client):
    payload = {
        "call_id": "cancel",
        "name": "shell",
        "stream": True,
        "timeout": 10,
        "arguments": {
            "command": python_command(
                "import sys,time;sys.stdout.write('started');"
                "sys.stdout.flush();time.sleep(5)"
            )
        },
    }
    async with client as c:
        request = asyncio.create_task(
            c.post("/invoke", headers={"Authorization": "Bearer secret"}, json=payload)
        )
        await asyncio.sleep(0.05)
        stopped = await c.post("/stop", headers={"Authorization": "Bearer secret"})
        response = await asyncio.wait_for(request, 2)
    assert stopped.json() == {"stopped": True}
    assert _events(response)[-1]["result"]["error"] == "interrupted"


@pytest.mark.asyncio
async def test_shell_callback_decodes_split_utf8_before_completion(tmp_path):
    output = []
    started = asyncio.Event()

    async def emit(stream, text):
        output.append((stream, text))
        started.set()

    tools = SandboxTools(tmp_path)
    task = asyncio.create_task(
        tools.shell(
            python_command(
                "import sys,time;"
                "sys.stdout.buffer.write(bytes([0o342]));sys.stdout.buffer.flush();"
                "time.sleep(0.02);"
                "sys.stdout.buffer.write(bytes([0o200,0o235]));"
                "sys.stdout.buffer.flush();"
                "time.sleep(0.1)"
            ),
            on_output=emit,
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    assert not task.done()
    result = await task
    assert output == [("stdout", "”")]
    assert result["stdout"] == "”"


@pytest.mark.asyncio
async def test_controller_persists_stream_events_immediately():
    class Repo:
        def __init__(self):
            self.events = []

        async def append(self, ctx, payloads):
            self.events.extend(payloads)

    class Response:
        async def aiter_bytes(self):
            yield b'{"type":"output","stream":"stdout","text":"hel'
            yield b'lo"}\n{"type":"result","result":{"call_id":"call-1","status":"ok"}}\n'

    repo = Repo()
    controller = SandboxController.__new__(SandboxController)
    controller.repo = repo
    ctx = ExecutionContext("u", "s", "c", "r", "v", 1, "w")
    result = await controller._read_stream(Response(), ctx, "call-1")

    assert result == {"call_id": "call-1", "status": "ok"}
    assert repo.events == [
        {
            "type": "tool_output",
            "tool_call_id": "call-1",
            "stream": "stdout",
            "text": "hello",
        }
    ]


@pytest.mark.asyncio
async def test_stop_does_not_block_when_stream_queue_is_full(tmp_path):
    backpressure_command = python_command(
        "import sys,time; sys.stdout.write('x' * 2000000); time.sleep(5)"
    )
    app = create_sandbox_app(tmp_path, "secret")
    invoke = next(route.endpoint for route in app.routes if route.path == "/invoke")
    stop = next(route.endpoint for route in app.routes if route.path == "/stop")
    response = await invoke(
        {
            "call_id": "backpressure",
            "name": "shell",
            "stream": True,
            "timeout": 10,
            "arguments": {"command": backpressure_command},
        },
        authorization="Bearer secret",
    )
    try:
        await asyncio.sleep(0.05)
        result = await asyncio.wait_for(stop(authorization="Bearer secret"), timeout=1)
        assert result == {"stopped": True}
    finally:
        await response.body_iterator.aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "POSIX process-group semantics: Windows has no os.killpg/signal.SIGKILL, "
        "and os.kill(pid, 0) terminates the process instead of probing it"
    ),
)
async def test_timeout_kills_sigterm_ignoring_descendant(tmp_path):
    child_code = (
        "import signal,time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "time.sleep(30)"
    )
    parent_code = (
        "import subprocess,sys,time;"
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        "print(child.pid, flush=True);"
        "time.sleep(30)"
    )
    command = python_command(parent_code)
    output = []

    async def emit(_stream, text):
        output.append(text)

    child_pid = None
    try:
        with pytest.raises(asyncio.TimeoutError):
            await SandboxTools(tmp_path).shell(command, timeout=0.5, on_output=emit)
        child_pid = int("".join(output).strip())
        for _ in range(50):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("SIGTERM-ignoring descendant survived process cleanup")
    finally:
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_call_id", ["other", "call-1"])
async def test_controller_rejects_late_output_and_wrong_terminal_call_id(
    terminal_call_id,
):
    class Repo:
        async def append(self, _ctx, _payloads):
            raise AssertionError("late output must not be persisted")

    class Response:
        async def aiter_bytes(self):
            yield (
                json.dumps(
                    {
                        "type": "result",
                        "result": {"call_id": terminal_call_id, "status": "ok"},
                    }
                ).encode()
                + b'\n{"type":"output","stream":"stdout","text":"late"}\n'
            )

    controller = SandboxController.__new__(SandboxController)
    controller.repo = Repo()
    ctx = ExecutionContext("u", "s", "c", "r", "v", 1, "w")
    with pytest.raises(Conflict):
        await controller._read_stream(Response(), ctx, "call-1")


@pytest.mark.asyncio
async def test_controller_bounds_aggregate_output_per_stream():
    class Repo:
        def __init__(self):
            self.events = []

        async def append(self, _ctx, payloads):
            self.events.extend(payloads)

    half = "x" * (MAX_OUTPUT // 2 + 1)
    data = b"".join(
        [
            json.dumps({"type": "output", "stream": "stdout", "text": half}).encode()
            + b"\n",
            json.dumps({"type": "output", "stream": "stdout", "text": half}).encode()
            + b"\n",
            b'{"type":"result","result":{"call_id":"call-1","status":"ok"}}\n',
        ]
    )

    class Response:
        async def aiter_bytes(self):
            yield data

    repo = Repo()
    controller = SandboxController.__new__(SandboxController)
    controller.repo = repo
    ctx = ExecutionContext("u", "s", "c", "r", "v", 1, "w")
    with pytest.raises(Conflict):
        await controller._read_stream(Response(), ctx, "call-1")
    assert len(repo.events) == 1


@pytest.mark.asyncio
async def test_controller_accepts_maximal_escaped_terminal_result():
    text = "\x00" * MAX_OUTPUT
    line = (
        json.dumps(
            {
                "type": "result",
                "result": {
                    "call_id": "call-1",
                    "status": "ok",
                    "result": {"stdout": text, "stderr": text},
                },
            },
            ensure_ascii=False,
        ).encode()
        + b"\n"
    )

    class Response:
        async def aiter_bytes(self):
            yield line

    controller = SandboxController.__new__(SandboxController)
    controller.repo = type("Repo", (), {"append": None})()
    ctx = ExecutionContext("u", "s", "c", "r", "v", 1, "w")
    result = await controller._read_stream(Response(), ctx, "call-1")
    assert result["result"]["stdout"] == text
