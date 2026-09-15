from collections.abc import Iterator
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

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
    manifest = SimpleNamespace(
        allowed_tools=("list_files", "read_text", "write_text", "publish_artifact"),
    )

    def as_dict(self):
        return {"ready": True, "available": True, "version": "1.0.0"}


class _Bundle(dict):
    ready = True


class _Runtime:
    def __init__(self) -> None:
        self.histories: list[list[dict[str, str]]] = []

    async def stream(self, **kwargs):
        history = kwargs["history"]
        self.histories.append(list(history))
        prefix = "follow-up: " if history else ""
        yield RuntimeSignal("message.delta", {"delta": prefix + "done"})
        yield RuntimeSignal("runtime.response", {"usage": {"input_tokens": 1}})


class _SlowRuntime:
    async def stream(self, **_kwargs):
        yield RuntimeSignal("message.delta", {"delta": "working"})
        await asyncio.Event().wait()


class _ArtifactRevisionRuntime:
    def __init__(self) -> None:
        self.service: OfficeService | None = None

    async def stream(self, **kwargs):
        assert self.service is not None
        ctx = SimpleNamespace(request=SimpleNamespace(request_context={**kwargs, "work_dir": str(kwargs["work_dir"])}))
        tools = {tool.__name__: tool for tool in self.service._build_tools(ctx)}
        index_path = kwargs["work_dir"] / "input" / "artifacts" / "index.json"
        mounted = json.loads(index_path.read_text(encoding="utf-8"))
        if mounted:
            tools["write_text"]("output/report.md", "# Version 2")
            tools["publish_artifact"](
                "output/report.md",
                "Report",
                "text/markdown",
                mounted[0]["artifact_id"],
            )
        else:
            tools["write_text"]("output/report.md", "# Version 1")
            tools["publish_artifact"]("output/report.md", "Report", "text/markdown")
        yield RuntimeSignal("message.delta", {"delta": "done"})
        yield RuntimeSignal("runtime.response", {"usage": {}})


class _CoordinatedSlowRuntime:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def stream(self, **_kwargs):
        self.started.set()
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


def test_artifacts_are_auto_mounted_and_revisions_are_immutable(tmp_path: Path) -> None:
    settings = OfficeSettings(work_root=tmp_path, openai_api_key="test")
    bundle = _Bundle({name: _Skill() for name in ("writing", "docx", "xlsx", "pptx", "pdf", "bi-analysis")})
    runtime = _ArtifactRevisionRuntime()
    repository = MemoryRepository()
    service = OfficeService(settings, bundle, repository=repository, object_store=MemoryObjectStore(), runtime_host=runtime)
    runtime.service = service
    with TestClient(create_app(settings, service=service)) as client:
        session_id = client.post("/api/v1/sessions", headers=_headers(), json={"metadata": {}}).json()["session_id"]
        first = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers=_headers("revision-1"),
            json={"content": "create"},
        ).json()["artifacts"][0]
        second = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers=_headers("revision-2"),
            json={"content": "revise"},
        ).json()["artifacts"][0]
        assert second["artifact_version"] == 2
        assert second["supersedes_artifact_id"] == first["artifact_id"]
        listing = client.get(f"/api/v1/sessions/{session_id}/artifacts", headers=_headers("list")).json()
        old = next(item for item in listing if item["artifact_id"] == first["artifact_id"])
        assert old["superseded_by"] == second["artifact_id"]


@pytest.mark.asyncio
async def test_cancel_is_observed_across_service_instances(tmp_path: Path) -> None:
    settings = OfficeSettings(work_root=tmp_path, openai_api_key="test", cancel_poll_seconds=0.01)
    bundle = _Bundle({name: _Skill() for name in ("writing", "docx", "xlsx", "pptx", "pdf", "bi-analysis")})
    repository = MemoryRepository()
    object_store = MemoryObjectStore()
    runtime = _CoordinatedSlowRuntime()
    first = OfficeService(settings, bundle, repository=repository, object_store=object_store, runtime_host=runtime)
    second = OfficeService(settings, bundle, repository=repository, object_store=object_store, runtime_host=_Runtime())
    identity = RequestIdentity("tenant-a", "user-a", "cross-instance")
    session = await first.create_session(identity, {})

    async def consume() -> None:
        async for _ in first.message_events(identity, session["session_id"], MessageCreate(content="wait")):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(runtime.started.wait(), timeout=1)
    assert await second.cancel(identity, session["session_id"], identity.request_id)
    with pytest.raises(asyncio.CancelledError):
        await task
    turns = await repository.list_turns(identity.tenant_id, identity.user_id, session["session_id"])
    assert turns[0]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_history_uses_persisted_rolling_summary_and_token_budget(tmp_path: Path) -> None:
    settings = OfficeSettings(
        work_root=tmp_path,
        openai_api_key="test",
        max_history_messages=2,
        max_history_tokens=16,
        summary_max_chars=200,
    )
    bundle = _Bundle({name: _Skill() for name in ("writing", "docx", "xlsx", "pptx", "pdf", "bi-analysis")})
    repository = MemoryRepository()
    runtime = _Runtime()
    service = OfficeService(settings, bundle, repository=repository, object_store=MemoryObjectStore(), runtime_host=runtime)
    identity = RequestIdentity("tenant-a", "user-a", "summary-request")
    session = await service.create_session(identity, {})
    for index in range(4):
        turn_identity = RequestIdentity(identity.tenant_id, identity.user_id, f"summary-{index}")
        async for _ in service.message_events(turn_identity, session["session_id"], MessageCreate(content="long office request " + str(index))):
            pass
    stored = await repository.get_session(identity.tenant_id, identity.user_id, session["session_id"])
    assert stored and stored["conversation_summary"]
    assert stored["summary_through_message_id"]
    assert runtime.histories[-1][0]["role"] == "system"
    assert "Conversation summary" in runtime.histories[-1][0]["content"]
