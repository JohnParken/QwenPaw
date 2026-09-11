"""P0 immutable file and reference-set service."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .auth import Auth
from .contracts import FileRef, Scope, digest

MAX_FILE = 1_048_576
MAX_BODY = 1_500_000
PROTOCOL = "p0.v1"


class Upload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
    scope: Scope
    content_base64: str


class RefSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Scope
    refs: list[FileRef] = Field(max_length=1024)


def _scope(value: Any) -> Scope:
    try:
        return Scope.model_validate(value)
    except ValidationError:
        raise HTTPException(422, "INVALID_SCOPE") from None


def _protocol(request: Request) -> None:
    if request.headers.get("X-Protocol-Version") != PROTOCOL:
        raise HTTPException(400, "PROTOCOL_VERSION_REQUIRED")


def _id(value: str, label: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", value):
        raise HTTPException(422, f"INVALID_{label.upper()}")
    return value


def _connect(dsn: str):
    return psycopg.connect(dsn)


def _migrate(dsn: str) -> None:
    sql = (
        Path(__file__).parents[2] / "cloud/migrations/002_files.sql"
    ).read_text()
    with _connect(dsn) as conn:
        conn.execute(sql)
        conn.commit()


async def _json_body(request: Request) -> dict[str, Any]:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY:
            raise HTTPException(413, "REQUEST_TOO_LARGE")
        chunks.append(chunk)
    try:
        value = json.loads(b"".join(chunks))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "INVALID_JSON") from None
    if not isinstance(value, dict):
        raise HTTPException(422, "INVALID_JSON")
    return value


def _lock(conn: Any, kind: str, identifier: str) -> None:
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"{kind}:{identifier}",),
    )


def _authorized(
    request: Request,
    auth: Auth,
    *,
    scope: Scope | None = None,
    pin: bool = False,
) -> dict:
    claims = auth.request(request, "files", "file-worker", "core")
    role = claims["role"]
    if pin and role != "core":
        raise HTTPException(403, "FILE_WORKER_CANNOT_PIN")
    if scope is not None and role == "file-worker":
        if claims.get("scope") != scope.model_dump():
            raise HTTPException(403, "SCOPE_FORBIDDEN")
    return claims


def _decode(value: str) -> bytes:
    if len(value) > ((MAX_FILE + 2) // 3) * 4 + 4:
        raise HTTPException(413, "FILE_TOO_LARGE")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "INVALID_BASE64") from None
    if len(data) > MAX_FILE:
        raise HTTPException(413, "FILE_TOO_LARGE")
    return data


def _durable_blob(root: Path, file_id: str, data: bytes) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=root)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        target = root / file_id
        os.replace(temporary, target)
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return target
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def create_app(dsn: str, root: Path, signing_key: str) -> FastAPI:
    _migrate(dsn)
    app = FastAPI(title="QwenPaw P0 File API", version=PROTOCOL)
    auth = Auth(signing_key)
    root = Path(root)

    @app.post("/v1/uploads")
    async def upload(request: Request):
        _protocol(request)
        _authorized(request, auth)
        try:
            body = Upload.model_validate(await _json_body(request))
        except (ValidationError, ValueError):
            raise HTTPException(422, "INVALID_UPLOAD") from None
        _authorized(request, auth, scope=body.scope)
        data = _decode(body.content_base64)
        sha = hashlib.sha256(data).hexdigest()
        ref = FileRef(file_id=body.upload_id, sha256=sha, size=len(data))
        blob = root / body.upload_id
        with _connect(dsn) as conn:
            _lock(conn, "upload", body.upload_id)
            row = conn.execute(
                "SELECT scope, sha256, size, status "
                "FROM files.files "
                "WHERE file_id=%s "
                "AND version=1",
                (body.upload_id,),
            ).fetchone()
            if row:
                if (
                    row[0] != body.scope.model_dump()
                    or row[1] != sha
                    or row[2] != len(data)
                ):
                    raise HTTPException(409, "UPLOAD_ID_CONFLICT")
                conn.commit()
                return {"ref": ref.model_dump(), "status": row[3]}
            _durable_blob(root, body.upload_id, data)
            conn.execute(
                "INSERT INTO files.files("
                "file_id,scope,sha256,size,status,blob_path"
                ") VALUES (%s,%s,%s,%s,'READY',%s)",
                (
                    body.upload_id,
                    json.dumps(body.scope.model_dump()),
                    sha,
                    len(data),
                    str(blob),
                ),
            )
            conn.commit()
        return {"ref": ref.model_dump(), "status": "READY"}

    @app.get("/v1/files/{file_id}")
    async def download(file_id: str, request: Request, version: int = 1):
        _protocol(request)
        claims = _authorized(request, auth)
        with _connect(dsn) as conn:
            row = conn.execute(
                "SELECT scope, sha256, size, status, blob_path "
                "FROM files.files "
                "WHERE file_id=%s "
                "AND version=%s",
                (file_id, version),
            ).fetchone()
        if not row:
            raise HTTPException(404, "FILE_NOT_FOUND")
        if (
            claims["role"] == "file-worker"
            and claims.get("scope") != _scope(row[0]).model_dump()
        ):
            raise HTTPException(403, "SCOPE_FORBIDDEN")
        if row[3] != "READY":
            raise HTTPException(409, "FILE_NOT_READY")
        try:
            data = Path(row[4]).read_bytes()
        except OSError:
            raise HTTPException(404, "FILE_NOT_FOUND") from None
        if hashlib.sha256(data).hexdigest() != row[1] or len(data) != row[2]:
            raise HTTPException(500, "FILE_INTEGRITY_ERROR")
        return Response(data, media_type="application/octet-stream")

    @app.post("/v1/reference-sets/{set_id}")
    async def pin(set_id: str, request: Request):
        _protocol(request)
        _id(set_id, "set_id")
        _authorized(request, auth, pin=True)
        try:
            body = RefSet.model_validate(await _json_body(request))
        except (ValidationError, ValueError):
            raise HTTPException(422, "INVALID_REFERENCE_SET") from None
        _authorized(request, auth, scope=body.scope, pin=True)
        ref_dicts = [ref.model_dump() for ref in body.refs]
        with _connect(dsn) as conn:
            _lock(conn, "set", set_id)
            for ref in body.refs:
                row = conn.execute(
                    "SELECT scope, sha256, size, status "
                    "FROM files.files "
                    "WHERE file_id=%s "
                    "AND version=%s",
                    (ref.file_id, ref.version),
                ).fetchone()
                if (
                    not row
                    or row[3] != "READY"
                    or row[1] != ref.sha256
                    or row[2] != ref.size
                ):
                    raise HTTPException(409, "REFERENCE_NOT_READY")
                if row[0] != body.scope.model_dump():
                    raise HTTPException(403, "SCOPE_FORBIDDEN")
            set_digest = digest(
                {"scope": body.scope.model_dump(), "refs": ref_dicts}
            )
            existing = conn.execute(
                "SELECT scope, digest, refs, status "
                "FROM files.reference_sets "
                "WHERE set_id=%s",
                (set_id,),
            ).fetchone()
            if existing:
                if (
                    existing[1] != set_digest
                    or existing[0] != body.scope.model_dump()
                ):
                    raise HTTPException(409, "REFERENCE_SET_CONFLICT")
                conn.commit()
                return {
                    "set_id": set_id,
                    "digest": existing[1],
                    "refs": existing[2],
                    "status": existing[3],
                }
            conn.execute(
                "INSERT INTO files.reference_sets("
                "set_id, scope, digest, refs, status"
                ") VALUES (%s,%s,%s,%s,'PINNED')",
                (
                    set_id,
                    json.dumps(body.scope.model_dump()),
                    set_digest,
                    json.dumps(ref_dicts),
                ),
            )
            conn.commit()
        return {
            "set_id": set_id,
            "digest": set_digest,
            "refs": ref_dicts,
            "status": "PINNED",
        }

    @app.get("/v1/reference-sets/{set_id}")
    async def get_set(set_id: str, request: Request):
        _protocol(request)
        _id(set_id, "set_id")
        claims = _authorized(request, auth)
        with _connect(dsn) as conn:
            row = conn.execute(
                "SELECT scope, digest, refs, status "
                "FROM files.reference_sets "
                "WHERE set_id=%s",
                (set_id,),
            ).fetchone()
        if not row:
            raise HTTPException(404, "REFERENCE_SET_NOT_FOUND")
        if (
            claims["role"] == "file-worker"
            and claims.get("scope") != _scope(row[0]).model_dump()
        ):
            raise HTTPException(403, "SCOPE_FORBIDDEN")
        return {
            "set_id": set_id,
            "digest": row[1],
            "refs": row[2],
            "status": row[3],
        }

    from .openapi import install_openapi

    install_openapi(
        app,
        models=(Upload, RefSet),
        request_models={
            ("post", "/v1/uploads"): Upload,
            ("post", "/v1/reference-sets/{set_id}"): RefSet,
        },
    )
    return app
