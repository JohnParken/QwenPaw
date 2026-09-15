"""Versioned, JSON-only P0 wire types. No database or runtime imports."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$"


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Scope(Wire):
    tenant_id: str = Field(pattern=ID)
    owner_user_id: str = Field(pattern=ID)
    scope_type: Literal["standalone", "workspace"]
    scope_id: str = Field(pattern=ID)

    @property
    def key(self) -> str:
        return digest(self.model_dump())


class FileRef(Wire):
    file_id: str = Field(pattern=ID)
    version: Literal[1] = 1
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=104857600)
    mime_type: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=255)


class Verification(Wire):
    level: Literal["structural_verified", "content_checked", "unverified"]
    checks: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class Artifact(Wire):
    artifact_id: str = Field(pattern=ID)
    file_ref: FileRef
    verification: Verification


class LiveSnapshot(Wire):
    attempt_id: str = Field(pattern=ID)
    lease_epoch: int = Field(ge=1)
    sequence: int = Field(ge=1)
    text: str = Field(max_length=65536)


class RuntimeIdentity(Wire):
    kind: Literal["local_bundle"] = "local_bundle"
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    dirty_diff_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    dependency_lock_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    python: Literal["3.12.11"] = "3.12.11"
    arch: str


class ExecutionContext(Wire):
    scope: Scope
    actor_user_id: str = Field(pattern=ID)
    session_id: str = Field(pattern=ID)
    run_id: str = Field(pattern=ID)
    attempt_id: str = Field(pattern=ID)
    lease_epoch: int = Field(ge=1, le=9007199254740991)
    base_revision: int = Field(ge=0, le=9007199254740991)
    runtime_identity: RuntimeIdentity
    profile: Literal["offline-p0-v1"] = "offline-p0-v1"

    @model_validator(mode="after")
    def owner_only(self):
        if self.actor_user_id != self.scope.owner_user_id:
            raise ValueError("P0 is owner-only")
        if (
            self.scope.scope_type == "standalone"
            and self.scope.scope_id != self.session_id
        ):
            raise ValueError("standalone scope must equal session_id")
        return self


class Limits(Wire):
    heartbeat_seconds: float = Field(default=1, gt=0)
    lease_seconds: float = Field(default=12, gt=0, le=120)
    safety_seconds: float = Field(default=3, gt=0)
    request_timeout: float = Field(default=2, gt=0)
    stop_grace: float = Field(default=1, gt=0, le=5)
    output_bytes: int = Field(default=65536, ge=1024, le=1048576)
    file_bytes: int = Field(default=1048576, ge=1024, le=1048576)
    segment_seconds: float = Field(default=60, gt=0, le=120)
    test_seconds: float = Field(default=180, gt=0, le=300)
    retries: int = Field(default=2, ge=0, le=3)

    @model_validator(mode="after")
    def timing(self):
        if self.safety_seconds <= self.stop_grace:
            raise ValueError("safety margin must exceed stop grace")
        if (
            self.heartbeat_seconds + self.request_timeout + self.safety_seconds
            >= self.lease_seconds
        ):
            raise ValueError("lease must exceed heartbeat + timeout + safety")
        if self.test_seconds <= self.segment_seconds + self.lease_seconds:
            raise ValueError("test deadline must cover segment and lease")
        return self
