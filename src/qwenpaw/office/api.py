# -*- coding: utf-8 -*-
"""Independent FastAPI surface for the trusted Office BFF."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .bundle import BundleLock, SkillBundle, load_bundle, verify_bundle
from .config import OfficeSettings
from .models import (
    CancelRequest,
    MessageCreate,
    MessageResult,
    RequestIdentity,
    SessionCreate,
    validate_identifier,
)
from .service import OfficeService, OfficeServiceError, _record_data


def _identity(request: Request) -> RequestIdentity:
    try:
        return RequestIdentity(
            tenant_id=validate_identifier(request.headers.get("X-Tenant-Id", ""), "tenant_id"),
            user_id=validate_identifier(request.headers.get("X-User-Id", ""), "user_id"),
            request_id=validate_identifier(request.headers.get("X-Request-Id", ""), "request_id"),
            trace_id=(validate_identifier(request.headers["X-Trace-Id"], "trace_id") if request.headers.get("X-Trace-Id") else None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _record_filename(record: dict[str, Any]) -> str:
    return str(_record_data(record).get("filename", "download.bin")).replace('"', "")


def _sse(event: Any) -> bytes:
    payload = event.model_dump(mode="json")
    return (
        f"event: {event.event}\n"
        f"id: {event.sequence}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    ).encode("utf-8")


def _load_default_bundle(settings: OfficeSettings) -> SkillBundle:
    try:
        return load_bundle(settings.skill_bundle_path)
    except Exception:
        validation = verify_bundle(settings.skill_bundle_path)
        # Keep the process alive so /health/ready can expose the exact
        # fail-closed diagnostics instead of turning an integrity problem into
        # a restart loop.
        return SkillBundle(
            validation.root,
            validation.skills,
            validation.lock or BundleLock(1, {}),
            validation,
        )


def create_app(
    settings: OfficeSettings | None = None,
    *,
    service: OfficeService | None = None,
) -> FastAPI:
    settings = settings or OfficeSettings.from_env()
    service = service or OfficeService(settings, _load_default_bundle(settings))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await service.initialize()
        try:
            yield
        finally:
            await service.close()

    application = FastAPI(
        title="QwenPaw Office API",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.office_service = service

    @application.exception_handler(OfficeServiceError)
    async def handle_service_error(_request: Request, exc: OfficeServiceError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @application.exception_handler(ValueError)
    async def handle_validation_error(_request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.get("/api/v1/health/live")
    async def live() -> dict[str, Any]:
        return {"live": True}

    @application.get("/api/v1/health/ready")
    async def ready() -> JSONResponse:
        report = await service.readiness()
        return JSONResponse(status_code=200 if report["ready"] else 503, content=report)

    @application.get("/api/v1/skills")
    async def skills(request: Request) -> dict[str, Any]:
        _identity(request)
        report = await service.readiness()
        return {"ready": report["ready"], "skills": report["skills"]}

    @application.post("/api/v1/sessions", status_code=201)
    async def create_session(request: Request, body: SessionCreate) -> dict[str, Any]:
        return await service.create_session(_identity(request), body.metadata)

    @application.get("/api/v1/sessions/{session_id}")
    async def get_session(request: Request, session_id: str) -> dict[str, Any]:
        return await service.get_session(_identity(request), validate_identifier(session_id, "session_id"))

    @application.delete("/api/v1/sessions/{session_id}")
    async def close_session(request: Request, session_id: str) -> dict[str, Any]:
        return await service.close_session(_identity(request), validate_identifier(session_id, "session_id"))

    @application.post("/api/v1/files", status_code=201)
    async def upload_file(
        request: Request,
        x_file_name: str = Header(alias="X-File-Name"),
        x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
        content_type: str | None = Header(default=None, alias="Content-Type"),
    ) -> dict[str, Any]:
        declared = request.headers.get("content-length")
        if declared and int(declared) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="file exceeds the upload limit")
        data = await request.body()
        try:
            return await service.upload_file(
                _identity(request),
                filename=x_file_name,
                content_type=content_type or "application/octet-stream",
                data=data,
                session_id=(validate_identifier(x_session_id, "session_id") if x_session_id else None),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.get("/api/v1/files/{file_id}")
    async def download_file(request: Request, file_id: str) -> Response:
        record, data = await service.get_file(_identity(request), validate_identifier(file_id, "file_id"))
        metadata = _record_data(record)
        return Response(
            data,
            media_type=str(metadata.get("content_type", "application/octet-stream")),
            headers={"Content-Disposition": f'attachment; filename="{_record_filename(record)}"'},
        )

    @application.delete("/api/v1/files/{file_id}")
    async def delete_file(request: Request, file_id: str) -> dict[str, Any]:
        return await service.delete_file(_identity(request), validate_identifier(file_id, "file_id"))

    @application.get("/api/v1/sessions/{session_id}/artifacts")
    async def list_artifacts(request: Request, session_id: str) -> list[dict[str, Any]]:
        return await service.list_artifacts(_identity(request), validate_identifier(session_id, "session_id"))

    @application.get("/api/v1/artifacts/{artifact_id}")
    async def download_artifact(request: Request, artifact_id: str) -> Response:
        record, data = await service.get_artifact(_identity(request), validate_identifier(artifact_id, "artifact_id"))
        metadata = _record_data(record)
        return Response(
            data,
            media_type=str(metadata.get("content_type", "application/octet-stream")),
            headers={"Content-Disposition": f'attachment; filename="{_record_filename(record)}"'},
        )

    @application.post("/api/v1/sessions/{session_id}/cancel")
    async def cancel_turn(
        request: Request,
        session_id: str,
        body: CancelRequest = Body(default_factory=CancelRequest),
    ) -> dict[str, Any]:
        identity = _identity(request)
        cancelled = await service.cancel(
            identity,
            validate_identifier(session_id, "session_id"),
            body.request_id,
        )
        if not cancelled:
            raise HTTPException(status_code=404, detail="active turn not found")
        return {"cancelled": True}

    @application.post("/api/v1/sessions/{session_id}/messages")
    async def send_message(request: Request, session_id: str, body: MessageCreate):
        identity = _identity(request)
        session_id = validate_identifier(session_id, "session_id")
        events = service.message_events(identity, session_id, body)
        if "text/event-stream" in request.headers.get("accept", ""):
            async def stream_events() -> AsyncGenerator[bytes, None]:
                try:
                    async for event in events:
                        if await request.is_disconnected():
                            raise asyncio.CancelledError
                        yield _sse(event)
                finally:
                    await events.aclose()

            return StreamingResponse(
                stream_events(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        final: MessageResult | None = None
        async for event in events:
            if event.event in {"turn.completed", "turn.failed"}:
                if event.event == "turn.completed":
                    final = MessageResult.model_validate(event.data)
                else:
                    final = MessageResult(
                        request_id=identity.request_id,
                        session_id=session_id,
                        turn_id=event.turn_id,
                        status="failed",
                        message=str(event.data.get("message", "")),
                    )
        if final is None:
            raise HTTPException(status_code=500, detail="runtime returned no terminal event")
        return final

    return application


app = create_app()

__all__ = ["app", "create_app"]
