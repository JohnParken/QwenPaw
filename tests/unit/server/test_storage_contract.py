"""Identical behavioral contracts for memory and opt-in real TDSQL.

TDSQL tests create only uniquely prefixed tables in a dedicated test database.
They do not emulate or substitute a real server when that backend is selected.
"""

import asyncio
import os
import uuid

import pytest
import pytest_asyncio

from qwenpaw.server.contracts import Conflict, ExecutionContext, LeaseLost, NotFound
from qwenpaw.server.storage import MemoryRepository, TDSQLRepository, create_repository
from qwenpaw.server.storage.schema import statements

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(params=["memory", "tdsql"])
async def repo(request):
    prefix = "qp_test_" + uuid.uuid4().hex[:16] + "_"
    if request.param == "tdsql":
        dsn = os.environ.get("QWENPAW_TEST_TDSQL_URL")
        if not dsn or os.environ.get("QWENPAW_TEST_TDSQL_ALLOW_DDL") != "1":
            pytest.skip("Dedicated TDSQL test URL and ALLOW_DDL=1 required")
        backend = TDSQLRepository(dsn, prefix=prefix, max_size=8)
    else:
        backend = MemoryRepository(prefix=prefix)
    try:
        await backend.migrate()
        yield backend
    finally:
        try:
            await backend.drop_test_tables()
        finally:
            await backend.close()


def context(run):
    return ExecutionContext(
        run["user_id"],
        run["session_id"],
        run["channel_id"],
        run["id"],
        run["definition_version"],
        run["epoch"],
        run["worker_id"],
    )


async def submit(repo, user="alice", session="same", request="r", channel="web"):
    return await repo.submit(user, channel, session, request, {"message": "hello"}, "v")


async def expire(repo, table, identity, value):
    # Fault injection only: business code never gets database connections.
    async with repo._db.connection() as c:
        await c.execute(
            f"UPDATE {repo.table(table)} SET lease_until=%s WHERE {identity}=%s",
            (await c.now() - 1, value),
        )


async def stop(repo, run):
    async with repo.sandbox_stop_guard(
        run["session_id"], {"run_id": run["id"], "epoch": run["epoch"]}
    ) as allowed:
        assert allowed


async def test_opaque_scopes_and_idempotency(repo):
    runs = [
        await submit(
            repo, user=u, channel=c, request="r" if c == "web" else "r-channel"
        )
        for u, c in [
            ("a", "web"),
            ("A", "web"),
            ("a ", "web"),
            ("a", "Web"),
            ("中文", "web"),
        ]
    ]
    assert len({r["session_id"] for r in runs}) == 5
    retries = await asyncio.gather(*(submit(repo, "a") for _ in range(12)))
    assert {r["id"] for r in retries} == {runs[0]["id"]}
    with pytest.raises(Conflict):
        await repo.submit("a", "web", "same", "r", {"message": "different"}, "v")
    for run in runs[1:]:
        if run["user_id"] != "a":
            with pytest.raises(NotFound):
                await repo.messages("a", run["session_id"])
            with pytest.raises(NotFound):
                await repo.get_run("a", run["id"])
    record = await repo.add_file("a", "external", "x.txt", "object", 3)
    with pytest.raises(NotFound):
        await repo.file("A", record["id"])
    assert (await repo.file("a", record["id"]))["key"] == "object"


async def test_competing_workers_fifo_and_quota(repo):
    first = await submit(repo)
    second = await submit(repo, request="r2")
    winners = await asyncio.gather(*(repo.claim("w" + str(i), 60, 2) for i in range(8)))
    claimed = [r for r in winners if r]
    assert len(claimed) == 1 and claimed[0]["id"] == first["id"]
    ctx = context(claimed[0])
    await repo.append(ctx, [{"text": "one"}, {"text": "two"}])
    await repo.finish(
        ctx, "completed", {"messages": [{"role": "assistant", "content": "done"}]}
    )
    successor = await repo.claim("next", 60, 2)
    assert successor["id"] == second["id"] and successor["epoch"] > ctx.epoch
    with pytest.raises(LeaseLost):
        await repo.append(ctx, [{"text": "late"}])
    assert [e["seq"] for e in await repo.events("alice", first["id"], 0)] == [1, 2, 3]
    assert [e["seq"] for e in await repo.events("alice", first["id"], 2)] == [3]
    assert (await repo.messages("alice", first["session_id"]))[-1]["payload"][
        "content"
    ] == "done"


