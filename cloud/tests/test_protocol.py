"""P0-F01..F04: real PostgreSQL, HTTP, restart and exact fault barriers."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
import threading
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import psycopg
import pytest

from qwenpaw_cloud.contracts import Limits
from qwenpaw_cloud.core import create_app
from qwenpaw_cloud.repository import PostgresRepository
from support import Harness, scope

pytestmark = pytest.mark.skipif(
    not os.getenv("P0_TEST_DSN"), reason="real P0_TEST_DSN required"
)


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path / "services", os.environ["P0_TEST_DSN"])
    try:
        yield h
    finally:
        h.close()


def action(claim):
    c = claim["context"]
    return {"attempt_id": c["attempt_id"], "lease_epoch": c["lease_epoch"]}


def checkpoint(h, claim, marker="s1-marker"):
    c = claim["context"]
    ref = h.file_client(c["scope"]).upload(
        c["scope"],
        json.dumps({"session_id": c["session_id"], "marker": marker}).encode(),
    )
    return {
        **action(claim),
        "commit_id": uuid4().hex,
        "expected_revision": c["base_revision"],
        "conversation_ref": ref,
        "files": {c["session_id"] + ".txt": ref},
        "cursor": 1,
        "next_segment": 2,
        "budget": {"segments_used": 1},
        "memory": None,
    }


def headers(h, wid):
    return {
        "Authorization": "Bearer " + h.auth.issue("worker", wid, "core"),
        "X-Protocol-Version": "p0.v1",
    }


def test_p0_f01_claim_race_and_lost_response(harness):
    h = harness
    h.new_run(scope(), "s1", "s1-only")
    clients = [h.register("w1"), h.register("w2")]
    barrier = threading.Barrier(2)
    ids = [uuid4().hex, uuid4().hex]

    def compete(i):
        barrier.wait(timeout=3)
        return clients[i].claim(ids[i])

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(compete, range(2)))
    winners = [i for i, r in enumerate(results) if "context" in r]
    assert len(winners) == 1
    i = winners[0]
    original = results[i]["context"]
    assert clients[i].get("/v1/claims/" + ids[i])["context"] == original
    assert clients[i].claim(ids[i])["context"] == original
    with psycopg.connect(h.dsn) as db:
        assert (
            db.execute(
                "SELECT count(*) "
                "FROM harness.attempts "
                "WHERE data->>'state'='ASSIGNED'"
            ).fetchone()[0]
            == 1
        )
    # A second scope: inject failure strictly after the PG claim transaction.
    h.new_run(scope("s2"), "s2", "s2-only")
    app = create_app(PostgresRepository(h.dsn), h.file_url, h.key)
    app.state.fault_hook = (
        lambda name: (_ for _ in ()).throw(RuntimeError("drop response"))
        if name == "claim_committed"
        else None
    )
    loser = 1 - i
    req = uuid4().hex
    with TestClient(app, raise_server_exceptions=False) as api:
        response = api.post(
            "/v1/claim",
            json={"request_id": req},
            headers=headers(h, f"w{loser + 1}"),
        )
        assert response.status_code == 500
    recovered = clients[loser].get("/v1/claims/" + req)
    assert "context" in recovered
    assert clients[loser].claim(req)["context"] == recovered["context"]


def test_p0_f02_restart_expiry_and_old_requests(tmp_path):
    limits = Limits(
        heartbeat_seconds=0.1,
        lease_seconds=2,
        safety_seconds=0.5,
        request_timeout=0.2,
        stop_grace=0.1,
    )
    h = Harness(
        tmp_path / "services", os.environ["P0_TEST_DSN"], limits=limits
    )
    try:
        run = h.new_run(scope(), "s1", "restart-marker")
        w = h.register("a")
        rid = uuid4().hex
        claim = w.claim(rid)
        h.stop("core")
        h.start("core")
        assert w.get("/v1/claims/" + rid)["context"] == claim["context"]
        # Deadline is fixed by the persisted lease.
        # Random fault sleep must not affect this value.
        from datetime import datetime

        until = datetime.fromisoformat(
            claim["lease_until"].replace("Z", "+00:00")
        ).timestamp()
        time.sleep(max(0, until - time.time() + 0.05))
        for route in ("ready", "heartbeat"):
            with pytest.raises(httpx.HTTPStatusError) as e:
                w.post("/v1/" + route, action(claim))
            assert e.value.response.status_code == 409
        h.client("coordinator").post("/v1/recovery/scan")
        assert w.claim(rid)["state"] == "LOST"
        b = h.register("b")
        fresh = b.claim(uuid4().hex)
        assert fresh["context"]["lease_epoch"] == 2
        assert h.query(run)["state"] == "STARTING"
    finally:
        h.close()


def test_p0_f03_pin_and_publish_response_windows(harness):
    h = harness
    run = h.new_run(scope(), "s1", "pin-marker")
    w = h.register("a")
    claim = w.claim(uuid4().hex)
    w.post("/v1/ready", action(claim))
    body = checkpoint(h, claim)
    app = create_app(PostgresRepository(h.dsn), h.file_url, h.key)

    def fault(name):
        if name == "pin_committed":
            raise RuntimeError("pin barrier failure")

    app.state.fault_hook = fault
    with TestClient(app, raise_server_exceptions=False) as api:
        response = api.post(
            "/v1/checkpoints", json=body, headers=headers(h, "a")
        )
        assert response.status_code == 500
    assert h.query(run)["scope"]["revision"] == 0
    assert w.get("/v1/checkpoints/" + body["commit_id"])["state"] == "PINNED"
    # Retained reference set is queryable even though no head was published.
    with psycopg.connect(h.dsn) as db:
        assert (
            db.execute("SELECT count(*) FROM files.reference_sets").fetchone()[
                0
            ]
            == 2
        )
    app.state.fault_hook = (
        lambda name: (_ for _ in ()).throw(
            RuntimeError("publish response lost")
        )
        if name == "publish_committed"
        else None
    )
    with TestClient(app, raise_server_exceptions=False) as api:
        assert (
            api.post(
                "/v1/checkpoints", json=body, headers=headers(h, "a")
            ).status_code
            == 500
        )
    first = w.get("/v1/checkpoints/" + body["commit_id"])
    assert first["state"] == "COMMITTED" and first["revision"] == 1
    assert w.post("/v1/checkpoints", body) == first
    h.stop("files")
    h.start("files")
    h.stop("core")
    h.start("core")
    assert w.get("/v1/checkpoints/" + body["commit_id"]) == first
    assert h.query(run)["scope"]["revision"] == 1


def test_p0_f04_handoff_fences_previous_worker(harness):
    h = harness
    run = h.new_run(scope(), "s1", "fence-marker")
    a = h.register("a")
    old = a.claim(uuid4().hex)
    a.post("/v1/ready", action(old))
    body = checkpoint(h, old)
    committed = a.post("/v1/checkpoints", body)
    a.post("/v1/boundary", {**action(old), "action": "handoff"})
    a.post("/v1/cleanup", {**action(old), "confirmed": True})
    b = h.register("b")
    new = b.claim(uuid4().hex)
    assert new["checkpoint"]["conversations"]["s1"] == body["conversation_ref"]
    assert new["context"]["lease_epoch"] == old["context"]["lease_epoch"] + 1
    for route in ("ready", "heartbeat"):
        with pytest.raises(httpx.HTTPStatusError) as e:
            a.post("/v1/" + route, action(old))
        assert e.value.response.status_code == 409
    stale = {
        **body,
        "commit_id": uuid4().hex,
        "expected_revision": 1,
        "cursor": 2,
        "next_segment": 3,
        "budget": {"segments_used": 2},
    }
    with pytest.raises(httpx.HTTPStatusError):
        a.post("/v1/checkpoints", stale)
    assert a.get("/v1/checkpoints/" + body["commit_id"]) == committed
    b.post("/v1/ready", action(new))
    second = {**stale, **action(new), "commit_id": uuid4().hex}
    b.post("/v1/checkpoints", second)
    b.post("/v1/boundary", {**action(new), "action": "finish"})
    b.post("/v1/cleanup", {**action(new), "confirmed": True})
    assert h.query(run)["state"] == "SUCCEEDED"
    with pytest.raises(httpx.HTTPStatusError):
        a.post("/v1/ready", action(old))
    assert h.query(run)["state"] == "SUCCEEDED"


def test_workspace_sessions_single_writer_preserves_refs(harness):
    h = harness
    runs = [
        h.new_run(scope(workspace=True), sid, sid + "-marker")
        for sid in ("s1", "s2")
    ]
    clients = [h.register("w1"), h.register("w2")]
    barrier = threading.Barrier(2)

    def compete(i):
        barrier.wait(timeout=3)
        return clients[i].claim(uuid4().hex)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(compete, range(2)))
    win = next(i for i, value in enumerate(results) if "context" in value)
    lose = 1 - win
    assert sum("context" in r for r in results) == 1
    first = results[win]
    winner_run = first["context"]["run_id"]
    loser_run = next(r for r in runs if r["run_id"] != winner_run)
    for _ in range(3):
        assert clients[lose].claim(uuid4().hex) == {"assignment": None}
    assert h.query(loser_run)["state"] == "QUEUED"
    with psycopg.connect(h.dsn) as db:
        snapshot = db.execute(
            "SELECT data FROM harness.attempts "
            "WHERE data->>'state' IN "
            "('ASSIGNED','STARTING','RUNNING')"
        ).fetchall()
        assert len(snapshot) == 1 and snapshot[0][0]["run_id"] == winner_run

    def finish(client, claim, label):
        client.post("/v1/ready", action(claim))
        body = checkpoint(h, claim, label)
        client.post("/v1/checkpoints", body)
        client.post("/v1/boundary", {**action(claim), "action": "finish"})
        # No scope reuse before physical cleanup, even with logical terminal.
        assert clients[lose].claim(uuid4().hex) == {"assignment": None}
        client.post("/v1/cleanup", {**action(claim), "confirmed": True})
        return body

    first_body = finish(clients[win], first, "first-session")
    second = clients[lose].claim(uuid4().hex)
    assert second["context"]["base_revision"] == 1
    first_sid = first["context"]["session_id"]
    assert (
        second["checkpoint"]["conversations"][first_sid]
        == first_body["conversation_ref"]
    )
    clients[lose].post("/v1/ready", action(second))
    second_body = checkpoint(h, second, "second-session")
    clients[lose].post("/v1/checkpoints", second_body)
    head = h.query(loser_run)["scope"]["head"]
    assert head["conversations"][first_sid] == first_body["conversation_ref"]
    assert (
        head["conversations"][second["context"]["session_id"]]
        == second_body["conversation_ref"]
    )
    assert (
        json.loads(
            h.file_client(scope(workspace=True)).download(
                first_body["conversation_ref"]
            )
        )["marker"]
        == "first-session"
    )


def test_auth_scope_protocol_and_slot_quarantine(harness):
    h = harness
    assert (
        httpx.post(
            h.core_url + "/v1/claim", json={"request_id": "a"}, trust_env=False
        ).status_code
        == 426
    )
    token = h.auth.issue("worker", "w", "core")
    assert (
        httpx.post(
            h.core_url + "/v1/claim",
            json={"request_id": "a"},
            headers={
                "X-Protocol-Version": "p0.v1",
                "Authorization": "Bearer " + token[:-1] + "x",
            },
            trust_env=False,
        ).status_code
        == 401
    )
    run = h.new_run(scope(), "s1", "identity-marker")
    with pytest.raises(httpx.HTTPStatusError) as e:
        h.client("bff", tenant_id="t2", user_id="u1").get(
            "/v1/runs/" + run["run_id"]
        )
    assert e.value.response.status_code == 403
    a = h.register("a")
    claim = a.claim(uuid4().hex)
    with pytest.raises(httpx.HTTPStatusError) as e:
        h.register("b").post("/v1/ready", action(claim))
    assert e.value.response.status_code == 403
    a.post("/v1/boundary", {**action(claim), "action": "failed"})
    a.post("/v1/cleanup", {**action(claim), "confirmed": False})
    with pytest.raises(httpx.HTTPStatusError) as e:
        a.claim(uuid4().hex)
    assert e.value.response.status_code == 409
