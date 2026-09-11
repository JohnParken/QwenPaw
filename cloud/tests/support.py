"""Actual loopback service processes with synthetic private configuration."""
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql

from qwenpaw_cloud.auth import Auth
from qwenpaw_cloud.client import APIClient
from qwenpaw_cloud.contracts import Limits
from qwenpaw_cloud.identity import local_identity

REPO = Path(__file__).resolve().parents[2]


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Harness:
    def __init__(self, root, dsn, *, executor="fixture", limits=None):
        self.root = root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.limits = limits or Limits()
        self.executor = executor
        parsed = urlsplit(dsn)
        name = "p0_" + uuid4().hex[:12] + "_test"
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL(
                    "CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'"
                ).format(sql.Identifier(name))
            )
        self.dsn = urlunsplit(parsed._replace(path="/" + name))
        self.identity = local_identity()
        self.key = secrets.token_hex(32)
        self.auth = Auth(self.key)
        keyfile = self.root / "service-key"
        keyfile.write_text(self.key)
        keyfile.chmod(0o600)
        self.file_url, self.core_url = (
            f"http://127.0.0.1:{port()}",
            f"http://127.0.0.1:{port()}",
        )
        self.configs = {}
        for role, url in [("files", self.file_url), ("core", self.core_url)]:
            config = {
                "role": role,
                "profile": "macos-dev",
                "port": int(url.rsplit(":", 1)[1]),
                "dsn": self.dsn,
                "signing_key_file": str(keyfile),
                "root": str(self.root / role),
                "runtime_identity": self.identity.model_dump(),
                "file_url": self.file_url,
                "core_url": self.core_url,
                "limits": self.limits.model_dump(),
            }
            path = self.root / (role + ".json")
            path.write_text(json.dumps(config))
            self.configs[role] = path
        self.procs = {}
        self.logs = []
        self.start("files")
        self.start("core")

    def env(self):
        # No parent model credentials/DSNs inherited even by Supervisor.
        return {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.root),
            "PYTHONPATH": str(REPO / "src"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(self.root),
        }

    def start(self, role):
        log = open(self.root / (role + ".log"), "ab")
        self.logs.append(log)
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "qwenpaw_cloud",
                role,
                "--config",
                str(self.configs[role]),
            ],
            cwd=REPO,
            env=self.env(),
            stdout=log,
            stderr=log,
        )
        self.procs[role] = proc
        url = self.file_url if role == "files" else self.core_url
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if proc.poll() is not None:
                raise RuntimeError((self.root / (role + ".log")).read_text())
            try:
                if (
                    httpx.get(
                        url + "/openapi.json", timeout=0.3, trust_env=False
                    ).status_code
                    == 200
                ):
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.05)
        raise RuntimeError("service startup deadline")

    def stop(self, role):
        proc = self.procs.pop(role)
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def close(self):
        for role in list(self.procs):
            self.stop(role)
        for log in self.logs:
            log.close()
        # Retain PG facts and files. Explicit reset is an operator-only step.

    def client(self, role, subject="client", **claims):
        return APIClient(
            self.core_url,
            self.auth.issue(role, subject, "core", **claims),
            self.limits,
        )

    def file_client(self, scope):
        return APIClient(
            self.file_url,
            self.auth.issue(
                "file-worker", "test-uploader", "files", scope=scope
            ),
            self.limits,
        )

    def new_run(self, scope, session_id, marker, **extra):
        file = self.file_client(scope).upload(
            scope, json.dumps({"marker": marker}).encode()
        )
        bff = self.client(
            "bff", tenant_id=scope["tenant_id"], user_id=scope["owner_user_id"]
        )
        return bff.post(
            "/v1/runs",
            {
                "request_id": uuid4().hex,
                "scope": scope,
                "session_id": session_id,
                "input_ref": file,
                "runtime_identity": self.identity.model_dump(),
                **extra,
            },
        )

    def register(self, wid):
        client = self.client("worker", wid)
        client.post(
            "/v1/workers/register",
            {
                "worker_id": wid,
                "runtime_identity": self.identity.model_dump(),
                "executor": self.executor,
                "slots": 1,
            },
        )
        return client

    def worker(self, wid, assignment=None, kill_after_commit=False):
        workroot = self.root / ("worker-" + wid)
        workroot.mkdir(mode=0o700)
        tokenfile = self.root / (wid + ".token")
        tokenfile.write_text(self.auth.issue("worker", wid, "core"))
        tokenfile.chmod(0o600)
        path = self.root / (wid + ".json")
        config = {
            "role": "worker",
            "worker_id": wid,
            "token_file": str(tokenfile),
            "root": str(workroot),
            "core_url": self.core_url,
            "file_url": self.file_url,
            "runtime_identity": self.identity.model_dump(),
            "executor": self.executor,
            "limits": self.limits.model_dump(),
        }
        path.write_text(json.dumps(config))
        argv = [
            sys.executable,
            "-m",
            "qwenpaw_cloud",
            "worker",
            "--config",
            str(path),
            "--once",
        ]
        if assignment is not None:
            allocation = self.root / (wid + "-assignment.json")
            allocation.write_text(json.dumps(assignment))
            argv = [
                sys.executable,
                str(REPO / "cloud/tests/drive_assignment.py"),
                str(path),
                str(allocation),
            ]
        if kill_after_commit:
            barrier = self.root / (wid + "-commit-barrier.json")
            env = self.env()
            env["P0_TEST_COMMIT_BARRIER"] = str(barrier)
            with open(self.root / (wid + "-crash.log"), "w") as logfile:
                proc = subprocess.Popen(
                    argv, cwd=REPO, env=env, stdout=logfile, stderr=logfile
                )
                deadline = time.monotonic() + 30
                try:
                    while time.monotonic() < deadline:
                        if barrier.exists():
                            value = json.loads(barrier.read_text())
                            proc.kill()
                            proc.wait(3)
                            return value
                        if proc.poll() is not None:
                            raise RuntimeError(
                                (self.root / (wid + "-crash.log")).read_text()
                            )
                        time.sleep(0.025)
                    raise RuntimeError("committed barrier not reached")
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(3)
        result = subprocess.run(
            argv,
            cwd=REPO,
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=self.limits.test_seconds,
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-8000:])
        return json.loads(result.stdout)

    def query(self, run):
        return self.client("bff", tenant_id="t1", user_id="u1").get(
            "/v1/runs/" + run["run_id"]
        )


def scope(session="s1", workspace=False):
    return {
        "tenant_id": "t1",
        "owner_user_id": "u1",
        "scope_type": "workspace" if workspace else "standalone",
        "scope_id": "ws1" if workspace else session,
    }
