"""Authoritative tool boundary, durable approvals and remote-only execution."""

from __future__ import annotations

import asyncio
import time

import httpx
import jsonschema

from .contracts import Conflict, ToolOutcomeUnknown


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
        spec = self.catalog.get(name)
        if spec is None:
            raise PermissionError("Tool is not in the platform catalog")
        jsonschema.validate(arguments, spec.input_schema)
        repo = self.services.repository
        record = await repo.begin_tool(ctx, call_id, name, arguments)
        if not record["created"]:
            if record["status"] == "completed":
                return record["result"]
            raise Conflict("Tool was already dispatched; result may be unknown")
        try:
            if spec.approval:
                approval = await repo.request_approval(
                    ctx, call_id, self.config.approval_timeout
                )
                await repo.append(ctx, [{"type": "approval", "approval": approval}])
                deadline = time.monotonic() + self.config.approval_timeout
                while True:
                    decision = await repo.approval(ctx, approval["id"])
                    if decision["status"] == "approved":
                        break
                    if decision["status"] != "pending" or time.monotonic() >= deadline:
                        result = {"error": "approval_denied_or_expired"}
                        await repo.end_tool(ctx, call_id, "denied", result)
                        if (
                            decision["status"] == "expired"
                            or time.monotonic() >= deadline
                        ):
                            await repo.cancel(ctx.user_id, ctx.run_id)
                            raise asyncio.CancelledError("Approval expired")
                        return result
                    await asyncio.sleep(0.5)
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
            state = (
                "unknown"
                if result.get("status") in {"unknown", "timeout"}
                else "completed"
            )
            await repo.end_tool(ctx, call_id, state, result)
            if state == "unknown":
                raise ToolOutcomeUnknown(
                    "Tool outcome unknown; explicit new run required"
                )
            return result
        except BaseException as exc:
            # Once dispatch began we cannot infer whether side effects occurred.
            try:
                await asyncio.shield(
                    repo.end_tool(
                        ctx, call_id, "unknown", {"error": "execution_interrupted"}
                    )
                )
            except Exception:
                pass  # Lease fencing or reaper now owns the record.
            if isinstance(exc, Exception):
                raise ToolOutcomeUnknown(
                    "Tool outcome unknown; explicit new run required"
                ) from exc
            raise
