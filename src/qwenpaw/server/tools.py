"""Authoritative tool boundary, durable approvals and remote-only execution."""

from __future__ import annotations

import asyncio
import time

import httpx
import jsonschema

from .contracts import Conflict, ToolOutcomeUnknown
from ..providers.tl_wire_log import log_wire


class RemoteTools:
    def __init__(self, config, repository):
        self.config = config
        self.repository = repository
        self.client = httpx.AsyncClient(
            trust_env=False,
            base_url=config.controller_url,
            headers={
                "Authorization": "Bearer " + config.internal_token.get_secret_value()
            },
            timeout=3700,
        )

    async def invoke(self, ctx, call_id, name, arguments, timeout):
        response = await self.client.post(
            "/invoke",
            json={
                "context": ctx.__dict__,
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
                "timeout": timeout,
            },
        )
        response.raise_for_status()
        return response.json()

    async def stop_session(self, session_id, run_id=None, epoch=None):
        response = await self.client.post(
            f"/sessions/{session_id}/stop",
            timeout=150,
            json={"run_id": run_id, "epoch": epoch},
        )
        response.raise_for_status()

    async def close(self):
        await self.client.aclose()


class ToolGateway:
    def __init__(self, services, definition, config):
        self.services = services
        self.definition = definition
        self.config = config
        self.catalog = {tool.name: tool for tool in definition.tools}

    async def invoke(self, ctx, call_id, name, arguments):
        log_wire(
            event="tool_dispatch",
            payload={"name": name, "arguments": arguments},
            tool_call_id=call_id,
            session_id=ctx.session_id,
        )
        spec = self.catalog.get(name)
        if spec is None:
            raise PermissionError("Tool is not in the platform catalog")
        jsonschema.validate(arguments, spec.input_schema)
        repo = self.services.repository
        record = await repo.begin_tool(ctx, call_id, name, arguments)
        if not record["created"]:
            if record["status"] in {"completed", "failed", "denied", "cancelled"}:
                return record["result"]
            raise Conflict("Tool was already dispatched; result may be unknown")
        dispatched = False
        finalized = False
        try:
            if spec.approval:
                approval = await repo.request_approval(
                    ctx, call_id, self.config.approval_timeout
                )
                deadline = time.monotonic() + self.config.approval_timeout
                while True:
                    decision = await repo.approval(ctx, approval["id"])
                    if decision["status"] == "approved":
                        break
                    if decision["status"] != "pending" or time.monotonic() >= deadline:
                        result = {"error": "approval_denied_or_expired"}
                        await repo.end_tool(ctx, call_id, "denied", result)
                        finalized = True
                        if (
                            decision["status"] == "expired"
                            or time.monotonic() >= deadline
                        ):
                            await repo.cancel(ctx.user_id, ctx.run_id)
                            raise asyncio.CancelledError("Approval expired")
                        return result
                    await asyncio.sleep(0.5)
            await repo.validate_execution(ctx)
            await repo.append(
                ctx,
                [
                    {
                        "type": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                        "status": "running",
                    }
                ],
            )
            dispatched = True
            if spec.execution == "service":
                if name == "memory_search":
                    result = {
                        "items": await self.services.memory.search(
                            ctx.user_id, arguments["query"], self.definition
                        )
                    }
                elif name == "remember":
                    await self.services.memory.remember(
                        ctx, arguments["text"], self.definition
                    )
                    result = {"saved": True}
                elif name == "knowledge_search":
                    result = {
                        "items": await self.services.memory.knowledge(
                            arguments["query"], self.definition
                        )
                    }
                else:
                    raise PermissionError("Unknown service tool")
            elif spec.execution == "sandbox":
                result = await self.services.tools.invoke(
                    ctx, call_id, name, arguments, spec.timeout
                )
            else:
                raise PermissionError("Unknown execution target")
            inner = result.get("result", {})
            failed = result.get("status") in {"error", "failed"} or (
                isinstance(inner, dict)
                and (
                    inner.get("status") in {"error", "failed"}
                    or inner.get("exit_code", 0) != 0
                )
            )
            state = (
                "unknown"
                if result.get("status") in {"unknown", "timeout"}
                else "failed" if failed else "completed"
            )
            await repo.end_tool(ctx, call_id, state, result)
            finalized = True
            log_wire(
                event="tool_result",
                level="ERROR" if state in {"failed", "unknown"} else "DEBUG",
                payload={"name": name, "state": state, "result": result},
                tool_call_id=call_id,
            )
            if state == "unknown":
                raise ToolOutcomeUnknown(
                    "Tool outcome unknown; explicit new run required"
                )
            return result
        except BaseException as exc:
            log_wire(
                event=(
                    "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
                ),
                payload={
                    "stage": "tool_gateway",
                    "name": name,
                    "exception_type": type(exc).__name__,
                    "dispatched": dispatched,
                },
                tool_call_id=call_id,
            )
            # Once dispatch began we cannot infer whether side effects occurred.
            if not finalized:
                try:
                    await asyncio.shield(
                        repo.end_tool(
                            ctx,
                            call_id,
                            (
                                "unknown"
                                if dispatched
                                else (
                                    "cancelled"
                                    if isinstance(exc, asyncio.CancelledError)
                                    else "failed"
                                )
                            ),
                            {"error": "execution_interrupted"},
                        )
                    )
                except Exception as finalize_error:
                    log_wire(
                        event="error",
                        payload={
                            "stage": "tool_finalize",
                            "exception_type": type(finalize_error).__name__,
                        },
                        tool_call_id=call_id,
                    )
                    # Lease fencing or reaper now owns the record.
            if dispatched and isinstance(exc, Exception):
                raise ToolOutcomeUnknown(
                    "Tool outcome unknown; explicit new run required"
                ) from exc
            raise
