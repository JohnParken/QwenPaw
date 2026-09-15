"""Mandatory P0-S01/S02 using independent Supervisors and real File API/PG."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
from uuid import uuid4

import psycopg
import pytest

from support import Harness, scope

pytestmark = pytest.mark.skipif(
    not os.getenv("P0_TEST_DSN"), reason="real P0_TEST_DSN required"
)


def assert_history(export, own, other):
    text = json.dumps(export["conversation"])
    assert own in text and other not in text
    messages = export["conversation"]["state"]["context"]
    calls = [
        b["id"]
        for m in messages
        for b in m["content"]
        if b["type"] == "tool_call"
    ]
    results = [
        b["id"]
        for m in messages
        for b in m["content"]
        if b["type"] == "tool_result"
    ]
    assert calls and calls == results
    assert len(calls) == export["cursor"]
    assert export["memory"] is None
    assert export["budget"] == {"segments_used": export["cursor"]}


@pytest.fixture(params=["fixture", "native"])
def harness(request, tmp_path):
    if request.param == "native" and os.getenv("P0_NATIVE") != "1":
        pytest.skip(
            "P0_NATIVE=1 required; fixture never substitutes for native"
        )
    h = Harness(
        tmp_path / "services",
        os.environ["P0_TEST_DSN"],
        executor=request.param,
    )
    try:
        yield h
    finally:
        h.close()


def test_same_user_sessions_restore_independently(harness):
    h = harness
    runs = {
        sid: h.new_run(scope(sid), sid, sid + "-private-marker")
        for sid in ("s1", "s2")
    }
    seen = {"s1": [], "s2": []}
    previous_refs = {}
    evidence = []
    for number in range(4):
        output = h.worker("worker-" + str(number))
        export = output["export"]
        assert str(h.root) not in json.dumps(export)
        sid = export["session_id"]
        other = "s2" if sid == "s1" else "s1"
        assert_history(
            export, sid + "-private-marker", other + "-private-marker"
        )
        seen[sid].append(export["cursor"])
        assert export["scope"] == scope(sid)
        own_head = h.query(runs[sid])["scope"]["head"]
        assert (
            json.loads(
                h.file_client(scope(sid)).download(
                    own_head["conversations"][sid]
                )
            )
            == export
        )
        for name, ref in own_head["files"].items():
            assert h.file_client(scope(sid)).download(ref) == __import__(
                "base64"
            ).b64decode(export["files"][name])
        if other in previous_refs:
            assert (
                h.query(runs[other])["scope"]["head"]["conversations"][other]
                == previous_refs[other]
            )
        previous_refs[sid] = own_head["conversations"][sid]
        evidence.append(
            {
                "worker": number,
                "session_id": sid,
                "cursor": export["cursor"],
                "attempt_id": output["attempt_id"],
                "conversation": export["conversation"],
                "file_refs": own_head["files"],
            }
        )
    assert seen == {"s1": [1, 2], "s2": [1, 2]}
    assert all(h.query(r)["state"] == "SUCCEEDED" for r in runs.values())
    (h.root / "P0-S01-evidence.json").write_text(
        json.dumps(evidence, indent=2)
    )


def test_workspace_sessions_single_writer_preserves_refs(harness):
    h = harness
    shared = scope(workspace=True)
    # Pre-seed both conversations using real first-segment exports. Explicitly
    # finish those seed Runs at their committed boundary before the race.
    for sid in ("s1", "s2"):
        h.new_run(shared, sid, sid + "-workspace-marker")
        seed = h.worker("seed-" + sid)
        assert seed["export"]["cursor"] == 1
        finalizer = h.register("finalize-" + sid)
        claim = finalizer.claim(uuid4().hex)
        action = {
            k: claim["context"][k] for k in ("attempt_id", "lease_epoch")
        }
        finalizer.post("/v1/ready", action)
        finalizer.post("/v1/boundary", {**action, "action": "finish"})
        finalizer.post("/v1/cleanup", {**action, "confirmed": True})
    runs = {
        sid: h.new_run(shared, sid, sid + "-workspace-marker")
        for sid in ("s1", "s2")
    }
    baseline = h.query(runs["s1"])["scope"]["head"]
    assert set(baseline["conversations"]) == {"s1", "s2"}
    clients = [h.register("race-0"), h.register("race-1")]
    barrier = threading.Barrier(2)

    def compete(i):
        barrier.wait(timeout=3)
        return clients[i].claim(uuid4().hex)

    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(compete, range(2)))
    assert sum("context" in c for c in claims) == 1
    win = next(i for i, c in enumerate(claims) if "context" in c)
    lose = 1 - win
    sid = claims[win]["context"]["session_id"]
    other = "s2" if sid == "s1" else "s1"
    for _ in range(3):
        assert clients[lose].claim(uuid4().hex) == {"assignment": None}
        assert h.query(runs[other])["state"] == "QUEUED"
    with psycopg.connect(h.dsn) as conn:
        snapshot = conn.execute(
            "SELECT data FROM harness.attempts "
            "WHERE data->>'state' IN "
            "('ASSIGNED','STARTING','RUNNING')"
        ).fetchall()
        assert (
            len(snapshot) == 1
            and snapshot[0][0]["run_id"] == runs[sid]["run_id"]
        )
    first = h.worker("race-" + str(win), claims[win])
    assert_history(
        first["export"], sid + "-workspace-marker", other + "-workspace-marker"
    )
    head = h.query(runs[sid])["scope"]["head"]
    assert head["conversations"][other] == baseline["conversations"][other]
    assert h.file_client(shared).download(
        head["conversations"][other]
    ) == h.file_client(shared).download(baseline["conversations"][other])
    second_claim = clients[lose].claim(uuid4().hex)
    assert second_claim["context"]["base_revision"] == head["revision"]
    assert second_claim["context"]["session_id"] == other
    second = h.worker("race-" + str(lose), second_claim)
    assert_history(
        second["export"],
        other + "-workspace-marker",
        sid + "-workspace-marker",
    )
    final = h.query(runs[other])["scope"]["head"]
    assert final["conversations"][sid] == head["conversations"][sid]
    assert all(h.query(r)["state"] == "SUCCEEDED" for r in runs.values())
    (h.root / "P0-S02-evidence.json").write_text(
        json.dumps(
            {
                "pg_competition_snapshot": snapshot,
                "baseline": baseline,
                "first": first,
                "second": second,
                "final": final,
            },
            indent=2,
        )
    )


def test_p0_f04_kill_native_worker_after_committed_boundary(harness):
    from datetime import datetime
    import time
    import httpx

    h = harness
    run = h.new_run(scope(), "s1", "crash-private-marker")
    a = h.register("worker-a")
    old = a.claim(uuid4().hex)
    barrier = h.worker("worker-a", old, kill_after_commit=True)
    assert barrier["result"]["state"] == "COMMITTED"
    assert h.query(run)["scope"]["revision"] == 1
    # Kill happened only after durable publication. The old private directory
    # remains as evidence; Worker B can obtain state only via File API.
    with psycopg.connect(h.dsn) as conn:
        lease = conn.execute(
            "SELECT data FROM harness.attempts WHERE id=%s",
            (old["context"]["attempt_id"],),
        ).fetchone()[0]["lease_until"]
    time.sleep(max(0, lease - time.time() + 0.05))
    h.stop("core")
    h.start("core")
    assert (
        a.get("/v1/checkpoints/" + barrier["body"]["commit_id"])["state"]
        == "COMMITTED"
    )
    h.client("coordinator").post("/v1/recovery/scan")
    resumed = h.worker("worker-b")
    assert resumed["export"]["cursor"] == 2
    assert_history(resumed["export"], "crash-private-marker", "foreign-marker")
    assert h.query(run)["state"] == "SUCCEEDED"
    action = {
        key: old["context"][key] for key in ("attempt_id", "lease_epoch")
    }
    for route in ("ready", "heartbeat"):
        with pytest.raises(httpx.HTTPStatusError) as e:
            a.post("/v1/" + route, action)
        assert e.value.response.status_code == 409
    stale = {
        **barrier["body"],
        "commit_id": uuid4().hex,
        "expected_revision": 2,
        "cursor": 2,
        "next_segment": 3,
        "budget": {"segments_used": 2},
    }
    with pytest.raises(httpx.HTTPStatusError):
        a.post("/v1/checkpoints", stale)
    assert h.query(run)["state"] == "SUCCEEDED"
    (h.root / "P0-F04-evidence.json").write_text(
        json.dumps(
            {"barrier": barrier, "resumed": resumed, "final": h.query(run)},
            indent=2,
        )
    )
