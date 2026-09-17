# -*- coding: utf-8 -*-
"""Configuration for the standalone Office agent service."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class OfficeSettings:
    """Environment-owned settings with safe development defaults."""

    production: bool = False
    host: str = "127.0.0.1"
    port: int = 8090
    database_url: str | None = None
    object_store_backend: str = "memory"
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_prefix: str = "qwenpaw-office"
    work_root: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "qwenpaw-office")
    skill_bundle_path: Path = field(default_factory=lambda: Path(__file__).with_name("skills"))
    max_upload_bytes: int = 50 * 1024 * 1024
    allowed_providers: tuple[str, ...] = ("openai", "tlprovider")
    default_provider: str = "tlprovider"
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    tl_model: str = "default"
    tl_base_url: str = ""

    @classmethod
    def from_env(cls) -> "OfficeSettings":
        prefix = "QWENPAW_OFFICE_"
        providers = tuple(
            value.strip().lower()
            for value in os.environ.get(prefix + "ALLOWED_PROVIDERS", "openai,tlprovider").split(",")
            if value.strip()
        )
        return cls(
            production=_env_bool(prefix + "PRODUCTION"),
            host=os.environ.get(prefix + "HOST", "127.0.0.1"),
            port=int(os.environ.get(prefix + "PORT", "8090")),
            database_url=os.environ.get(prefix + "DATABASE_URL") or None,
            object_store_backend=os.environ.get(prefix + "OBJECT_STORE_BACKEND", "memory").lower(),
            s3_endpoint_url=os.environ.get(prefix + "S3_ENDPOINT_URL") or None,
            s3_bucket=os.environ.get(prefix + "S3_BUCKET") or None,
            s3_region=os.environ.get(prefix + "S3_REGION", "us-east-1"),
            s3_access_key=os.environ.get(prefix + "S3_ACCESS_KEY") or None,
            s3_secret_key=os.environ.get(prefix + "S3_SECRET_KEY") or None,
            s3_prefix=os.environ.get(prefix + "S3_PREFIX", "qwenpaw-office"),
            work_root=Path(os.environ.get(prefix + "WORK_ROOT", str(Path(tempfile.gettempdir()) / "qwenpaw-office"))),
            skill_bundle_path=Path(os.environ.get(prefix + "SKILL_BUNDLE_PATH", str(Path(__file__).with_name("skills")))),
            max_upload_bytes=int(os.environ.get(prefix + "MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))),
            allowed_providers=providers,
            default_provider=os.environ.get(prefix + "DEFAULT_PROVIDER", "tlprovider").lower(),
            openai_model=os.environ.get(prefix + "OPENAI_MODEL", "gpt-4.1-mini"),
            openai_base_url=os.environ.get(prefix + "OPENAI_BASE_URL", "https://api.openai.com/v1"),
            openai_api_key=os.environ.get(prefix + "OPENAI_API_KEY", ""),
            tl_model=os.environ.get(prefix + "TL_MODEL", "default"),
            tl_base_url=os.environ.get(prefix + "TL_BASE_URL", ""),
        )

    def static_readiness(self) -> list[str]:
        failures: list[str] = []
        if self.default_provider not in self.allowed_providers:
            failures.append("default provider is not allowed")
        if set(self.allowed_providers) - {"openai", "tlprovider"}:
            failures.append("only openai and tlprovider are supported")
        if self.production and not self.database_url:
            failures.append("production requires PostgreSQL")
        if self.production and self.object_store_backend != "s3":
            failures.append("production requires the S3 object store")
        if self.object_store_backend == "s3" and not self.s3_bucket:
            failures.append("S3 bucket is not configured")
        if self.default_provider == "openai" and not self.openai_api_key:
            failures.append("OpenAI API key is not configured")
        if self.default_provider == "tlprovider" and not self.tl_base_url:
            failures.append("TLProvider base URL is not configured")
        return failures


__all__ = ["OfficeSettings"]
