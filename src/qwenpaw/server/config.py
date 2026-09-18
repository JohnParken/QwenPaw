"""Explicit server configuration; never reads personal assistant profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from ..providers.tl_config import TLConfig
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class ToolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    description: str
    input_schema: dict
    execution: str = "sandbox"
    approval: bool = False
    timeout: int = Field(default=60, ge=1, le=3600)


class AssistantDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: str = Field(min_length=1)
    name: str = "assistant"
    system_prompt: str
    model: str
    model_protocol: Literal["openai", "tl"] = "openai"
    tl_config: TLConfig | None = None
    context_size: int = Field(default=32768, ge=2048)
    base_url: str | None = None
    max_iters: int = Field(default=20, ge=1, le=100)
    tools: tuple[ToolDefinition, ...] = ()
    public_knowledge: tuple[str, ...] = ()
    auto_memory: bool = True
    embedding_model: str | None = None

    @model_validator(mode="after")
    def validate_protocol(self):
        if self.model_protocol == "tl":
            url = urlsplit(self.base_url or "")
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.query
                or url.fragment
                or url.username
                or url.password
            ):
                raise ValueError(
                    "TL requires an HTTP(S) service root without URL credentials"
                )
            if self.embedding_model:
                raise ValueError(
                    "TL does not expose an embeddings endpoint; omit embedding_model"
                )
        elif self.tl_config is not None:
            raise ValueError("tl_config requires model_protocol=tl")
        return self


class ServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    storage_table_prefix: str = Field(
        default="qp_service_", pattern=r"^[a-z][a-z0-9_]{0,30}_$"
    )
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_url: SecretStr
    service_token: SecretStr
    internal_token: SecretStr
    definition_path: Path
    redis_url: str | None = None
    controller_url: str = "http://qwenpaw-controller:8091"
    model_api_key: SecretStr = SecretStr("")
    concurrency: int = Field(default=10, ge=1, le=100)
    per_user_concurrency: int = Field(default=4, ge=1, le=100)
    lease_seconds: int = Field(default=60, ge=15)
    approval_timeout: int = Field(default=600, ge=10)
    idle_seconds: int = Field(default=900, ge=30)
    namespace: str = "qwenpaw"
    sandbox_image: str = "qwenpaw-server:local"
    workspace_pvc: str = "qwenpaw-workspaces"
    workspace_root: Path = Path("/workspaces")
    s3_bucket: str = "qwenpaw-files"
    s3_endpoint: str | None = None
    s3_region: str = "us-east-1"
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1)

    @classmethod
    def from_env(cls) -> "ServerConfig":
        values = {}
        for name in cls.model_fields:
            value = os.environ.get("QWENPAW_SERVER_" + name.upper())
            if value is not None:
                values[name] = value
        config = cls.model_validate(values)
        for token in (config.service_token, config.internal_token):
            if len(token.get_secret_value()) < 32:
                raise ValueError(
                    "Server service tokens must contain at least 32 characters"
                )
        return config

    def definition(self) -> AssistantDefinition:
        return AssistantDefinition.model_validate(
            json.loads(self.definition_path.read_text())
        )
