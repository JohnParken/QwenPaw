"""Real PG File API: immutable content, scope, concurrency and retention."""
import base64
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from qwenpaw_cloud.auth import Auth
from qwenpaw_cloud.files import create_app

pytestmark = pytest.mark.skipif(
    not os.getenv("P0_TEST_DSN"), reason="real P0_TEST_DSN required"
)
KEY = "synthetic-file-test-key-" + "k" * 32
SCOPE = {
    "tenant_id": "t1",
    "owner_user_id": "u1",
    "scope_type": "standalone",
    "scope_id": "s1",
}


def headers(role="file-worker", scope=None):
    return {
        "X-Protocol-Version": "p0.v1",
        "Authorization": "Bearer "
        + Auth(KEY).issue(role, "test", "files", scope=scope or SCOPE),
    }


def body(uid, content=b"hello"):
    return {
        "upload_id": uid,
        "scope": SCOPE,
        "content_base64": base64.b64encode(content).decode(),
    }


def test_file_scope_idempotency_integrity_and_restart(tmp_path):
    dsn = os.environ["P0_TEST_DSN"]
    uid, sid = uuid4().hex, uuid4().hex
    with TestClient(create_app(dsn, tmp_path, KEY)) as api:
        assert api.post("/v1/uploads", json=body(uid)).status_code == 400
        assert (
            api.post(
                "/v1/uploads",
                json=body(uid),
                headers={"X-Protocol-Version": "p0.v1"},
            ).status_code
            == 401
        )
        first = api.post("/v1/uploads", json=body(uid), headers=headers())
        assert first.status_code == 200
        assert (
            api.post("/v1/uploads", json=body(uid), headers=headers()).json()
            == first.json()
        )
        assert (
            api.post(
                "/v1/uploads", json=body(uid, b"changed"), headers=headers()
            ).status_code
            == 409
        )
        ref = first.json()["ref"]
        assert (
            api.get("/v1/files/" + uid, headers=headers()).content == b"hello"
        )
        foreign = {**SCOPE, "tenant_id": "t2"}
        assert (
            api.get(
                "/v1/files/" + uid, headers=headers(scope=foreign)
            ).status_code
            == 403
        )
        assert (
            api.get(
                "/v1/files/" + uid + "?version=2", headers=headers()
            ).status_code
            == 404
        )
        pin = {"scope": SCOPE, "refs": [ref]}
        assert (
            api.post(
                "/v1/reference-sets/" + sid, json=pin, headers=headers()
            ).status_code
            == 403
        )
        sealed = api.post(
            "/v1/reference-sets/" + sid, json=pin, headers=headers("core")
        ).json()
        assert sealed["status"] == "PINNED"
        assert (
            api.post(
                "/v1/reference-sets/" + sid, json=pin, headers=headers("core")
            ).json()
            == sealed
        )
    with TestClient(create_app(dsn, tmp_path, KEY)) as restarted:
        assert (
            restarted.get(
                "/v1/reference-sets/" + sid, headers=headers("core")
            ).json()
            == sealed
        )
        assert (
            restarted.get("/v1/files/" + uid, headers=headers()).content
            == b"hello"
        )
        (tmp_path / uid).write_bytes(b"corrupted")
        assert (
            restarted.get("/v1/files/" + uid, headers=headers()).status_code
            == 500
        )


def test_concurrent_conflicting_upload_preserves_winner(tmp_path):
    uid = uuid4().hex
    dsn = os.environ["P0_TEST_DSN"]
    barrier = threading.Barrier(2)
    # Independent app event loops exercise two actual PG transactions.
    apis = [TestClient(create_app(dsn, tmp_path, KEY)) for _ in range(2)]

    def upload(index):
        barrier.wait(timeout=3)
        return apis[index].post(
            "/v1/uploads",
            json=body(uid, str(index).encode()),
            headers=headers(),
        )

    try:
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(upload, range(2)))
        assert sorted(r.status_code for r in responses) == [200, 409]
        winner = next(
            i for i, r in enumerate(responses) if r.status_code == 200
        )
        assert (
            apis[0].get("/v1/files/" + uid, headers=headers()).content
            == str(winner).encode()
        )
        ref = responses[winner].json()["ref"]
        sid = uuid4().hex

        def pin(index):
            barrier.wait(timeout=3)
            return apis[index].post(
                "/v1/reference-sets/" + sid,
                json={"scope": SCOPE, "refs": [ref]},
                headers=headers("core"),
            )

        with ThreadPoolExecutor(2) as pool:
            pins = list(pool.map(pin, range(2)))
        assert all(r.status_code == 200 for r in pins)
        assert pins[0].json() == pins[1].json()
    finally:
        for api in apis:
            api.close()


def test_chunked_upload_bounded_before_json(tmp_path):
    with TestClient(
        create_app(os.environ["P0_TEST_DSN"], tmp_path, KEY)
    ) as api:
        response = api.post(
            "/v1/uploads",
            content=(b"x" * 100000 for _ in range(20)),
            headers=headers(),
        )
        assert response.status_code == 413
        assert (
            api.post(
                "/v1/reference-sets/bad%20id",
                json={"scope": SCOPE, "refs": []},
                headers=headers("core"),
            ).status_code
            == 422
        )
