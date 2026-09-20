import sys
from pathlib import Path

import httpx
import pytest

from qwenpaw.server.sandbox_app import create_sandbox_app


def python_command(source: str) -> str:
    """Return a shell command that runs *source* on this interpreter.

    Both argv entries are double-quoted, a form POSIX ``sh`` and Windows
    ``cmd.exe`` both accept, so the sandbox tests do not depend on a
    POSIX-only shell.  *source* must therefore avoid double quotes.
    """
    if '"' in source:
        raise ValueError("source must not contain double quotes")
    return f'"{sys.executable}" -c "{source}"'


@pytest.fixture
def client(tmp_path: Path):
    app = create_sandbox_app(tmp_path, "secret")
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sandbox"
    )


@pytest.mark.asyncio
async def test_auth_files_and_dedup(client, tmp_path):
    async with client as c:
        assert (await c.get("/health")).status_code == 401
        headers = {"Authorization": "Bearer secret"}
        payload = {
            "call_id": "a",
            "name": "write_file",
            "arguments": {"path": "x", "content": "hello"},
        }
        first = await c.post("/invoke", headers=headers, json=payload)
        second = await c.post("/invoke", headers=headers, json=payload)
        assert first.json() == second.json()
        assert (tmp_path / "x").read_text() == "hello"
        conflict = {**payload, "arguments": {"path": "x", "content": "other"}}
        assert (
            await c.post("/invoke", headers=headers, json=conflict)
        ).status_code == 409


@pytest.mark.asyncio
async def test_traversal_is_rejected(client):
    async with client as c:
        response = await c.post(
            "/invoke",
            headers={"Authorization": "Bearer secret"},
            json={
                "call_id": "b",
                "name": "read_file",
                "arguments": {"path": "../outside"},
            },
        )
        assert response.json()["result"]["status"] == "error"


@pytest.mark.asyncio
async def test_shell_bounded_and_epoch_fenced(client):
    async with client as c:
        headers = {"Authorization": "Bearer secret"}
        payload = {
            "call_id": "shell",
            "name": "shell",
            "arguments": {
                "command": python_command(
                    "import sys; sys.stdout.write('x' * 1100000)"
                )
            },
        }
        result = await c.post("/invoke", headers=headers, json=payload)
        assert len(result.json()["result"]["stdout"]) == 1024 * 1024
        stopped = await c.post("/stop", headers=headers)
        assert stopped.json()["stopped"] is True
        stale = await c.post(
            "/invoke",
            headers=headers,
            json={"call_id": "old", "name": "list_files", "epoch": 0},
        )
        assert stale.status_code == 409
