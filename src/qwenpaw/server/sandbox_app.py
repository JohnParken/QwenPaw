"""Authenticated, session-local tool service; never deployed in an Agent worker."""

from __future__ import annotations

import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from .sandbox_tools import SandboxTools


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

    @app.post("/invoke")
    async def invoke(payload: dict, authorization: str = Header(default="")):
        nonlocal epoch, cache_bytes
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
        async with serial:
            # Recheck after waiting for the serial gate; stop fences queued requests too.
            if stopped or supplied_epoch < epoch:
                raise HTTPException(409, "Sandbox stopped or stale epoch")
            epoch = supplied_epoch
            if call_id in cache:
                old_digest, result = cache[call_id]
                if old_digest != digest:
                    raise HTTPException(409, "Conflicting tool call ID")
                return result
            if len(cache) >= 10000:
                raise HTTPException(429, "Sandbox call limit reached; recycle sandbox")
            result = {"call_id": call_id, "status": "unknown"}
            cache[call_id] = (digest, result)

            async def execute():
                method = getattr(tools, name)
                if name == "shell":
                    return await method(**args, timeout=timeout)
                return await method(**args)

            task = asyncio.create_task(execute())
            active.add(task)
            try:
                value = await asyncio.wait_for(task, timeout)
                result = {"call_id": call_id, "status": "ok", "result": value}
            except asyncio.CancelledError:
                result = {
                    "call_id": call_id,
                    "status": "unknown",
                    "error": "interrupted",
                }
            except asyncio.TimeoutError:
                result = {"call_id": call_id, "status": "unknown", "error": "timeout"}
            except Exception as exc:
                result = {
                    "call_id": call_id,
                    "status": "error",
                    "error": str(exc)[:2000],
                    "result": {"status": "error", "error": str(exc)[:2000]},
                }
            finally:
                active.discard(task)
                size = len(json.dumps(result).encode())
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
            return result

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
