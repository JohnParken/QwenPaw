"""Focused PostgreSQL repository checks.

These tests intentionally use a real database.  Set
``QWENPAW_TEST_DATABASE_URL`` to run them; they are skipped in the normal
unit-only environment where no PostgreSQL service is configured.
"""

from __future__ import annotations

import os
import uuid

import pytest

from qwenpaw.server.contracts import Conflict, ExecutionContext, LeaseLost, NotFound
from qwenpaw.server.repository import PostgresRepository

DATABASE_URL = os.environ.get("QWENPAW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="QWENPAW_TEST_DATABASE_URL is not configured",
)


@pytest.fixture
async def repo():
    if not DATABASE_URL:  # pragma: no cover - guarded by pytestmark
        pytest.skip("QWENPAW_TEST_DATABASE_URL is not configured")
    from psycopg import AsyncConnection
    from psycopg.conninfo import make_conninfo

    schema = "repository_test_" + uuid.uuid4().hex
    connection = await AsyncConnection.connect(DATABASE_URL, autocommit=True)
    await connection.execute(f'CREATE SCHEMA "{schema}"')
    repository = PostgresRepository(
        make_conninfo(DATABASE_URL, options=f"-csearch_path={schema},public"),
        min_size=1,
        max_size=4,
    )
    await repository.open()
    await repository.migrate()
    repository._test_user = "repository-test-" + uuid.uuid4().hex
    try:
        yield repository
    finally:
        await repository.close()
        await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await connection.close()


async def _definition(repo, version="test-v1"):
    await repo.put_definition(version, {"version": version, "model": "test"})
    return version


async def _claim(repo, user, request="r1", session="channel-session"):
    version = await _definition(repo)
    submitted = await repo.submit(
        user, "channel", session, request, {"message": request}, version
    )
    claimed = await repo.claim("worker-" + uuid.uuid4().hex, 60, 2)
    assert claimed and claimed["id"] == submitted["id"]
    return claimed


@pytest.mark.asyncio
async def test_submit_idempotency_ownership_and_definition(repo):
    user = repo._test_user
    version = await _definition(repo)
    external_id = str(uuid.uuid4())
    first = await repo.submit(user, "channel", external_id, "r1", {"x": 1}, version)
    again = await repo.submit(user, "channel", external_id, "r1", {"x": 1}, version)
    assert again["id"] == first["id"]
    with pytest.raises(Conflict):
        await repo.submit(user, "channel", first["session_id"], "r1", {"x": 2}, version)
    with pytest.raises(NotFound):
        await repo.get_run("another-user", first["id"])
    with pytest.raises(Conflict):
        await repo.put_definition(version, {"version": version, "model": "changed"})


@pytest.mark.asyncio
async def test_fifo_fencing_events_and_atomic_messages(repo):
    user = repo._test_user
    first = await _claim(repo, user)
    second = await repo.submit(
        user, "channel", "channel-session", "r2", {"message": "r2"}, "test-v1"
    )
    assert await repo.claim("other-worker", 60, 2) is None
    ctx = ExecutionContext(
        user,
        first["session_id"],
        "channel",
        first["id"],
        "test-v1",
        first["epoch"],
        first["worker_id"],
    )
    assert await repo.heartbeat(ctx, 60) is False
    await repo.append(ctx, [{"type": "delta", "text": "hi"}])
    await repo.finish(
        ctx,
        "completed",
        {"state": {"context": [{"id": "a1", "role": "assistant", "content": "hi"}]}},
        {"role": "assistant", "content": "hi", "id": "a1"},
    )
    events = await repo.events(user, first["id"], 0)
    assert [item["seq"] for item in events] == [1, 2]
    assert events[-1]["payload"]["type"] == "terminal"
    messages = await repo.messages(user, first["session_id"])
    assert [item["payload"]["role"] for item in messages] == [
        "user",
        "user",
        "assistant",
    ]
    claimed_second = await repo.claim("other-worker", 60, 2)
    assert claimed_second and claimed_second["id"] == second["id"]
    assert claimed_second["epoch"] > first["epoch"]
    with pytest.raises(LeaseLost):
        await repo.append(ctx, [{"stale": True}])


@pytest.mark.asyncio
async def test_tool_dedup_and_approval_lifecycle(repo):
    user = repo._test_user
    run = await _claim(repo, user)
    ctx = ExecutionContext(
        user,
        run["session_id"],
        "channel",
        run["id"],
        "test-v1",
        run["epoch"],
        run["worker_id"],
    )
    created = await repo.begin_tool(ctx, "call-1", "echo", {"text": "x"})
    assert created == {
        "created": True,
        "call_id": "call-1",
        "status": "running",
        "result": None,
    }
    approval = await repo.request_approval(ctx, "call-1", 60)
    assert approval["status"] == "pending" and approval["id"]
    assert (await repo.get_run(user, run["id"]))["status"] == "waiting_approval"
    await repo.decide(user, approval["id"], True)
    assert (await repo.approval(ctx, approval["id"]))["status"] == "approved"
    await repo.end_tool(ctx, "call-1", "completed", {"ok": True})
    duplicate = await repo.begin_tool(ctx, "call-1", "echo", {"text": "x"})
    assert duplicate["created"] is False and duplicate["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_expired_run_keeps_session_blocked_until_interrupt(repo):
    user = repo._test_user
    first = await _claim(repo, user, "r-expire")
    second = await repo.submit(
        user, "channel", "channel-session", "r-after", {"message": "after"}, "test-v1"
    )
    async with repo.pool.connection() as conn:
        await conn.execute(
            "UPDATE qp_server_runs SET lease_until = now() - interval '1 second' WHERE id = %s",
            (first["id"],),
        )
    expired = await repo.expired()
    assert any(item["id"] == first["id"] for item in expired)
    assert await repo.claim("new-worker", 60, 2) is None
    await repo.interrupt(first["id"], first["epoch"])
    claimed = await repo.claim("new-worker", 60, 2)
    assert claimed and claimed["id"] == second["id"]
