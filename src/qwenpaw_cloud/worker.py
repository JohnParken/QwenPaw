"""One Supervisor process, one slot; state transfer uses HTTP FileRefs."""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

from .client import APIClient
from .contracts import ExecutionContext, Limits, RuntimeIdentity, canonical
from .sandbox import LeaseWatchdog, PathMap, TaskLauncher


class Worker:
    def __init__(
        self,
        *,
        worker_id: str,
        core_url: str,
        file_url: str,
        token: str,
        root: Path,
        runtime_identity: RuntimeIdentity,
        executor: str = "fixture",
        limits: Limits | None = None,
    ):
        self.worker_id, self.file_url = worker_id, file_url
        self.root, self.runtime_identity, self.executor = (
            root,
            runtime_identity,
            executor,
        )
        self.limits = limits or Limits()
        from .identity import local_identity

        if runtime_identity != local_identity():
            raise ValueError("LOCAL_BUNDLE_FINGERPRINT_MISMATCH")
        self.core = APIClient(core_url, token, self.limits)
        self.launcher = TaskLauncher(self.limits)
        self.stop_event = asyncio.Event()

    async def serve(self):
        """Single-slot loop; drain/stop never admits another Attempt."""
        import signal

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop_event.set)
        while not self.stop_event.is_set() and not self.launcher.quarantined:
            await self.run_once()
            if self.stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), self.limits.heartbeat_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def run_once(self):
        await asyncio.to_thread(
            self.core.post,
            "/v1/workers/register",
            {
                "worker_id": self.worker_id,
                "runtime_identity": self.runtime_identity.model_dump(),
                "executor": self.executor,
                "slots": 1,
            },
        )
        watchdog = LeaseWatchdog(self.limits)
        assignment = await asyncio.to_thread(self.core.claim, uuid4().hex)
        if assignment.get("assignment", "present") is None:
            return {"state": "IDLE"}
        return await self.execute_assignment(assignment, watchdog)

    async def execute_assignment(
        self, assignment: dict, watchdog: LeaseWatchdog
    ):
        """Execute confirmed allocation after claim-result query."""
        context = ExecutionContext.model_validate(assignment["context"])
        if assignment["state"] != "ASSIGNED" or watchdog.expired():
            raise RuntimeError("ASSIGNMENT_NOT_EXECUTABLE")
        action = {
            "attempt_id": context.attempt_id,
            "lease_epoch": context.lease_epoch,
        }
        files = APIClient(self.file_url, assignment["file_token"], self.limits)
        paths = PathMap.create(self.root, context)
        heartbeat_stop = asyncio.Event()

        async def heartbeat():
            while not heartbeat_stop.is_set():
                sent = time.monotonic()
                try:
                    result = await asyncio.to_thread(
                        self.core.post, "/v1/heartbeat", action
                    )
                    if not watchdog.renew(sent, result["lease_seconds"]):
                        return
                except Exception:
                    # Network loss does not extend the local deadline.
                    if watchdog.expired():
                        return
                try:
                    await asyncio.wait_for(
                        heartbeat_stop.wait(), self.limits.heartbeat_seconds
                    )
                except asyncio.TimeoutError:
                    pass

        renewal = asyncio.create_task(heartbeat())
        boundary_sent = False
        try:
            input_data = json.loads(
                await asyncio.to_thread(
                    files.download, assignment["input_ref"]
                )
            )
            if (
                set(input_data) != {"marker"}
                or not isinstance(input_data["marker"], str)
                or len(input_data["marker"]) > 80
            ):
                raise ValueError("INVALID_FIXED_INPUT")
            head = assignment["checkpoint"]
            restore = None
            if head:
                if (
                    head["runtime_identity"]
                    != self.runtime_identity.model_dump()
                    or head["scope"] != context.scope.model_dump()
                ):
                    raise ValueError("CHECKPOINT_IDENTITY_MISMATCH")
                if context.session_id in head["conversations"]:
                    restore = json.loads(
                        await asyncio.to_thread(
                            files.download,
                            head["conversations"][context.session_id],
                        )
                    )
                else:
                    # New conversation, existing shared project snapshot.
                    restore = {
                        "schema": "p0.v1",
                        "scope": context.scope.model_dump(),
                        "session_id": context.session_id,
                        "runtime_identity": self.runtime_identity.model_dump(),
                        "conversation": {
                            "state": {
                                "session_id": context.session_id,
                                "summary": "",
                                "context": [],
                            }
                        },
                        "files": {},
                        "cursor": 0,
                        "next_segment": 1,
                        "budget": {"segments_used": 0},
                        "memory": None,
                        "executor": self.executor,
                    }
                # Restore only from immutable FileRefs, never
                # a predecessor directory.
                for name, ref in head["files"].items():
                    data = await asyncio.to_thread(files.download, ref)
                    restore["files"][name] = base64.b64encode(data).decode()
            index = restore["next_segment"] if restore else 1

            async def ready():
                if watchdog.expired():
                    raise RuntimeError("LEASE_DEADLINE")
                await asyncio.to_thread(self.core.post, "/v1/ready", action)

            raw = await self.launcher.run(
                paths,
                {
                    "context": context.model_dump(),
                    "executor": self.executor,
                    "root": str(paths.root),
                    "restore": restore,
                    "segment": {
                        "marker": input_data["marker"],
                        "index": index,
                    },
                    "handshake": True,
                },
                native=self.executor == "native",
                watchdog=watchdog,
                cancel=self.stop_event,
                on_ready=ready,
            )
            export = json.loads(raw)
            if (
                export["scope"] != context.scope.model_dump()
                or export["session_id"] != context.session_id
            ):
                raise ValueError("EXPORT_SCOPE_MISMATCH")
            if (
                export["runtime_identity"]
                != self.runtime_identity.model_dump()
                or export["executor"] != self.executor
            ):
                raise ValueError("EXPORT_IDENTITY_MISMATCH")
            if watchdog.expired():
                raise RuntimeError("LEASE_DEADLINE")
            conversation = await asyncio.to_thread(
                files.upload, context.scope.model_dump(), canonical(export)
            )
            file_refs = {}
            for name, encoded in export["files"].items():
                file_refs[name] = await asyncio.to_thread(
                    files.upload,
                    context.scope.model_dump(),
                    base64.b64decode(encoded, validate=True),
                )
            commit_id = uuid4().hex
            checkpoint = {
                **action,
                "commit_id": commit_id,
                "expected_revision": context.base_revision,
                "conversation_ref": conversation,
                "files": file_refs,
                "cursor": export["cursor"],
                "next_segment": export["next_segment"],
                "budget": export["budget"],
                "memory": None,
            }
            try:
                committed = await asyncio.to_thread(
                    self.core.post, "/v1/checkpoints", checkpoint
                )
            except Exception:
                committed = await asyncio.to_thread(
                    self.core.get, "/v1/checkpoints/" + commit_id
                )
                if committed["state"] != "COMMITTED":
                    raise RuntimeError("COMMIT_NOT_CONFIRMED")
            ending = "finish" if export["cursor"] == 2 else "handoff"
            result = await asyncio.to_thread(
                self.core.post, "/v1/boundary", {**action, "action": ending}
            )
            boundary_sent = True
            return {
                **result,
                "run_id": context.run_id,
                "attempt_id": context.attempt_id,
                "commit_id": commit_id,
                "revision": committed["revision"],
                "export": export,
            }
        finally:
            heartbeat_stop.set()
            await renewal
            if not boundary_sent:
                try:
                    await asyncio.to_thread(
                        self.core.post,
                        "/v1/boundary",
                        {**action, "action": "failed"},
                    )
                except Exception:
                    pass  # expired attempts are reconciled by the Core scanner
            clean = self.launcher.last_cleanup
            if clean:
                try:
                    shutil.rmtree(paths.root)
                except OSError:
                    clean = False
            if not clean:
                self.launcher.quarantined = True
            try:
                await asyncio.to_thread(
                    self.core.post,
                    "/v1/cleanup",
                    {**action, "confirmed": clean},
                )
            except Exception:
                self.launcher.quarantined = True
