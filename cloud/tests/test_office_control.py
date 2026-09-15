from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from qwenpaw_cloud.auth import Auth
from qwenpaw_cloud.contracts import Limits
from qwenpaw_cloud.core import create_app
from qwenpaw_cloud.repository import FixtureRepository
from qwenpaw_cloud.sandbox import (
    PathMap,
    ShellPolicy,
    ShellPolicyError,
)


KEY = "office-test-signing-key-with-32-bytes-minimum"


def _headers(auth):
    return {
        "Authorization": "Bearer "
        + auth.issue(
            "bff", "trusted-bff", "core", tenant_id="t1", user_id="u1"
        ),
        "X-Protocol-Version": "p0.v1",
    }


def test_session_crud_and_idempotency(tmp_path):
    auth = Auth(KEY)
    api = TestClient(create_app(FixtureRepository(), "http://files", KEY))
    body = {"request_id": "request1", "title": "Quarterly report"}
    first = api.post("/v1/sessions", json=body, headers=_headers(auth))
    assert first.status_code == 201
    assert api.post("/v1/sessions", json=body, headers=_headers(auth)).json() == first.json()
    session_id = first.json()["session_id"]
    assert api.get("/v1/sessions", headers=_headers(auth)).json()["items"][0]["session_id"] == session_id
    assert api.get(f"/v1/sessions/{session_id}/messages", headers=_headers(auth)).json() == {"items": []}
    assert api.delete(f"/v1/sessions/{session_id}", headers=_headers(auth)).status_code == 204


def test_shell_policy_and_artifact_boundary(tmp_path):
    root = tmp_path / "attempts"
    root.mkdir(mode=0o700)
    # Minimal duck-typed context; PathMap only reads these fields.
    class Scope:
        key = "scope"

    class Context:
        scope = Scope()
        attempt_id = "attempt"

    paths = PathMap.create(root, Context())
    skill_root = tmp_path / "skills"
    skill_root.mkdir()
    policy = ShellPolicy(paths, skill_root, {"python", "node"})
    assert policy.validate(["python", "script.py"], paths.root / "workspace", "docx").argv[0] == "python"
    with pytest.raises(ShellPolicyError, match="COMMAND_NOT_ALLOWED"):
        policy.validate(["curl", "https://example.com"], paths.root / "workspace", "docx")
    with pytest.raises(ShellPolicyError, match="CWD_OUTSIDE_ATTEMPT"):
        policy.validate(["python"], tmp_path, "docx")
    outside = paths.root / "workspace" / "bad.docx"
    outside.write_bytes(b"x")
    with pytest.raises(ShellPolicyError, match="ARTIFACT_OUTSIDE_OUTPUT"):
        policy.artifact_path(outside)
    artifact = paths.root / "output" / "ok.docx"
    artifact.write_bytes(b"x")
    assert policy.artifact_path(artifact) == artifact.resolve()