async def test_quota_full_user_cannot_starve_other_users(repo):
    await submit(repo, "a")
    await repo.claim("w", 60, 1)
    for i in range(40):
        await submit(repo, "a", str(i), str(i))
    other = await submit(repo, "b")
    assert (await repo.claim("w2", 60, 1))["id"] == other["id"]
    assert await repo.claim("w3", 60, 1) is None


async def test_expiry_requires_confirmed_stop_and_fences_old_worker(repo):
    await submit(repo)
    run = await repo.claim("old", 60, 4)
    ctx = context(run)
    await repo.begin_tool(ctx, "call", "shell", {})
    successor = await submit(repo, request="r2")
    await expire(repo, "runs", "id", run["id"])
    for operation in [
        lambda: repo.append(ctx, [{}]),
        lambda: repo.finish(ctx, "completed", {}),
        lambda: repo.begin_tool(ctx, "late", "shell", {}),
    ]:
        with pytest.raises(LeaseLost):
            await operation()
    with pytest.raises(Conflict):
        await repo.interrupt(run["id"], run["epoch"])
    with pytest.raises(RuntimeError):
        async with repo.sandbox_stop_guard(
            ctx.session_id, {"run_id": ctx.run_id, "epoch": ctx.epoch}
        ) as allowed:
            assert allowed
            raise RuntimeError("Kubernetes unavailable")
    assert await repo.claim("next", 60, 4) is None
    await stop(repo, run)
    await repo.interrupt(run["id"], run["epoch"])
    assert (await repo.get_run("alice", run["id"]))["status"] == "interrupted"
    assert (await repo.claim("next", 60, 4))["id"] == successor["id"]
    async with repo.sandbox_stop_guard(
        ctx.session_id, {"run_id": ctx.run_id, "epoch": ctx.epoch}
    ) as allowed:
        assert not allowed
    with pytest.raises(LeaseLost):
        await repo.heartbeat(ctx, 60)


async def test_approval_ownership_dedup_and_cancel(repo):
    await submit(repo)
    run = await repo.claim("w", 60, 4)
    ctx = context(run)
    assert (await repo.begin_tool(ctx, "t", "shell", {"a": 1}))["created"]
    assert not (await repo.begin_tool(ctx, "t", "shell", {"a": 1}))["created"]
    with pytest.raises(Conflict):
        await repo.begin_tool(ctx, "t", "shell", {"a": 2})
    approval = await repo.request_approval(ctx, "t", 60)
    with pytest.raises(NotFound):
        await repo.decide("other", approval["id"], True)
    await repo.cancel("alice", run["id"])
    assert (await repo.decide("alice", approval["id"], True))["status"] == "expired"
    with pytest.raises(Conflict):
        await repo.begin_tool(ctx, "new", "shell", {})
    await stop(repo, run)
    await repo.finish(ctx, "completed", {})
    assert (await repo.get_run("alice", run["id"]))["status"] == "cancelled"
    assert (
        len(
            [
                e
                for e in await repo.events("alice", run["id"], 0)
                if e["payload"]["type"] == "terminal"
            ]
        )
        == 1
    )


async def test_approval_expired_worker_cannot_dispatch(repo):
    await submit(repo)
    run = await repo.claim("w", 60, 4)
    ctx = context(run)
    await repo.begin_tool(ctx, "t", "shell", {})
    approval = await repo.request_approval(ctx, "t", 60)
    await expire(repo, "runs", "id", run["id"])
    assert (await repo.decide("alice", approval["id"], True))["status"] == "expired"
    with pytest.raises(LeaseLost):
        await repo.approval(ctx, approval["id"])


async def test_memory_cross_channel_scope_and_job_fencing(repo):
    for i, channel in enumerate(["web", "mobile"]):
        await submit(repo, session=str(i), request=str(i), channel=channel)
        run = await repo.claim("w", 60, 4)
        await repo.remember(context(run), "likes tea")
        await repo.finish(context(run), "completed", {})
    assert len(await repo.memories("alice", "tea")) == 1
    assert await repo.memories("other", "tea") == []
    job = await repo.claim_memory("owner", 60)
    assert await repo.claim_memory("competitor", 60) is None
    await expire(repo, "memory_jobs", "run_id", job["run_id"])
    reclaimed = await repo.claim_memory("owner", 60)
    assert reclaimed["run_id"] == job["run_id"] and reclaimed["epoch"] > job["epoch"]
    assert not await repo.merge_memory(job, ["stale"])
    assert await repo.merge_memory(reclaimed, ["likes books"])
    await repo.delete_memories("alice")
    assert not await repo.merge_memory(reclaimed, ["resurrected"])
    assert not await repo.complete_memory(reclaimed)
    assert await repo.memories("alice", "") == []


