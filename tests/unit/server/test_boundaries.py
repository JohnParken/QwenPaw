import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from qwenpaw.server.config import ServerConfig, AssistantDefinition
from qwenpaw.server.contracts import ExecutionContext, RuntimeServices, Conflict
from qwenpaw.server.controller import Kubernetes
from qwenpaw.server.sandbox_app import create_sandbox_app
from qwenpaw.server.tools import ToolGateway


@pytest.mark.asyncio
async def test_stop_fences_waiting_call_and_unknown_result(tmp_path):
    app = create_sandbox_app(tmp_path, "secret")
    headers = {"Authorization": "Bearer secret"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://sandbox"
    ) as client:
        task = asyncio.create_task(
            client.post(
                "/invoke",
                headers=headers,
                json={
                    "call_id": "long",
                    "epoch": 7,
                    "name": "shell",
                    "timeout": 60,
                    "arguments": {"command": "sleep 30"},
                },
            )
        )
        await asyncio.sleep(0.1)
        queued = asyncio.create_task(
            client.post(
                "/invoke",
                headers=headers,
                json={
                    "call_id": "next",
                    "epoch": 7,
                    "name": "write_file",
                    "arguments": {"path": "bad", "content": "should not execute"},
                },
            )
        )
        await asyncio.sleep(0.05)
        assert (await client.post("/stop", headers=headers)).status_code == 200
        assert (await task).json()["status"] == "unknown"
        assert (await queued).status_code == 409
        assert not (tmp_path / "bad").exists()


@pytest.mark.asyncio
async def test_sandbox_epoch_symlink_and_timeout(tmp_path):
    (tmp_path / "escape").symlink_to(tmp_path.parent)
    headers = {"Authorization": "Bearer secret"}
    app = create_sandbox_app(tmp_path, "secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://sandbox"
    ) as client:
        body = {
            "call_id": "a",
            "epoch": 2,
            "name": "read_file",
            "arguments": {"path": "escape/file"},
        }
        assert (await client.post("/invoke", headers=headers, json=body)).json()[
            "status"
        ] == "error"
        body.update(call_id="b", epoch=1)
        assert (
            await client.post("/invoke", headers=headers, json=body)
        ).status_code == 409
        body.update(
            call_id="c",
            epoch=3,
            name="shell",
            timeout=0.05,
            arguments={"command": "sleep 10"},
        )
        result = await client.post("/invoke", headers=headers, json=body)
        assert result.json()["status"] == "unknown"
        assert (
            result.json()
            == (await client.post("/invoke", headers=headers, json=body)).json()
        )


def test_kubernetes_pod_has_only_session_mount_and_no_platform_secrets():
    kube = object.__new__(Kubernetes)
    kube.config = ServerConfig(
        database_url="db",
        service_token="private-service",
        internal_token="private-controller",
        definition_path=Path("definition"),
    )
    session_id = "95f3b6eb-46f0-472a-842e-609516a27de4"
    spec = kube.manifest(session_id)["spec"]
    assert not spec["automountServiceAccountToken"]
    container = spec["containers"][0]
    assert container["securityContext"]["readOnlyRootFilesystem"]
    assert container["volumeMounts"][0]["subPath"] == session_id
    assert "private-controller" not in str(spec)
    assert "private-service" not in str(spec)
    assert "database_url" not in str(spec)


@pytest.mark.asyncio
async def test_duplicate_or_failed_remote_tool_never_falls_back():
    repo = SimpleNamespace(
        begin_tool=AsyncMock(return_value={"created": False, "status": "unknown"}),
        end_tool=AsyncMock(),
    )
    remote = SimpleNamespace(invoke=AsyncMock())
    definition = AssistantDefinition(
        version="v",
        system_prompt="x",
        model="x",
        tools=[
            {
                "name": "shell",
                "description": "shell",
                "input_schema": {"type": "object"},
            }
        ],
    )
    gateway = ToolGateway(
        RuntimeServices(repo, remote, None),
        definition,
        SimpleNamespace(approval_timeout=1),
    )
    ctx = ExecutionContext("u", "s", "c", "r", "v", 1, "w")
    with pytest.raises(Conflict):
        await gateway.invoke(ctx, "call", "shell", {})
    remote.invoke.assert_not_awaited()
    repo.begin_tool.return_value = {"created": True}
    remote.invoke.side_effect = TimeoutError("remote offline")
    from qwenpaw.server.contracts import ToolOutcomeUnknown

    with pytest.raises(ToolOutcomeUnknown):
        await gateway.invoke(ctx, "call2", "shell", {})
    assert repo.end_tool.await_args.args[2] == "unknown"


@pytest.mark.asyncio
async def test_stdio_mcp_connection_is_session_local_and_reusable(
    tmp_path, monkeypatch
):
    import json
    import sys
    from qwenpaw.server.sandbox_tools import SandboxTools

    fixture = Path(__file__).resolve().parents[2] / "fixtures/mcp/stdio_echo_server.py"
    monkeypatch.setenv(
        "QWENPAW_SERVER_MCP_JSON",
        json.dumps(
            {
                "echo": {
                    "command": sys.executable,
                    "args": [str(fixture)],
                }
            }
        ),
    )
    proxy = SandboxTools(tmp_path)
    try:
        first = await asyncio.wait_for(proxy.mcp("echo", "echo", {"text": "first"}), 15)
        connection = proxy._mcp_workers["echo"][1]
        second = await asyncio.wait_for(
            proxy.mcp("echo", "echo", {"text": "second"}), 15
        )
        assert proxy._mcp_workers["echo"][1] is connection
        assert first["content"][0]["text"] == "first"
        assert second["content"][0]["text"] == "second"
    finally:
        await proxy.close()
    assert connection.done()


@pytest.mark.asyncio
async def test_browser_recreated_while_workspace_persists(tmp_path):
    from qwenpaw.server.sandbox_tools import SandboxTools

    if os.environ.get("QWENPAW_TEST_BROWSER") != "1":
        pytest.skip("Set QWENPAW_TEST_BROWSER=1 with Playwright Chromium installed")
    first = SandboxTools(tmp_path)
    try:
        await first.write_file("saved.txt", "persistent")
        page = await first.browser(
            "navigate", url="data:text/html,<title>session one</title>"
        )
        assert page["title"] == "session one"
    finally:
        await first.close()
    second = SandboxTools(tmp_path)
    try:
        assert (await second.read_file("saved.txt"))["content"] == "persistent"
        assert (await second.browser())["url"] == "about:blank"
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_idle_worker_uses_one_claim_loop_for_all_slots():
    from qwenpaw.server.worker import Worker

    repo = SimpleNamespace(
        claim=AsyncMock(return_value=None),
        expired=AsyncMock(return_value=[]),
        prune_events=AsyncMock(),
    )
    config = SimpleNamespace(concurrency=100, lease_seconds=60, per_user_concurrency=4)
    worker = Worker(config, RuntimeServices(repo, None, None))
    serving = asyncio.create_task(worker.serve())
    try:
        await asyncio.sleep(0.25)
        assert 1 <= repo.claim.await_count <= 4
    finally:
        serving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await serving
