"""G1/G2 role and wire checks without database dependencies."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from qwenpaw_cloud.__main__ import Config
from qwenpaw_cloud.auth import Auth
from qwenpaw_cloud.contracts import ExecutionContext, Limits
from qwenpaw_cloud.identity import local_identity


def test_worker_rejects_database_and_signing_capability(tmp_path):
    base = dict(
        role="worker",
        root=str(tmp_path),
        worker_id="w1",
        token_file=str(tmp_path / "token"),
        runtime_identity=local_identity(),
    )
    assert Config(**base).role == "worker"
    for forbidden in (
        {"dsn": "postgresql://test@localhost/private_test"},
        {"signing_key_file": "/test/key"},
    ):
        with pytest.raises(ValueError):
            Config(**base, **forbidden)
    with pytest.raises(ValueError):
        Config(**{**base, "profile": "linux-production"})
    with pytest.raises(ValueError):
        Config(**{**base, "file_url": "https://elsewhere.invalid"})


def test_worker_import_has_no_core_database_dependency():
    code = (
        "import qwenpaw_cloud.worker,sys; "
        'assert "psycopg" not in sys.modules; '
        'assert "qwenpaw_cloud.repository" not in sys.modules; '
        'assert "qwenpaw_cloud.core" not in sys.modules'
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            "PATH": "/usr/bin:/bin",
        },
        timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_service_identity_audience_expiry_and_signature():
    from fastapi import HTTPException

    auth = Auth("synthetic-test-key-" + "x" * 32)
    token = auth.issue("worker", "w1", "core")
    assert auth.verify(token, "core", ("worker",))["sub"] == "w1"
    for candidate, audience, roles in (
        (token, "files", ("worker",)),
        (token, "core", ("bff",)),
        (token + "x", "core", ("worker",)),
        (auth.issue("worker", "w1", "core", ttl=-1), "core", ("worker",)),
    ):
        with pytest.raises(HTTPException):
            auth.verify(candidate, audience, roles)


def test_fixture_lifecycle_does_not_load_native_runtime():
    code = (
        "import qwenpaw_cloud.runner,sys; "
        "assert not any("
        'k=="qwenpaw" or k.startswith("qwenpaw.") for k in sys.modules)'
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            "PATH": "/usr/bin:/bin",
        },
        timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_synthetic_users_with_same_names_get_private_paths(tmp_path):
    from qwenpaw_cloud.sandbox import PathMap

    identity = local_identity()
    roots = []
    for user in ("u1", "u2"):
        context = ExecutionContext(
            scope={
                "tenant_id": "t1",
                "owner_user_id": user,
                "scope_type": "standalone",
                "scope_id": "same-name",
            },
            actor_user_id=user,
            session_id="same-name",
            run_id="same-run",
            attempt_id="same-attempt",
            lease_epoch=1,
            base_revision=0,
            runtime_identity=identity,
        )
        paths = PathMap.create(tmp_path / "private", context)
        (paths.root / "workspace/marker").write_text(user)
        roots.append(paths.root)
    assert roots[0] != roots[1]
    assert [(root / "workspace/marker").read_text() for root in roots] == [
        "u1",
        "u2",
    ]


def test_dependency_drift_refuses_runtime_identity(monkeypatch):
    from qwenpaw_cloud import identity

    monkeypatch.setattr(identity.metadata, "version", lambda name: "0.0.wrong")
    with pytest.raises(ValueError, match="INSTALLED_DEPENDENCY_MISMATCH"):
        identity.local_identity()
