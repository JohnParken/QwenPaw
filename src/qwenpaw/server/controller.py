"""Kubernetes sandbox controller. Only this deployment has Pod privileges."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from .contracts import Conflict, ExecutionContext, NotFound


class Kubernetes:
    def __init__(self, config):
        self.config = config
        self.account = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.client = httpx.AsyncClient(
            trust_env=False,
            base_url=f"https://{host}:{port}",
            verify=str(self.account / "ca.crt"),
            timeout=30,
        )
        self.prefix = f"/api/v1/namespaces/{config.namespace}/pods"

    def token(self, session_id):
        return hmac.new(
            self.config.internal_token.get_secret_value().encode(),
            session_id.encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def name(session_id):
        return "qp-sandbox-" + uuid.UUID(session_id).hex

    def manifest(self, session_id):
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": self.name(session_id),
                "labels": {"app": "qwenpaw-sandbox"},
            },
            "spec": {
                "automountServiceAccountToken": False,
                "restartPolicy": "Never",
                "terminationGracePeriodSeconds": 10,
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 1000,
                    "runAsGroup": 1000,
                    "fsGroup": 1000,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "tools",
                        "image": self.config.sandbox_image,
                        "command": ["qwenpaw", "serve", "sandbox"],
                        "ports": [{"containerPort": 8092}],
                        "env": [
                            {
                                "name": "QWENPAW_SANDBOX_TOKEN",
                                "value": self.token(session_id),
                            },
                            {"name": "QWENPAW_SANDBOX_ROOT", "value": "/workspace"},
                            {
                                "name": "QWENPAW_SERVER_MCP_JSON",
                                "value": os.environ.get(
                                    "QWENPAW_SANDBOX_MCP_JSON", "{}"
                                ),
                            },
                            {"name": "HOME", "value": "/tmp/home"},
                        ],
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "256Mi"},
                            "limits": {
                                "cpu": "2",
                                "memory": "2Gi",
                                "ephemeral-storage": "2Gi",
                            },
                        },
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "volumeMounts": [
                            {
                                "name": "workspace",
                                "mountPath": "/workspace",
                                "subPath": session_id,
                            },
                            {"name": "tmp", "mountPath": "/tmp"},
                            {"name": "shm", "mountPath": "/dev/shm"},
                        ],
                    }
                ],
                "volumes": [
                    {
                        "name": "workspace",
                        "persistentVolumeClaim": {
                            "claimName": self.config.workspace_pvc
                        },
                    },
                    {"name": "tmp", "emptyDir": {"sizeLimit": "1Gi"}},
                    {
                        "name": "shm",
                        "emptyDir": {"medium": "Memory", "sizeLimit": "256Mi"},
                    },
                ],
            },
        }

    async def request(self, method, path, **kwargs):
        headers = {
            "Authorization": "Bearer "
            + self.account.joinpath("token").read_text().strip()
        }
        return await self.client.request(method, path, headers=headers, **kwargs)

    async def ensure(self, session_id):
        response = await self.request(
            "POST", self.prefix, json=self.manifest(session_id)
        )
        if response.status_code != 409:
            response.raise_for_status()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            response = await self.request(
                "GET", self.prefix + "/" + self.name(session_id)
            )
            response.raise_for_status()
            pod = response.json()
            if pod["status"].get("phase") in {"Failed", "Succeeded"} or pod[
                "metadata"
            ].get("deletionTimestamp"):
                raise Conflict("Sandbox requires teardown before reuse")
            ip = pod["status"].get("podIP")
            if ip:
                async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
                    try:
                        check = await client.get(
                            f"http://{ip}:8092/health",
                            headers={
                                "Authorization": "Bearer " + self.token(session_id),
                            },
                        )
                        if check.is_success:
                            return f"http://{ip}:8092"
                    except httpx.HTTPError:
                        pass
            await asyncio.sleep(0.5)
        raise TimeoutError("Sandbox cold start exceeded 120 seconds")

    async def stop(self, session_id):
        path = self.prefix + "/" + self.name(session_id)
        response = await self.request("DELETE", path, json={"gracePeriodSeconds": 10})
        if response.status_code != 404:
            response.raise_for_status()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            response = await self.request("GET", path)
            if response.status_code == 404:
                return
            response.raise_for_status()
            await asyncio.sleep(0.5)
        raise TimeoutError("Sandbox termination unconfirmed; session stays blocked")

    async def close(self):
        await self.client.aclose()


class SandboxController:
    def __init__(self, config, repository, kube, objects):
        self.config, self.repo, self.kube, self.objects = (
            config,
            repository,
            kube,
            objects,
        )

    async def initialize(self):
        await self.repo.ready()

    def root(self, session_id):
        canonical = str(uuid.UUID(session_id))
        root = self.config.workspace_root / canonical
        if root.is_symlink():
            raise Conflict("Invalid workspace directory")
        root.mkdir(parents=True, exist_ok=True)
        return root

    async def acquire(self, ctx):
        await self.repo.sandbox_acquire(ctx)

    async def invoke(self, payload):
        ctx = ExecutionContext(**payload["context"])
        # Recheck the immutable platform catalog at this boundary as well.
        from .config import AssistantDefinition

        definition = AssistantDefinition.model_validate(
            await self.repo.definition(ctx.definition_version)
        )
        spec = next(
            (item for item in definition.tools if item.name == payload["name"]), None
        )
        if spec is None or spec.execution != "sandbox":
            raise Conflict("Tool is not an allowed sandbox tool")
        import jsonschema

        jsonschema.validate(payload["arguments"], spec.input_schema)
        await self.acquire(ctx)
        try:
            self.root(ctx.session_id)
            endpoint = await self.kube.ensure(ctx.session_id)
            # Provisioning can outlast a lease. Validate again before dispatch.
            await self.repo.validate_execution(ctx)
            async with httpx.AsyncClient(
                trust_env=False, timeout=spec.timeout + 10
            ) as client:
                async with client.stream(
                    "POST",
                    endpoint + "/invoke",
                    headers={
                        "Authorization": "Bearer " + self.kube.token(ctx.session_id),
                    },
                    json={
                        "call_id": payload["call_id"],
                        "name": spec.name,
                        "arguments": payload["arguments"],
                        "timeout": spec.timeout,
                        "epoch": ctx.epoch,
                    },
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 32 * 1024 * 1024:
                            raise Conflict("Sandbox response exceeds 32 MiB")
                    result = json.loads(body)
                if spec.name == "publish_file" and result.get("status") == "ok":
                    import base64

                    artifact = result["result"]
                    content = base64.b64decode(artifact["base64"], validate=True)
                    if len(content) > self.config.max_upload_bytes:
                        raise Conflict("Artifact exceeds configured upload limit")
                    return {
                        "status": "ok",
                        "file": await self.publish(ctx, artifact["name"], content),
                    }
                content = json.dumps(result, ensure_ascii=False).encode()
                if len(content) > 65536:
                    record = await self.publish(ctx, "tool-result.json", content)
                    return {
                        "status": result.get("status", "unknown"),
                        "file": record,
                        "summary": "Large tool result stored as a private file",
                    }
                return result
        finally:
            await self.repo.sandbox_release(ctx.session_id)

    async def publish(self, ctx, name, content):
        # Object creation cannot execute code. Validate the lease again before
        # exposing the reference, and remove the object if registration fails.
        await self.repo.validate_execution(ctx)
        file_id = str(uuid.uuid4())
        key = "artifacts/" + file_id
        await self.objects.put(key, content)
        try:
            return await self.repo.add_file(
                ctx.user_id, file_id, str(name)[:255], key, len(content), ctx=ctx
            )
        except BaseException:
            await self.objects.delete(key)
            raise

    async def stop(self, session_id, expected=None):
        # Serialize teardown attempts across controllers and reapers. Keeping the
        # session lock until Pod deletion is confirmed prevents a new claimant
        # from racing a late DELETE against its replacement sandbox.
        async with self.repo.sandbox_stop_guard(session_id, expected) as allowed:
            if not allowed:
                return
            await self.kube.stop(session_id)

    async def import_file(self, session_id, payload):
        record = await self.repo.file(payload["user_id"], payload["file_id"])
        await self.repo.session(payload["user_id"], session_id)
        content = await self.objects.get(record["key"])
        context = payload.get("context")
        ctx = ExecutionContext(**context) if context else None
        async with self.repo.session_import_guard(
            payload["user_id"], session_id, ctx
        ):
            root = self.root(session_id)
            await asyncio.to_thread(self._write_attachment, root, record["id"], content)

        return {"path": "attachments/" + record["id"], "file_id": record["id"]}

    @staticmethod
    def _write_attachment(root, file_id, content):
        # Background sandbox processes may mutate paths even between runs.
        # Resolve each directory through a no-follow fd to avoid symlink races.
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                os.mkdir("attachments", mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            folder_fd = os.open(
                "attachments",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
            try:
                fd = os.open(
                    str(uuid.UUID(file_id)),
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=folder_fd,
                )
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
            finally:
                os.close(folder_fd)
        except OSError as exc:
            raise Conflict("Unsafe or unavailable attachment path") from exc
        finally:
            os.close(root_fd)

    async def sweep(self):
        for session_id in await self.repo.sandbox_idle(self.config.idle_seconds):
            await self.stop(session_id)


def create_controller_app(controller, config):
    @asynccontextmanager
    async def lifespan(_app):
        await controller.initialize()

        async def sweep():
            while True:
                try:
                    await controller.sweep()
                except Exception:
                    import logging

                    logging.getLogger(__name__).exception("Sandbox sweep failed")
                await asyncio.sleep(10)

        task = asyncio.create_task(sweep())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await controller.kube.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    def auth(value):
        if not hmac.compare_digest(
            value, "Bearer " + config.internal_token.get_secret_value()
        ):
            raise HTTPException(401, "Invalid internal credential")

    @app.exception_handler(Conflict)
    async def conflict(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(NotFound)
    async def missing(_request, _exc):
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.post("/invoke")
    async def invoke(payload: dict, authorization: str = Header(default="")):
        auth(authorization)
        return await controller.invoke(payload)

    @app.post("/sessions/{session_id}/stop")
    async def stop(
        session_id: str, payload: dict, authorization: str = Header(default="")
    ):
        auth(authorization)
        await controller.stop(session_id, payload)
        return {"stopped": True}

    @app.post("/sessions/{session_id}/import")
    async def import_file(
        session_id: str, payload: dict, authorization: str = Header(default="")
    ):
        auth(authorization)
        return await controller.import_file(session_id, payload)

    return app
