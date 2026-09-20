"""Loopback API -> Worker -> HTTP Controller -> HTTP sandbox acceptance.

The loopback sandbox server substitutes for Kubernetes in this test;
this does not claim process/Pod/NetworkPolicy isolation verification.
"""

import asyncio
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from qwenpaw.server.api import create_api
from qwenpaw.server.contracts import RuntimeServices
from qwenpaw.server.controller import SandboxController, create_controller_app
from qwenpaw.server.sandbox_app import create_sandbox_app
from qwenpaw.server.tools import RemoteTools
from qwenpaw.server.worker import Worker
from tests.unit.server.test_service import config, store  # noqa: F401
from tests.unit.server.test_streaming_acceptance import gated_model, listening


def python_command(source: str) -> str:
    """Return a shell command that runs *source* on this interpreter.

    Both argv entries are double-quoted, a form POSIX ``sh`` and Windows
    ``cmd.exe`` both accept, so the sandbox tests do not depend on a
    POSIX-only shell.  *source* must therefore avoid double quotes.
    """
    if '"' in source:
        raise ValueError("source must not contain double quotes")
    return f'"{sys.executable}" -c "{source}"'


@pytest.mark.asyncio
async def test_shell_logs_cross_real_http_before_completion(store, config, tmp_path):
    body = json.loads(config.definition_path.read_text())
    body["tools"] = [
        {
            "name": "shell",
            "description": "read-only test",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        }
    ]
    config.definition_path.write_text(json.dumps(body))
    config = config.model_copy(update={"workspace_root": tmp_path / "workspaces"})
    sandbox_root = tmp_path / "sandbox"
    sandbox_root.mkdir()
    secret = "sandbox-test-token"

    async with listening(create_sandbox_app(sandbox_root, secret)) as sandbox_url:

        async def ensure(_session):
            return sandbox_url

        async def stop(_session):
            async with httpx.AsyncClient() as c:
                response = await c.post(
                    sandbox_url + "/stop", headers={"Authorization": "Bearer " + secret}
                )
                response.raise_for_status()

        kube = SimpleNamespace(ensure=ensure, token=lambda _: secret, stop=stop)
        controller = SandboxController(config, store, kube, None)
        async with listening(
            create_controller_app(controller, config)
        ) as controller_url:
            config = config.model_copy(update={"controller_url": controller_url})
            remote = RemoteTools(config, store)
            gate = asyncio.Event()
            gate.set()
            # Pause between outputs so the client must observe a live result,
            # not a buffered HTTP body returned after process termination.
            factory = gated_model(
                gate,
                python_command(
                    "import sys,time;sys.stdout.write('before');"
                    "sys.stdout.flush();time.sleep(1);"
                    "sys.stdout.write('after');sys.stdout.flush()"
                ),
            )
            worker = Worker(config, RuntimeServices(store, remote, factory))
            headers = {"Authorization": "Bearer " + "a" * 32, "X-QwenPaw-User": "alice"}
            task = None
            try:
                async with (
                    listening(create_api(config, store, None)) as api_url,
                    httpx.AsyncClient(
                        base_url=api_url, headers=headers, timeout=10
                    ) as client,
                ):
                    response = await client.post(
                        "/v1/runs",
                        json={
                            "usrid": "alice",
                            "channelid": "web",
                            "sessionid": "same",
                            "request_id": "pipeline",
                            "message": "run",
                        },
                    )
                    assert response.status_code == 202
                    run = response.json()
                    task = asyncio.create_task(
                        worker.execute(await store.claim(worker.id, 60, 4))
                    )
                    events = []
                    async with client.stream(
                        "GET", f"/v1/runs/{run['id']}/events"
                    ) as response:
                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            event = json.loads(line[6:])
                            events.append(event)
                            if (
                                event.get("type") == "tool_output"
                                and event.get("text") == "before"
                            ):
                                assert not task.done()
                                assert (await store.get_run("alice", run["id"]))[
                                    "status"
                                ] == "running"
                    await asyncio.wait_for(task, 5)
                    output = [e for e in events if e.get("type") == "tool_output"]
                    assert "".join(e["text"] for e in output) == "beforeafter"
                    assert {e["tool_call_id"] for e in output} == {"model-call-1"}
                    terminal = [e for e in events if e.get("type") == "terminal"]
                    assert len(terminal) == 1
                    assert terminal[0]["status"] == "completed"
            finally:
                if task and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await remote.close()
