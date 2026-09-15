# -*- coding: utf-8 -*-
"""Wire models for the trusted-BFF Office API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_identifier(value: str, field_name: str = "identifier") -> str:
    value = value.strip()
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid {field_name}")
    return value


@dataclass(frozen=True)
class RequestIdentity:
    tenant_id: str
    user_id: str
    request_id: str
    trace_id: str | None = None


class SessionCreate(BaseModel):
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=1_000_000)
    file_ids: list[str] = Field(default_factory=list, max_length=64)
    artifact_ids: list[str] = Field(default_factory=list, max_length=64)
    provider: Literal["openai", "tlprovider"] | None = None

    @field_validator("file_ids")
    @classmethod
    def validate_file_ids(cls, values: list[str]) -> list[str]:
        return [validate_identifier(value, "file_id") for value in values]

    @field_validator("artifact_ids")
    @classmethod
    def validate_artifact_ids(cls, values: list[str]) -> list[str]:
        return [validate_identifier(value, "artifact_id") for value in values]


class CancelRequest(BaseModel):
    request_id: str | None = None

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str | None) -> str | None:
        return validate_identifier(value, "request_id") if value is not None else None


class OfficeEvent(BaseModel):
    event: str
    request_id: str
    session_id: str
    turn_id: str
    sequence: int
    timestamp: str = Field(default_factory=utc_now)
    data: dict[str, Any] = Field(default_factory=dict)


class MessageResult(BaseModel):
    request_id: str
    session_id: str
    turn_id: str
    status: Literal["completed", "failed", "interrupted"]
    message: str = ""
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)


__all__ = ["CancelRequest", "MessageCreate", "MessageResult", "OfficeEvent", "RequestIdentity", "SessionCreate", "utc_now", "validate_identifier"]
