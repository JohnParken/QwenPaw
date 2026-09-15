"""Separate P0 role entry points."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator

from .contracts import Limits, RuntimeIdentity, Wire


class Config(Wire):
    profile: Literal["macos-dev"] = "macos-dev"
    role: Literal["core", "files", "worker"]
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=18080, ge=1024, le=65535)
    dsn: str | None = None
    signing_key_file: str | None = None
    token_file: str | None = None
    core_url: str = "http://127.0.0.1:18080"
    file_url: str = "http://127.0.0.1:18081"
    root: str
    worker_id: str | None = None
    runtime_identity: RuntimeIdentity
    executor: Literal["fixture", "native", "office-agent"] = "fixture"
    model_provider: Literal["tl", "openai"] | None = None
    model_id: str | None = None
    model_base_url: str | None = None
    model_credential_file: str | None = None
    shell_mode: Literal["sandboxed", "trusted_container"] = "sandboxed"
    limits: Limits = Limits()

    @model_validator(mode="after")
    def role_boundary(self):
        if self.role == "worker":
            if (
                self.dsn
                or self.signing_key_file
                or not self.token_file
                or not self.worker_id
            ):
                raise ValueError(
                    "Worker receives only its token, never DSN/signing key"
                )
            if self.executor == "office-agent" and (
                not self.model_provider
                or not self.model_id
                or not self.model_base_url
            ):
                raise ValueError(
                    "office-agent requires provider, model and base URL"
                )
            if self.model_provider == "openai" and not self.model_credential_file:
                raise ValueError("OpenAI requires a credential file")
        elif not self.dsn or not self.signing_key_file:
            raise ValueError(
                "Core/File roles require their own database and service key"
            )
        if self.dsn:
            parsed = urlparse(self.dsn)
            if parsed.scheme not in {
                "postgres",
                "postgresql",
            } or not parsed.path.endswith("_test"):
                raise ValueError(
                    "explicit PostgreSQL test database ending _test required"
                )
        for value in (self.core_url, self.file_url):
            parsed = urlparse(value)
            if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
                raise ValueError("P0 service endpoints must be loopback HTTP")
        if not Path(self.root).is_absolute():
            raise ValueError("absolute private role root required")
        return self


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["core", "files", "worker"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Worker: execute at most one allocation",
    )
    args = parser.parse_args()
    config = Config.model_validate_json(args.config.read_text())
    if config.role != args.role:
        parser.error("role/config mismatch")
    if config.role == "worker":
        # Imports here ensure the Worker never loads repository or psycopg.
        from .worker import Worker

        worker = Worker(
            worker_id=config.worker_id,
            core_url=config.core_url,
            file_url=config.file_url,
            token=Path(config.token_file).read_text().strip(),
            root=Path(config.root),
            runtime_identity=config.runtime_identity,
            executor=config.executor,
            model_config=(
                {
                    "provider": config.model_provider,
                    "model": config.model_id,
                    "base_url": config.model_base_url,
                    "api_key": (
                        Path(config.model_credential_file).read_text().strip()
                        if config.model_credential_file
                        else ""
                    ),
                    "shell_mode": config.shell_mode,
                }
                if config.executor == "office-agent"
                else None
            ),
            limits=config.limits,
        )
        if args.once:
            print(json.dumps(asyncio.run(worker.run_once())))
        else:
            asyncio.run(worker.serve())
        return
    key = Path(config.signing_key_file).read_text().strip()
    if config.role == "core":
        from .core import create_app
        from .repository import PostgresRepository

        app = create_app(
            PostgresRepository(config.dsn), config.file_url, key, config.limits
        )
    else:
        from .files import create_app

        app = create_app(config.dsn, Path(config.root), key)
    import uvicorn

    uvicorn.run(app, host=config.host, port=config.port, access_log=False)


if __name__ == "__main__":
    main()
