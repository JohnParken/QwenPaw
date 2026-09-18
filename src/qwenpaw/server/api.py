"""BFF-only API. The browser never authenticates directly to this service."""

import asyncio
import hmac
import json
import uuid
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from .contracts import Conflict, NotFound


class SubmitRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    usrid: str = Field(min_length=1, max_length=256)
    sessionid: str = Field(min_length=1, max_length=256)
    channelid: str = Field(min_length=1, max_length=256)
    request_id: str = Field(min_length=1, max_length=256)
    message: str = Field(min_length=1, max_length=100000)
    attachments: list[str] = Field(default_factory=list, max_length=20)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool


class ImportFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_id: str


def create_api(config, repository, objects, notifier=None) -> FastAPI:
    from .event_feed import EventFeeds

    feeds = EventFeeds(repository, notifier)
    saved_definition = None
    app = FastAPI(title="QwenPaw Service API", docs_url=None, redoc_url=None)

    @app.exception_handler(NotFound)
    async def not_found(_request, _exc):
        return JSONResponse({"detail": "Resource not found"}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    async def principal(
        authorization: Annotated[str, Header()] = "",
        x_qwenpaw_user: Annotated[str, Header()] = "",
    ) -> str:
        expected = "Bearer " + config.service_token.get_secret_value()
        if not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "Invalid BFF credential")
        if not x_qwenpaw_user or len(x_qwenpaw_user) > 256:
            raise HTTPException(400, "X-QwenPaw-User is required")
        return x_qwenpaw_user

    user = Annotated[str, Depends(principal)]

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": "server"}

    @app.get("/ready")
    async def ready():
        await repository.ready()
        return {"status": "ready"}

    @app.get("/internal/metrics", response_class=PlainTextResponse)
    async def metrics(authorization: str = Header(default="")):
        if not hmac.compare_digest(
            authorization, "Bearer " + config.service_token.get_secret_value()
        ):
            raise HTTPException(401, "Invalid service credential")
        stats = await repository.statistics()
        lines = [
            f"qwenpaw_{metric}{{status={json.dumps(status)}}} {count}"
            for metric in ("runs", "tool_calls")
            for status, count in stats[metric].items()
        ]
        lines += [
            f"qwenpaw_oldest_queued_seconds {stats['oldest_queued_seconds']}",
            f"qwenpaw_active_sandboxes {stats['active_sandboxes']}",
            f"qwenpaw_queue_seconds_mean_5m {stats['queue_seconds_mean_5m']}",
        ]
        return "\n".join(lines) + "\n"

    @app.post("/v1/runs", status_code=202)
    async def submit(body: SubmitRun, usrid: user):
        nonlocal saved_definition
        if body.usrid != usrid:
            raise HTTPException(403, "BFF principal mismatch")
        for file_id in body.attachments:
            await repository.file(usrid, file_id)
        definition = config.definition()
        payload = definition.model_dump(mode="json")
        if saved_definition != payload:
            await repository.put_definition(definition.version, payload)
            saved_definition = payload
        return await repository.submit(
            usrid,
            body.channelid,
            body.sessionid,
            body.request_id,
            {"message": body.message, "attachments": body.attachments},
            definition.version,
        )

    @app.get("/v1/runs/{run_id}")
    async def get_run(run_id: str, usrid: user):
        return await repository.get_run(usrid, run_id)

    @app.post("/v1/runs/{run_id}/cancel")
    async def cancel(run_id: str, usrid: user):
        return await repository.cancel(usrid, run_id)

    @app.get("/v1/runs/{run_id}/events")
    async def events(
        run_id: str,
        request: Request,
        usrid: user,
        last_event_id: Annotated[str, Header()] = "0",
    ):
        await repository.get_run(usrid, run_id)
        try:
            cursor = int(last_event_id)
            if cursor < 0:
                raise ValueError()
        except ValueError as exc:
            raise HTTPException(400, "Invalid Last-Event-ID") from exc

        async def stream():
            position = cursor
            async with feeds.subscribe(usrid, run_id) as feed:
                while not await request.is_disconnected():
                    batch = await feed.after(position)
                    for event in batch:
                        position = event["seq"]
                        payload = dict(event["payload"])
                        stored_at = event.get("created_at")
                        if stored_at is not None:
                            payload["persisted_at"] = stored_at.isoformat()
                        yield f"id: {position}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                    if batch:
                        continue
                    if feed.status is not None:
                        yield f"event: end\ndata: {json.dumps({'status': feed.status})}\n\n"
                        break
                    async with feed.changed:
                        try:
                            await asyncio.wait_for(feed.changed.wait(), 1)
                        except asyncio.TimeoutError:
                            yield ": keepalive\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/v1/sessions")
    async def sessions(usrid: user, limit: int = 100):
        return await repository.sessions(usrid, max(1, min(limit, 100)))

    @app.get("/v1/sessions/{session_id}/messages")
    async def messages(session_id: str, usrid: user, limit: int = 100, after: int = 0):
        if after < 0:
            raise HTTPException(400, "Invalid message cursor")
        return await repository.messages(
            usrid, session_id, max(1, min(limit, 100)), after
        )

    @app.post("/v1/approvals/{approval_id}/decision")
    async def decide(approval_id: str, body: Decision, usrid: user):
        return await repository.decide(usrid, approval_id, body.approved)

    @app.post("/v1/files", status_code=201)
    async def upload(file: UploadFile, usrid: user):
        file_id = str(uuid.uuid4())
        # Object keys contain no user-provided path components.
        key = "uploads/" + file_id
        content = bytearray()
        try:
            while chunk := await file.read(65536):
                content.extend(chunk)
                if len(content) > config.max_upload_bytes:
                    raise HTTPException(413, "File too large")
            await objects.put(key, bytes(content))
            try:
                return await repository.add_file(
                    usrid,
                    file_id,
                    (file.filename or "file")[:255],
                    key,
                    len(content),
                )
            except BaseException:
                await objects.delete(key)
                raise
        finally:
            await file.close()

    @app.get("/v1/files/{file_id}")
    async def file_info(file_id: str, usrid: user):
        record = await repository.file(usrid, file_id)
        return {**record, "download_url": await objects.url(record["key"])}

    @app.delete("/v1/files/{file_id}", status_code=204)
    async def delete_file(file_id: str, usrid: user):
        record = await repository.delete_file(usrid, file_id)
        await objects.delete(record["key"])

    @app.delete("/v1/memory", status_code=204)
    async def delete_memory(usrid: user):
        await repository.delete_memories(usrid)

    @app.post("/v1/sessions/{session_id}/files")
    async def import_file(session_id: str, body: ImportFile, usrid: user):
        await repository.session(usrid, session_id)
        await repository.file(usrid, body.file_id)
        async with httpx.AsyncClient(trust_env=False, timeout=60) as client:
            response = await client.post(
                config.controller_url + f"/sessions/{session_id}/import",
                headers={
                    "Authorization": "Bearer "
                    + config.internal_token.get_secret_value()
                },
                json={"user_id": usrid, "file_id": body.file_id},
            )
            if response.status_code == 409:
                raise Conflict("Session is busy")
            response.raise_for_status()
            return response.json()

    return app
