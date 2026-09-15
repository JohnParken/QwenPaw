from collections.abc import Iterator
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qwenpaw.office.api import create_app
from qwenpaw.office.config import OfficeSettings
from qwenpaw.office.models import MessageCreate, RequestIdentity
from qwenpaw.office.runtime import RuntimeSignal
from qwenpaw.office.service import OfficeService
from qwenpaw.office.storage import MemoryObjectStore, MemoryRepository


class _Skill:
    available = True

    def as_dict(self):
        return {"ready": True, "available": True, "version": "1.0.0"}


class _Bundle(dict):
    ready = True


class _Runtime:
    async def stream(self, **kwargs):
        history = kwargs["history"]
        prefix = "follow-up: " if history else ""
        yield RuntimeSignal("message.delta", {"delta": prefix + "done"})
        yield RuntimeSignal("runtime.response", {"usage": {"input_tokens": 1}})


class _SlowRuntime:
    async def stream(self, **_kwargs):
        yield RuntimeSignal("message.delta", {"delta": "working"})
        await asyncio.Event().wait()


def _headers(request_id: str = "request-1", tenant: str = "tenant-a", user: str = "user-a") -> dict[str, str]:
    return {"X-Tenant-Id": tenant, "X-User-Id": user, "X-Request-Id": request_id}


def _client(tmp_path: Path) -> Iterator[TestClient]:
    settings = OfficeSettings(work_root=tmp_path, openai_api_key="test")
    bundle = _Bundle({name: _Skill() for name in ("writing", "docx", "xlsx", "pptx", "pdf", "bi-analysis")})
    service = OfficeService(settings, bundle, repository=MemoryRepository(), object_store=MemoryObjectStore(), runtime_host=_Runtime())
    with TestClient(create_app(settings, service=service)) as client:
        yield client


def test_identity_session_and_multiturn_json(tmp_path: Path) -> None:
    client = next(_client(tmp_path))
    created = client.post("/api/v1/sessions", headers=_headers(), json={"metadata": {}})
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    first = client.post(f"/api/v1/sessions/{session_id}/messages", headers=_headers("turn-1"), json={"content": "hello"})
    second = client.post(f"/api/v1/sessions/{session_id}/messages", headers=_headers("turn-2"), json={"content": "again"})
    assert first.json()["message"] == "done"
    assert second.json()["message"] == "follow-up: done"
    replay = client.post(f"/api/v1/sessions/{session_id}/messages", headers=_headers("turn-1"), json={"content": "hello"})
    assert replay.status_code == 200
    assert replay.json()["turn_id"] == first.json()["turn_id"]

    assert client.delete(f"/api/v1/sessions/{session_id}", headers=_headers("close-1")).status_code == 200
    closed = client.post(f"/api/v1/sessions/{session_id}/messages", headers=_headers("turn-3"), json={"content": "no"})
    assert closed.status_code == 409


def test_file_isolation_and_download(tmp_path: Path) -> None:
    client = next(_client(tmp_path))
    uploaded = client.post("/api/v1/files", headers={**_headers(), "X-File-Name": "data.csv", "Content-Type": "text/csv"}, content=b"a,b\n1,2\n")
    assert uploaded.status_code == 201
    file_id = uploaded.json()["file_id"]
    assert client.get(f"/api/v1/files/{file_id}", headers=_headers()).content == b"a,b\n1,2\n"
    assert client.get(f"/api/v1/files/{file_id}", headers=_headers(tenant="tenant-b")).status_code == 404


def test_sse_and_header_validation(tmp_path: Path) -> None:
    client = next(_client(tmp_path))
    assert client.post("/api/v1/sessions", json={"metadata": {}}).status_code == 400
    session_id = client.post("/api/v1/sessions", headers=_headers(), json={"metadata": {}}).json()["session_id"]
    response = client.post(f"/api/v1/sessions/{session_id}/messages", headers={**_headers("stream-1"), "Accept": "text/event-stream"}, json={"content": "hello"})
    assert response.status_code == 200
    assert "event: turn.started" in response.text
    assert "event: message.delta" in response.text
    assert "event: turn.completed" in response.text


@pytest.mark.asyncio
async def test_closing_event_stream_marks_turn_interrupted(tmp_path: Path) -> None:
    settings = OfficeSettings(work_root=tmp_path, openai_api_key="test")
    bundle = _Bundle({name: _Skill() for name in ("writing", "docx", "xlsx", "pptx", "pdf", "bi-analysis")})
    repository = MemoryRepository()
    service = OfficeService(
        settings,
        bundle,
        repository=repository,
        object_store=MemoryObjectStore(),
        runtime_host=_SlowRuntime(),
    )
    identity = RequestIdentity("tenant-a", "user-a", "disconnect-1")
    session = await service.create_session(identity, {})
    events = service.message_events(
        identity,
        session["session_id"],
        MessageCreate(content="wait"),
    )
    started = await anext(events)
    await anext(events)
    await events.aclose()
    turn = await repository.get_turn(
        identity.tenant_id,
        identity.user_id,
        session["session_id"],
        started.turn_id,
    )
    assert turn is not None and turn["status"] == "interrupted"