async def test_vectors_public_versions_and_private_users(repo):
    await submit(repo)
    run = await repo.claim("w", 60, 4)
    await repo.remember(context(run), "private")
    entry = (await repo.pending_memory_vectors("alice", "embedding"))[0]
    await repo.put_memory_vector("alice", entry["id"], "embedding", [1, 0])
    assert await repo.search_memory_vectors("other", "embedding", [1, 0]) == []
    assert (await repo.search_memory_vectors("alice", "embedding", [1, 0]))[0][
        "text"
    ] == "private"
    await repo.put_knowledge_vector("v", 0, "public", "embedding", [1, 0])
    assert await repo.search_knowledge_vectors("v", "embedding", [1, 0]) == ["public"]
    assert await repo.search_knowledge_vectors("v2", "embedding", [1, 0]) == []
    with pytest.raises(ValueError):
        await repo.put_knowledge_vector("v", 1, "invalid", "embedding", [float("nan")])
    await repo.delete_memories("alice")
    assert await repo.search_memory_vectors("alice", "embedding", [1, 0]) == []


async def test_transaction_rollback_and_configuration_snapshot(repo):
    await repo.migrate()
    await repo.ready()
    payload = {"model": "x", "tools": []}
    await repo.put_definition("v", payload)
    payload["tools"].append("bad")
    assert (await repo.definition("v"))["tools"] == []
    with pytest.raises(Conflict):
        await repo.put_definition("v", payload)
    await submit(repo)
    run = await repo.claim("w", 60, 4)
    ctx = context(run)
    with pytest.raises(ValueError):
        await repo.append(ctx, [{"text": "must rollback"}, {"bad": float("nan")}])
    assert await repo.events("alice", run["id"], 0) == []
    assert (await repo.statistics())["runs"]["running"] == 1


async def test_mysql_schema_has_no_postgres_or_newer_lock_syntax():
    sql = "\n".join(statements("qp_test_check_", "mysql")).upper()
    for unsupported in [
        "JSONB",
        "RETURNING",
        "SKIP LOCKED",
        "TIMESTAMPTZ",
        "CREATE EXTENSION",
    ]:
        assert unsupported not in sql
    assert "ENGINE=INNODB" in sql
    with pytest.raises(ValueError):
        create_repository("postgresql://unsupported")
    with pytest.raises(ValueError):
        MemoryRepository(prefix="unsafe;")


async def test_stop_fences_new_tool_dispatch_and_unsafe_completion(repo):
    await submit(repo)
    run = await repo.claim("w", 60, 4)
    ctx = context(run)
    await repo.begin_tool(ctx, "t", "shell", {})
    with pytest.raises(Conflict):
        await repo.finish(ctx, "completed", {})
    await stop(repo, run)
    with pytest.raises(LeaseLost):
        await repo.sandbox_acquire(ctx)
    with pytest.raises(LeaseLost):
        await repo.begin_tool(ctx, "late", "shell", {})
    await repo.finish(ctx, "interrupted", {})


async def test_reconnect_preserves_durable_state(repo):
    if repo._db.dialect != "mysql":
        pytest.skip("Persistence across connections requires the real TDSQL backend")
    run = await submit(repo)
    await repo.close()
    await repo.open()
    await repo.ready()
    assert (await repo.get_run("alice", run["id"]))["status"] == "queued"
    assert len(await repo.messages("alice", run["session_id"])) == 1


async def test_backend_failure_never_falls_back_to_memory(monkeypatch):
    with pytest.raises(ValueError):
        TDSQLRepository(None)
    repo = create_repository("mysql://test:test@127.0.0.1:1/test")

    async def unavailable():
        raise ConnectionError("test outage")

    monkeypatch.setattr(repo._db, "open", unavailable)
    with pytest.raises(ConnectionError):
        await repo.ready()
    assert isinstance(repo, TDSQLRepository)
    assert repo._db.raw is None
