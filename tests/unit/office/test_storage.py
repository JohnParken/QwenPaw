import asyncio

import pytest

from qwenpaw.office.storage import IdempotencyConflict, MemoryObjectStore, MemoryRepository


@pytest.mark.asyncio
async def test_repository_isolates_tenant_and_user() -> None:
    repo = MemoryRepository()
    created = await repo.create_session("tenant-a", "user-a", "session-1")
    assert created["session_id"] == "session-1"
    assert await repo.get_session("tenant-a", "user-a", "session-1")
    assert await repo.get_session("tenant-b", "user-a", "session-1") is None
    assert await repo.get_session("tenant-a", "user-b", "session-1") is None


@pytest.mark.asyncio
async def test_turn_idempotency_and_conflict() -> None:
    repo = MemoryRepository()
    one = await repo.create_turn("t", "u", "s", "turn", idempotency_key="request", request_hash="a", data={"status": "running"})
    two = await repo.create_turn("t", "u", "s", "other", idempotency_key="request", request_hash="a", data={"status": "running"})
    assert two["turn_id"] == one["turn_id"]
    with pytest.raises(IdempotencyConflict):
        await repo.create_turn("t", "u", "s", idempotency_key="request", request_hash="b")


@pytest.mark.asyncio
async def test_artifact_versions_are_immutable() -> None:
    repo = MemoryRepository()
    first = await repo.create_artifact("t", "u", "a", data={"name": "v1"})
    second = await repo.create_artifact("t", "u", "b", supersedes_artifact_id="a", data={"name": "v2"})
    assert first["artifact_version"] == 1
    assert second["artifact_version"] == 2
    assert second["supersedes_artifact_id"] == "a"

    results = await asyncio.gather(
        repo.create_artifact("t", "u", "c", supersedes_artifact_id="b"),
        repo.create_artifact("t", "u", "d", supersedes_artifact_id="b"),
        return_exceptions=True,
    )
    assert sum(isinstance(item, dict) for item in results) == 1


@pytest.mark.asyncio
async def test_session_lock_serializes() -> None:
    repo = MemoryRepository()
    order: list[str] = []

    async def enter(name: str) -> None:
        async with repo.session_lock("t", "u", "s"):
            order.append(name + "-start")
            await asyncio.sleep(0)
            order.append(name + "-end")

    await asyncio.gather(enter("a"), enter("b"))
    assert order in (["a-start", "a-end", "b-start", "b-end"], ["b-start", "b-end", "a-start", "a-end"])


@pytest.mark.asyncio
async def test_object_store_is_scope_and_version_isolated() -> None:
    store = MemoryObjectStore()
    first = await store.put("t", "u", "file", b"one")
    await store.put("t", "u", "file", b"two")
    assert await store.get("t", "u", "file") == b"two"
    assert await store.get("t", "u", "file", version_id=first["version_id"]) == b"one"
    assert await store.get("other", "u", "file") is None
