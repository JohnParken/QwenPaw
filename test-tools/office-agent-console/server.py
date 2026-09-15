"""Local-only UI for exercising the real QwenPaw office Agent.

This is deliberately not a production BFF.  It binds to loopback, keeps
sessions in memory, and materializes uploads into one private Attempt.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
import secrets
import shutil
import tempfile
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from qwenpaw_cloud.office_agent import OfficeAgentBridge
from qwenpaw_cloud.sandbox import PathMap
from qwenpaw_cloud.verification import verify_artifact


MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES = 10
ALLOWED_SUFFIXES = {".docx", ".xlsx", ".pptx", ".pdf", ".csv", ".tsv", ".txt", ".md"}
WEB_ROOT = Path(__file__).with_name("web")


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


@dataclass
class LocalSession:
    id: str
    paths: PathMap
    bridge: OfficeAgentBridge
    state: dict | None = None
    messages: list[dict[str, str]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _model_config() -> dict[str, str]:
    provider = os.environ.get("OFFICE_MODEL_PROVIDER", "").strip().lower()
    model = os.environ.get("OFFICE_MODEL_ID", "").strip()
    base_url = os.environ.get("OFFICE_MODEL_BASE_URL", "").strip()
    api_key = os.environ.get("OFFICE_MODEL_API_KEY", "")
    if provider not in {"tl", "openai"} or not model or not base_url:
        raise RuntimeError(
            "Set OFFICE_MODEL_PROVIDER, OFFICE_MODEL_ID and "
            "OFFICE_MODEL_BASE_URL before starting the console"
        )
    return {"provider": provider, "model": model, "base_url": base_url, "api_key": api_key}


def _private_root() -> Path:
    configured = os.environ.get("OFFICE_TEST_ROOT")
    root = Path(configured) if configured else Path(tempfile.gettempdir()) / "qwenpaw-office-console"
    root = root.resolve()
    if not root.is_absolute() or root in {Path("/"), Path.home().resolve()}:
        raise RuntimeError("OFFICE_TEST_ROOT must be a private absolute directory")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    return root


def create_app() -> FastAPI:
    model_config = _model_config()
    shell_mode = os.environ.get("OFFICE_SHELL_MODE", "sandboxed")
    root = _private_root()
    sessions: dict[str, LocalSession] = {}
    app = FastAPI(title="QwenPaw Office Agent Console", docs_url=None, redoc_url=None)

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {"ready": True, "provider": model_config["provider"], "model": model_config["model"]}

    @app.post("/api/sessions")
    async def create_session(
        files: Annotated[list[UploadFile], File(description="Office input files")],
    ) -> dict[str, object]:
        if not files or len(files) > MAX_FILES:
            raise HTTPException(400, f"upload 1-{MAX_FILES} files")
        session_id = secrets.token_urlsafe(18)
        attempt = root / session_id
        attempt.mkdir(mode=0o700)
        for name in ("home", "input", "workspace", "output", "tmp", "state", "secrets"):
            (attempt / name).mkdir(mode=0o700)
        used: set[str] = set()
        uploaded: list[str] = []
        try:
            for index, upload in enumerate(files, 1):
                name = Path(upload.filename or f"attachment-{index}").name
                if name in {"", ".", ".."} or name in used or Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
                    raise HTTPException(400, f"invalid or duplicate filename: {name}")
                data = await upload.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise HTTPException(413, f"file too large: {name}")
                (attempt / "input" / name).write_bytes(data)
                used.add(name)
                uploaded.append(name)
            paths = PathMap(attempt)
            paths.seal_inputs()
            bridge = OfficeAgentBridge(paths, model_config, shell_mode=shell_mode)
            sessions[session_id] = LocalSession(session_id, paths, bridge)
        except Exception:
            shutil.rmtree(attempt, ignore_errors=True)
            raise
        return {"id": session_id, "files": uploaded}

    def session_or_404(session_id: str) -> LocalSession:
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "session not found or server restarted")
        return session

    def artifacts(session: LocalSession) -> list[dict[str, object]]:
        output = session.paths.root / "output"
        result: list[dict[str, object]] = []
        for path in sorted(output.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(output).as_posix()
            verification = verify_artifact(path).as_dict() if path.suffix.lower() in ALLOWED_SUFFIXES else {
                "level": "unverified", "checks": [], "limitations": ["unsupported artifact type"]
            }
            result.append({"name": relative, "size": path.stat().st_size, "verification": verification})
        return result

    @app.post("/api/sessions/{session_id}/messages")
    async def send_message(session_id: str, body: MessageRequest) -> dict[str, object]:
        session = session_or_404(session_id)
        if session.lock.locked():
            raise HTTPException(409, "Agent is already running")
        async with session.lock:
            session.messages.append({"role": "user", "text": body.message})
            try:
                result = await session.bridge.run(body.message, restored_state=session.state)
            except Exception as exc:
                raise HTTPException(500, f"Agent failed: {type(exc).__name__}: {exc}") from exc
            session.state = result["conversation"]
            text = result.get("text", "")
            session.messages.append({"role": "assistant", "text": text})
            return {"message": text, "artifacts": artifacts(session)}

    @app.get("/api/sessions/{session_id}/artifacts")
    async def list_artifacts(session_id: str) -> dict[str, object]:
        return {"artifacts": artifacts(session_or_404(session_id))}

    @app.get("/api/sessions/{session_id}/artifacts/{name:path}")
    async def download_artifact(session_id: str, name: str):
        session = session_or_404(session_id)
        output = (session.paths.root / "output").resolve(strict=True)
        candidate = (output / name).resolve(strict=True)
        if not candidate.is_relative_to(output) or not candidate.is_file() or candidate.is_symlink():
            raise HTTPException(404, "artifact not found")
        return FileResponse(candidate, filename=candidate.name)

    app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Local QwenPaw Office Agent test console")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"])
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
