"""Independent, bounded P0 control plane. No QwenPaw runtime imports."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import Field

from . import PROTOCOL_VERSION
from .auth import Auth
from .contracts import (
    ID,
    ExecutionContext,
    FileRef,
    Limits,
    RuntimeIdentity,
    Scope,
    Wire,
    digest,
)

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}
ACTIVE = {"ASSIGNED", "STARTING", "RUNNING"}


def fail(code, status=409):
    raise HTTPException(status, code)


def stamp(seconds):
    return (
        datetime.fromtimestamp(seconds, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


class CreateRun(Wire):
    request_id: str = Field(pattern=ID)
    scope: Scope
    session_id: str = Field(pattern=ID)
    input_ref: FileRef
    runtime_identity: RuntimeIdentity
    expected_revision: int | None = Field(default=None, ge=0)
    base_policy: Literal["latest_at_claim"] = "latest_at_claim"


class Register(Wire):
    worker_id: str = Field(pattern=ID)
    runtime_identity: RuntimeIdentity
    executor: Literal["fixture", "native"]
    slots: Literal[1] = 1


class Claim(Wire):
    request_id: str = Field(pattern=ID)


class AttemptAction(Wire):
    attempt_id: str = Field(pattern=ID)
    lease_epoch: int = Field(ge=1)


class Checkpoint(AttemptAction):
    commit_id: str = Field(pattern=ID)
    expected_revision: int = Field(ge=0)
    conversation_ref: FileRef
    files: dict[str, FileRef] = Field(default_factory=dict, max_length=16)
    cursor: int = Field(ge=1, le=2)
    next_segment: int = Field(ge=2, le=3)
    budget: dict[str, int]
    memory: None = None


class Boundary(AttemptAction):
    action: Literal["handoff", "finish", "failed"]


class Cleanup(AttemptAction):
    confirmed: bool


def event(db, run, kind, now, **values):
    seq = run.get("event_seq", 0) + 1
    run["event_seq"] = seq
    db["events"][f'{run["run_id"]}:{seq:08d}'] = {
        "run_id": run["run_id"],
        "event_seq": seq,
        "kind": kind,
        "at": stamp(now),
        **values,
    }


def worker(db, claims):
    obj = db["workers"].get(claims["sub"])
    if obj is None:
        fail("WORKER_NOT_REGISTERED", 403)
    return obj


def current(db, claims, body, now):
    a = db["attempts"].get(body.attempt_id)
    if not a or a["worker_id"] != claims["sub"]:
        fail("ATTEMPT_NOT_ASSIGNED", 403)
    r = db["runs"][a["run_id"]]
    if (
        a["lease_epoch"] != body.lease_epoch
        or a["state"] not in ACTIVE
        or r["current_attempt_id"] != a["attempt_id"]
        or a["lease_until"] <= now
        or r["deadline"] <= now
        or r["state"] in TERMINAL
    ):
        fail("STALE_ATTEMPT")
    return a, r, db["scopes"][r["scope_key"]]


def assignment(db, a, auth):
    r = db["runs"][a["run_id"]]
    s = db["scopes"][r["scope_key"]]
    scope = {k: s[k] for k in Scope.model_fields}
    context = ExecutionContext(
        scope=scope,
        actor_user_id=scope["owner_user_id"],
        session_id=r["session_id"],
        run_id=r["run_id"],
        attempt_id=a["attempt_id"],
        lease_epoch=a["lease_epoch"],
        base_revision=a["base_revision"],
        runtime_identity=r["runtime_identity"],
    ).model_dump()
    return {
        "context": context,
        "state": a["state"],
        "run_state": r["state"],
        "cleanup": a["cleanup"],
        "lease_until": stamp(a["lease_until"]),
        "input_ref": r["input_ref"],
        "checkpoint": deepcopy(a["restore"]),
        "file_token": auth.issue(
            "file-worker", a["worker_id"], "files", scope=scope, ttl=300
        ),
    }


def create_app(
    repository, file_url: str, signing_key: str, limits: Limits | None = None
):
    limits = limits or Limits()
    auth = Auth(signing_key)
    app = FastAPI(title="QwenPaw P0 control", version=PROTOCOL_VERSION)
    app.state.repository = repository
    app.state.fault_hook = (
        None  # deterministic in-process test barrier; no HTTP fault switch
    )

    @app.middleware("http")
    async def protocol(request, call_next):
        if (
            request.url.path != "/openapi.json"
            and request.headers.get("x-protocol-version") != PROTOCOL_VERSION
        ):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=426,
                content={"detail": "PROTOCOL_VERSION_REQUIRED"},
            )
        # Bound chunked requests too; Content-Length is not a trusted limit.
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 65536:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=413, content={"detail": "BODY_TOO_LARGE"}
                )
            body.extend(chunk)
        request._body = bytes(body)
        return await call_next(request)

    def identity(request, *roles):
        return auth.request(request, "core", *roles)

    def fault(name):
        if app.state.fault_hook:
            app.state.fault_hook(name)

    def pin(set_id, scope, refs):
        with httpx.Client(
            timeout=limits.request_timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.post(
                file_url + "/v1/reference-sets/" + set_id,
                headers={
                    "Authorization": "Bearer "
                    + auth.issue("core", "control", "files"),
                    "X-Protocol-Version": PROTOCOL_VERSION,
                },
                json={"scope": scope, "refs": refs},
            )
            if response.status_code != 200:
                fail("FILE_PIN_FAILED", 502)
            result = response.json()
            if result.get("status") != "PINNED" or digest(
                result.get("refs")
            ) != digest(refs):
                fail("FILE_PIN_INVALID", 502)
            return result

    @app.post("/v1/runs", status_code=202)
    def create(body: CreateRun, request: Request):
        claims = identity(request, "bff")
        if (
            claims.get("tenant_id") != body.scope.tenant_id
            or claims.get("user_id") != body.scope.owner_user_id
        ):
            fail("SCOPE_FORBIDDEN", 403)
        if (
            body.scope.scope_type == "standalone"
            and body.session_id != body.scope.scope_id
        ):
            fail("SESSION_SCOPE_MISMATCH", 422)
        key = digest([claims["sub"], body.scope.model_dump(), body.request_id])
        h = digest(body.model_dump())
        # File pin is idempotent and deliberately outside Core transactions.
        with repository.transaction() as (db, now):
            prior = db["requests"].get("create-" + key)
            if prior:
                if prior["hash"] != h:
                    fail("IDEMPOTENCY_CONFLICT")
                return deepcopy(db["runs"][prior["run_id"]])
        pin(
            "input-" + key,
            body.scope.model_dump(),
            [body.input_ref.model_dump()],
        )
        with repository.transaction() as (db, now):
            prior = db["requests"].get("create-" + key)
            if prior:
                if prior["hash"] != h:
                    fail("IDEMPOTENCY_CONFLICT")
                return deepcopy(db["runs"][prior["run_id"]])
            if (
                sum(r["state"] not in TERMINAL for r in db["runs"].values())
                >= 32
            ):
                fail("STATIC_ADMISSION_FULL", 429)
            scope_key = body.scope.key
            db["scopes"].setdefault(
                scope_key,
                {
                    **body.scope.model_dump(),
                    "revision": 0,
                    "active_run_id": None,
                    "head": None,
                    "lifecycle": "ACTIVE",
                },
            )
            run_id = uuid4().hex
            r = {
                **body.model_dump(exclude={"scope"}),
                "scope_key": scope_key,
                "run_id": run_id,
                "actor_user_id": claims["user_id"],
                "tenant_id": body.scope.tenant_id,
                "owner_user_id": body.scope.owner_user_id,
                "state": "QUEUED",
                "current_attempt_id": None,
                "lease_epoch": 0,
                "checkpoint": None,
                "deadline": now + 300,
                "input_set_id": "input-" + key,
            }
            db["runs"][run_id] = r
            db["requests"]["create-" + key] = {"hash": h, "run_id": run_id}
            event(db, r, "RUN_ACCEPTED", now)
            return deepcopy(r)

    @app.get("/v1/runs/{run_id}")
    def query_run(run_id: str, request: Request):
        claims = identity(request, "bff")
        with repository.transaction() as (db, now):
            r = db["runs"].get(run_id)
            if not r:
                fail("RUN_NOT_FOUND", 404)
            s = db["scopes"][r["scope_key"]]
            if (claims.get("tenant_id"), claims.get("user_id")) != (
                s["tenant_id"],
                s["owner_user_id"],
            ):
                fail("SCOPE_FORBIDDEN", 403)
            return {
                **deepcopy(r),
                "scope": deepcopy(s),
                "events": [
                    deepcopy(e)
                    for e in db["events"].values()
                    if e["run_id"] == run_id
                ],
            }

    @app.post("/v1/workers/register")
    def register(body: Register, request: Request):
        claims = identity(request, "worker")
        if claims["sub"] != body.worker_id:
            fail("WORKER_IDENTITY_MISMATCH", 403)
        with repository.transaction() as (db, now):
            prior = db["workers"].get(body.worker_id)
            value = {**body.model_dump(), "quarantined": False}
            if prior and any(
                prior[k] != v for k, v in body.model_dump().items()
            ):
                fail("WORKER_INSTANCE_IMMUTABLE")
            db["workers"].setdefault(body.worker_id, value)
            return deepcopy(db["workers"][body.worker_id])

    @app.post("/v1/claim")
    def claim(body: Claim, request: Request):
        claims = identity(request, "worker")
        key = "claim-" + digest([claims["sub"], body.request_id])
        with repository.transaction() as (db, now):
            w = worker(db, claims)
            previous = db["requests"].get(key)
            if previous:
                aid = previous["attempt_id"]
                return (
                    assignment(db, db["attempts"][aid], auth)
                    if aid
                    else {"assignment": None}
                )
            if w["quarantined"] or any(
                a["worker_id"] == claims["sub"] and a["cleanup"] == "PENDING"
                for a in db["attempts"].values()
            ):
                fail("SLOT_UNAVAILABLE")
            selected = None
            for r in db["runs"].values():
                s = db["scopes"][r["scope_key"]]
                if (
                    r["state"] != "QUEUED"
                    or r["deadline"] <= now
                    or r["runtime_identity"] != w["runtime_identity"]
                ):
                    continue
                if s["lifecycle"] != "ACTIVE" or s["active_run_id"] not in (
                    None,
                    r["run_id"],
                ):
                    continue
                # A terminal predecessor can release logical occupancy
                # before cleanup.
                if any(
                    a["scope_key"] == r["scope_key"]
                    and a["cleanup"] == "PENDING"
                    for a in db["attempts"].values()
                ):
                    continue
                if (
                    r["lease_epoch"] == 0
                    and r["expected_revision"] is not None
                    and r["expected_revision"] != s["revision"]
                ):
                    r["state"] = "FAILED"
                    event(db, r, "STALE_REVISION", now)
                    continue
                aid = uuid4().hex
                r["lease_epoch"] += 1
                restore = (
                    r["checkpoint"] if r["lease_epoch"] > 1 else s["head"]
                )
                a = {
                    "attempt_id": aid,
                    "run_id": r["run_id"],
                    "scope_key": r["scope_key"],
                    "worker_id": claims["sub"],
                    "lease_epoch": r["lease_epoch"],
                    "lease_until": now + limits.lease_seconds,
                    "state": "ASSIGNED",
                    "cleanup": "PENDING",
                    "base_revision": s["revision"],
                    "restore": deepcopy(restore),
                }
                a["cursor_base"] = (
                    (restore or {})
                    .get("session_cursors", {})
                    .get(r["session_id"], 0)
                )
                db["attempts"][aid] = a
                r.update(state="STARTING", current_attempt_id=aid)
                s["active_run_id"] = r["run_id"]
                event(db, r, "ATTEMPT_ASSIGNED", now, attempt_id=aid)
                selected = a
                break
            db["requests"][key] = {
                "attempt_id": selected["attempt_id"] if selected else None
            }
            result = (
                assignment(db, selected, auth)
                if selected
                else {"assignment": None}
            )
        fault("claim_committed")
        return result

    @app.get("/v1/claims/{request_id}")
    def query_claim(request_id: str, request: Request):
        claims = identity(request, "worker")
        key = "claim-" + digest([claims["sub"], request_id])
        with repository.transaction() as (db, now):
            previous = db["requests"].get(key)
            if previous is None:
                fail("CLAIM_NOT_FOUND", 404)
            aid = previous["attempt_id"]
            return (
                assignment(db, db["attempts"][aid], auth)
                if aid
                else {"assignment": None}
            )

    @app.post("/v1/ready")
    def ready(body: AttemptAction, request: Request):
        claims = identity(request, "worker")
        with repository.transaction() as (db, now):
            a, r, s = current(db, claims, body, now)
            if r["state"] == "STARTING":
                a["state"] = r["state"] = "RUNNING"
                event(db, r, "RUNTIME_READY", now)
            return {"state": r["state"]}

    @app.post("/v1/heartbeat")
    def heartbeat(body: AttemptAction, request: Request):
        claims = identity(request, "worker")
        with repository.transaction() as (db, now):
            a, r, s = current(db, claims, body, now)
            a["lease_until"] = min(now + limits.lease_seconds, r["deadline"])
            result = {
                "lease_until": stamp(a["lease_until"]),
                "lease_seconds": a["lease_until"] - now,
                "control": "CONTINUE",
            }
        fault("heartbeat_committed")
        return result

    @app.post("/v1/checkpoints")
    def checkpoint(body: Checkpoint, request: Request):
        claims = identity(request, "worker")
        h = digest(body.model_dump())
        with repository.transaction() as (db, now):
            c = db["commits"].get(body.commit_id)
            if c:
                if c["worker_id"] != claims["sub"]:
                    fail("COMMIT_FORBIDDEN", 403)
                if c["hash"] != h:
                    fail("IDEMPOTENCY_CONFLICT")
                if c["state"] == "COMMITTED":
                    return deepcopy(c)
            a, r, s = current(db, claims, body, now)
            if (
                r["state"] != "RUNNING"
                or s["revision"] != body.expected_revision
            ):
                fail("STALE_REVISION")
            if (
                body.budget != {"segments_used": body.cursor}
                or body.next_segment != body.cursor + 1
            ):
                fail("INVALID_CONTINUATION", 422)
            previous_cursor = (
                (r["checkpoint"] or {})
                .get("session_cursors", {})
                .get(r["session_id"], a["cursor_base"])
            )
            if body.cursor != previous_cursor + 1:
                fail("CURSOR_CONFLICT")
            from pathlib import PurePosixPath

            if any(
                PurePosixPath(p).is_absolute()
                or ".." in PurePosixPath(p).parts
                or "\\" in p
                for p in body.files
            ):
                fail("INVALID_LOGICAL_PATH", 422)
            head = (
                deepcopy(s["head"])
                if s["head"]
                else {"conversations": {}, "files": {}}
            )
            head["conversations"][
                r["session_id"]
            ] = body.conversation_ref.model_dump()
            head.setdefault("session_cursors", {})[
                r["session_id"]
            ] = body.cursor
            head["files"].update(
                {k: v.model_dump() for k, v in body.files.items()}
            )
            head.update(
                cursor=body.cursor,
                next_segment=body.next_segment,
                budget=body.budget,
                memory=None,
                revision=s["revision"] + 1,
                runtime_identity=r["runtime_identity"],
                scope={k: s[k] for k in Scope.model_fields},
            )
            refs = sorted(
                {
                    digest(v): v
                    for v in [
                        *head["conversations"].values(),
                        *head["files"].values(),
                    ]
                }.values(),
                key=lambda x: x["file_id"],
            )
            c = db["commits"].setdefault(
                body.commit_id,
                {
                    "commit_id": body.commit_id,
                    "state": "PREPARING",
                    "hash": h,
                    "worker_id": claims["sub"],
                    "attempt_id": a["attempt_id"],
                    "run_id": r["run_id"],
                    "head": head,
                    "reference_set_id": "checkpoint-" + body.commit_id,
                    "refs": refs,
                },
            )
            scope = {k: s[k] for k in Scope.model_fields}
            pending = deepcopy(c)
        pin(pending["reference_set_id"], scope, pending["refs"])
        with repository.transaction() as (db, now):
            c = db["commits"][body.commit_id]
            if c["state"] == "PREPARING":
                c["state"] = "PINNED"
        fault("pin_committed")
        with repository.transaction() as (db, now):
            c = db["commits"][body.commit_id]
            if c["state"] == "COMMITTED":
                return deepcopy(c)
            a, r, s = current(db, claims, body, now)
            if (
                c["state"] != "PINNED"
                or r["state"] != "RUNNING"
                or s["revision"] != body.expected_revision
            ):
                fail("STALE_REVISION")
            s["revision"] += 1
            s["head"] = deepcopy(c["head"])
            r["checkpoint"] = deepcopy(c["head"])
            c.update(state="COMMITTED", revision=s["revision"])
            event(
                db,
                r,
                "CHECKPOINT_COMMITTED",
                now,
                commit_id=body.commit_id,
                revision=s["revision"],
            )
            result = deepcopy(c)
        fault("publish_committed")
        return result

    @app.get("/v1/checkpoints/{commit_id}")
    def query_checkpoint(commit_id: str, request: Request):
        claims = identity(request, "worker")
        with repository.transaction() as (db, now):
            c = db["commits"].get(commit_id)
            if not c:
                fail("COMMIT_NOT_FOUND", 404)
            if c["worker_id"] != claims["sub"]:
                fail("COMMIT_FORBIDDEN", 403)
            return deepcopy(c)

    @app.post("/v1/boundary")
    def boundary(body: Boundary, request: Request):
        claims = identity(request, "worker")
        with repository.transaction() as (db, now):
            a = db["attempts"].get(body.attempt_id)
            if (
                a
                and a["worker_id"] == claims["sub"]
                and a.get("boundary") == body.action
                and a["lease_epoch"] == body.lease_epoch
            ):
                return {"state": db["runs"][a["run_id"]]["state"]}
            a, r, s = current(db, claims, body, now)
            if body.action != "failed" and (
                not r["checkpoint"] or r["state"] != "RUNNING"
            ):
                fail("COMMITTED_BOUNDARY_REQUIRED")
            a["state"] = "COMPLETED" if body.action == "finish" else "STOPPED"
            a["boundary"] = body.action
            r["current_attempt_id"] = None
            r["state"] = {
                "handoff": "RECOVERING",
                "finish": "SUCCEEDED",
                "failed": "FAILED",
            }[body.action]
            if r["state"] in TERMINAL:
                s["active_run_id"] = None
            event(db, r, "BOUNDARY_" + body.action.upper(), now)
            return {"state": r["state"]}

    @app.post("/v1/cleanup")
    def cleanup(body: Cleanup, request: Request):
        claims = identity(request, "worker")
        with repository.transaction() as (db, now):
            a = db["attempts"].get(body.attempt_id)
            if (
                not a
                or a["worker_id"] != claims["sub"]
                or a["lease_epoch"] != body.lease_epoch
            ):
                fail("ATTEMPT_NOT_ASSIGNED", 403)
            if a["state"] in ACTIVE:
                fail("STOP_BOUNDARY_REQUIRED")
            status = "CONFIRMED" if body.confirmed else "QUARANTINED"
            if a["cleanup"] != "PENDING" and a["cleanup"] != status:
                fail("CLEANUP_IMMUTABLE")
            a["cleanup"] = status
            if not body.confirmed:
                db["workers"][claims["sub"]]["quarantined"] = True
            r = db["runs"][a["run_id"]]
            if r["state"] == "RECOVERING" and body.confirmed:
                r["state"] = "QUEUED"
                event(db, r, "RECOVERY_QUEUED", now)
            return {"cleanup": status, "run_state": r["state"]}

    @app.post("/v1/recovery/scan")
    def scan(request: Request):
        identity(request, "coordinator")
        with repository.transaction() as (db, now):
            recovered = []
            for a in db["attempts"].values():
                if a["state"] not in ACTIVE or a["lease_until"] > now:
                    continue
                r = db["runs"][a["run_id"]]
                a.update(state="LOST", cleanup="QUARANTINED")
                db["workers"][a["worker_id"]]["quarantined"] = True
                r.update(state="RECOVERING", current_attempt_id=None)
                event(db, r, "LEASE_LOST", now)
                # Offline P0 has no external effect channel.
                # Old tasks only touch private files.
                # All authoritative writes stay fenced until
                # a new worker starts.
                if r["deadline"] > now:
                    r["state"] = "QUEUED"
                    recovered.append(r["run_id"])
                else:
                    r["state"] = "TIMED_OUT"
                    db["scopes"][r["scope_key"]]["active_run_id"] = None
            return {"recovered": recovered}

    from .openapi import install_openapi

    install_openapi(app)
    return app
