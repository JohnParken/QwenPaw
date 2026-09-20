"""Authenticated, session-local tool service; never deployed in an Agent worker."""

from __future__ import annotations

import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from .sandbox_tools import SandboxTools

_STREAM_QUEUE_SIZE = 64
_MAX_NDJSON_LINE = 16 * 1024 * 1024


def create_sandbox_app(root: Path, token: str) -> FastAPI:
    tools = SandboxTools(root)
    serial = asyncio.Lock()
    cache = {}
    active = set()
    cache_bytes = 0
    epoch = -1
    stopped = False
    allowed = {
        "shell",
        "read_file",
        "write_file",
        "list_files",
        "browser",
        "mcp",
        "publish_file",
    }

    @asynccontextmanager
    async def lifespan(_app):
        yield
        for task in tuple(active):
            task.cancel()
        await asyncio.gather(*tuple(active), return_exceptions=True)
        await tools.close()

    app = FastAPI(
        title="QwenPaw sandbox", lifespan=lifespan, docs_url=None, redoc_url=None
    )

    def auth(value):
        if not value or not hmac.compare_digest(value, f"Bearer {token}"):
            raise HTTPException(401, "Invalid sandbox credential")

    def ndjson(event):
        line = (
            json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        if len(line) > _MAX_NDJSON_LINE:
            raise RuntimeError("Sandbox NDJSON event exceeds buffer limit")
        return line

    async def reserve(call_id, name, args, supplied_epoch, digest):
        nonlocal epoch
        acquired = False
        try:
            await serial.acquire()
            acquired = True
            # Recheck after waiting for the serial gate; stop fences queued requests too.
            if stopped or supplied_epoch < epoch:
                raise HTTPException(409, "Sandbox stopped or stale epoch")
            epoch = supplied_epoch
            if call_id in cache:
                old_digest, result = cache[call_id]
                if old_digest != digest:
                    raise HTTPException(409, "Conflicting tool call ID")
                serial.release()
                return digest, result
            if len(cache) >= 10000:
                raise HTTPException(429, "Sandbox call limit reached; recycle sandbox")
            result = {"call_id": call_id, "status": "unknown"}
            cache[call_id] = (digest, result)
            return digest, None
        except BaseException:
            if acquired:
                serial.release()
            raise

    async def execute_invocation(
        call_id,
        name,
        args,
        timeout,
        digest,
        stream_queue=None,
    ):
        nonlocal cache_bytes
        interrupted = False

        async def emit(stream, text):
            if stream_queue is not None:
                await stream_queue.put(
                    {"type": "output", "stream": stream, "text": text}
                )

        async def execute():
            method = getattr(tools, name)
            if name == "shell":
                if stream_queue is None:
                    return await method(**args, timeout=timeout)
                return await method(**args, timeout=timeout, on_output=emit)
            return await method(**args)

        task = asyncio.create_task(execute())
        invocation = asyncio.current_task()
        result = {"call_id": call_id, "status": "unknown"}
        try:
            try:
                value = await asyncio.wait_for(task, timeout)
                result = {"call_id": call_id, "status": "ok", "result": value}
            except asyncio.CancelledError:
                interrupted = True
                result = {
                    "call_id": call_id,
                    "status": "unknown",
                    "error": "interrupted",
                }
            except asyncio.TimeoutError:
                result = {"call_id": call_id, "status": "unknown", "error": "timeout"}
            except Exception as exc:
                message = str(exc)[:2000]
                result = {
                    "call_id": call_id,
                    "status": "error",
                    "error": message,
                    "result": {"status": "error", "error": message},
                }
            if stream_queue is not None:
                terminal = {"type": "result", "result": result}
                await finish_stream(stream_queue, terminal, interrupted)
            return result
        finally:
            if invocation is not None:
                active.discard(invocation)
            size = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
            if cache_bytes + size > 64 * 1024 * 1024:
                cache[call_id] = (
                    digest,
                    {
                        "call_id": call_id,
                        "status": "unknown",
                        "error": "result_cache_limit",
                    },
                )
            else:
                cache_bytes += size
                cache[call_id] = (digest, result)
            serial.release()

    async def cached_stream(result):
        yield ndjson({"type": "result", "result": result})

    async def finish_stream(stream_queue, terminal, interrupted):
        if not interrupted:
            await stream_queue.put(terminal)
            await stream_queue.put(None)
            return
        # Reserve two slots for the terminal and sentinel. A disconnected
        # consumer must never leave cancellation cleanup waiting on a full queue.
        while stream_queue.qsize() > stream_queue.maxsize - 2:
            try:
                stream_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        stream_queue.put_nowait(terminal)
        stream_queue.put_nowait(None)

    @app.post("/invoke")
    async def invoke(payload: dict, authorization: str = Header(default="")):
        auth(authorization)
        call_id, name = payload.get("call_id"), payload.get("name")
        args = payload.get("arguments", {})
        supplied_epoch = payload.get("epoch", 0)
        if (
            not isinstance(call_id, str)
            or not call_id
            or name not in allowed
            or not isinstance(args, dict)
        ):
            raise HTTPException(400, "Invalid tool invocation")
        if (
            not isinstance(supplied_epoch, int)
            or isinstance(supplied_epoch, bool)
            or supplied_epoch < 0
        ):
            raise HTTPException(400, "Invalid execution epoch")
        try:
            timeout = float(payload.get("timeout", 60))
            if not 0 < timeout <= 3600:
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, "Invalid timeout") from exc
        digest = tools.digest(name, args)
        streaming = payload.get("stream") is True
        _, cached = await reserve(call_id, name, args, supplied_epoch, digest)
        if cached is not None:
            if streaming:
                return StreamingResponse(
                    cached_stream(cached), media_type="application/x-ndjson"
                )
            return cached

        queue = asyncio.Queue(maxsize=_STREAM_QUEUE_SIZE) if streaming else None
        task = asyncio.create_task(
            execute_invocation(call_id, name, args, timeout, digest, stream_queue=queue)
        )
        active.add(task)
        if not streaming:
            return await task

        async def events():
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        return
                    yield ndjson(event)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/stop")
    async def stop(authorization: str = Header(default="")):
        nonlocal stopped
        auth(authorization)
        stopped = True
        tasks = tuple(active)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await tools.close()
        return {"stopped": True}

    @app.get("/health")
    async def health(authorization: str = Header(default="")):
        auth(authorization)
        if stopped:
            raise HTTPException(409, "Sandbox stopped")
        return {"status": "ok"}

    return app
