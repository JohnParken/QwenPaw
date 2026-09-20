import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from qwenpaw.server.api import create_api
from qwenpaw.server.objects import MemoryObjects
from qwenpaw.server.contracts import RuntimeServices
from qwenpaw.server.worker import Worker
from tests.unit.server.test_service import config, store, model_factory  # noqa: F401


@pytest.mark.asyncio
async def test_tl_owned_file_is_extracted_before_worker_without_sandbox(store, config):
    definition = json.loads(config.definition_path.read_text())
    definition.update(model_protocol="tl", base_url="http://tl.invalid")
    config.definition_path.write_text(json.dumps(definition))
    app = create_api(config, store, MemoryObjects())
    headers = {"Authorization": "Bearer " + "a" * 32, "X-QwenPaw-User": "alice"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
    ) as client:
        response = await client.post(
            "/v1/files", files={"file": ("notes.txt", b"private document fact 123")}
        )
        assert response.status_code == 201
        file_id = response.json()["id"]
        body = {
            "usrid": "bob",
            "channelid": "web",
            "sessionid": "same",
            "request_id": "one",
            "message": "summarize",
            "attachments": [file_id],
        }
        assert (
            await client.post("/v1/runs", json=body, headers={"X-QwenPaw-User": "bob"})
        ).status_code == 404
        assert (
            await client.get(
                f"/v1/files/{file_id}/content", headers={"X-QwenPaw-User": "bob"}
            )
        ).status_code == 404
        assert (
            await client.get(f"/v1/files/{file_id}/content")
        ).content == b"private document fact 123"
        body["usrid"] = "alice"
        response = await client.post("/v1/runs", json=body)
        assert response.status_code == 202, response.text
        run = response.json()
        assert run["input"]["attachment_text"][0]["text"] == "private document fact 123"
        duplicate = await client.post("/v1/runs", json=body)
        assert duplicate.json()["id"] == run["id"]
        tools = SimpleNamespace(invoke=AsyncMock(), stop_session=AsyncMock())
        worker = Worker(config, RuntimeServices(store, tools, model_factory))
        claimed = await store.claim(worker.id, 60, 4)
        await worker.execute(claimed)
        assert (await store.get_run("alice", run["id"]))["status"] == "completed"
        events = await store.events("alice", run["id"], 0)
        assert "private document fact 123" in json.dumps(
            [event["payload"] for event in events]
        )
        tools.invoke.assert_not_called()


@pytest.mark.asyncio
async def test_personal_parse_route_accepts_bytes_not_paths(monkeypatch):
    from qwenpaw.app.routers import console

    monkeypatch.setattr(
        console, "get_agent_for_request", AsyncMock(return_value=object())
    )
    app = FastAPI()
    app.include_router(console.router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/console/attachments/parse",
            files={"file": ("notes.txt", "你好 TL".encode())},
        )
        assert response.status_code == 200
        assert response.json()["text"] == "你好 TL"
        response = await client.post(
            "/api/console/attachments/parse",
            files={"file": ("image.png", b"fake binary")},
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_local_object_quota_is_bounded():
    from qwenpaw.server.contracts import Conflict

    objects = MemoryObjects(max_bytes=3)
    await objects.put("a", b"123")
    with pytest.raises(Conflict):
        await objects.put("b", b"x")
    await objects.delete("a")
    await objects.put("b", b"x")
    assert await objects.get("b") == b"x"
