# -*- coding: utf-8 -*-
"""Application service for request-scoped Office agent execution."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Iterable
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
import json
import mimetypes
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from .bundle import SKILL_NAMES, SkillBundle
from .config import OfficeSettings
from .models import MessageCreate, MessageResult, OfficeEvent, RequestIdentity
from .runtime import OfficeRuntimeHost
from .storage import (
    IdempotencyConflict,
    MemoryObjectStore,
    MemoryRepository,
    OfficeStorageError,
    PostgresRepository,
    S3ObjectStore,
)


class OfficeServiceError(RuntimeError):
    status_code = 500


class NotFoundError(OfficeServiceError):
    status_code = 404


class ConflictError(OfficeServiceError):
    status_code = 409


class NotReadyError(OfficeServiceError):
    status_code = 503


def _record_data(record: dict[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    for key, value in record.items():
        if key != "data":
            data.setdefault(key, value)
    return data


def _result_from_record(record: dict[str, Any]) -> MessageResult:
    data = _record_data(record)
    result = data.get("result") or {}
    if isinstance(result, MessageResult):
        return result
    return MessageResult.model_validate(result)


class OfficeService:
    """Coordinates identity scope, persistence, Runtime and artifacts."""

    def __init__(
        self,
        settings: OfficeSettings,
        bundle: SkillBundle,
        *,
        repository: Any | None = None,
        object_store: Any | None = None,
        runtime_host: Any | None = None,
    ) -> None:
        self.settings = settings
        self.bundle = bundle
        self.repository = repository or self._repository(settings)
        self.object_store = object_store or self._object_store(settings)
        registry = getattr(bundle, "skills", bundle)
        skill_directories = {
            name: skill.directory
            for name in SKILL_NAMES
            if (skill := registry.get(name) if hasattr(registry, "get") else None) is not None
            and getattr(skill, "directory", None) is not None
        }
        self.runtime_host = runtime_host or OfficeRuntimeHost(
            settings,
            skill_names=SKILL_NAMES,
            tool_factory=self._build_tools,
            repository=self.repository,
            skill_directories=skill_directories,
        )
        self._active: dict[tuple[str, str, str], tuple[str, asyncio.Task[Any]]] = {}
        self._active_lock = asyncio.Lock()
        self._published: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        self._skill_executions: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        self._mounted_artifacts: dict[tuple[str, str, str, str], dict[str, dict[str, Any]]] = {}

    @staticmethod
    def _repository(settings: OfficeSettings) -> Any:
        if settings.database_url:
            return PostgresRepository(settings.database_url, schema="office")
        return MemoryRepository()

    @staticmethod
    def _object_store(settings: OfficeSettings) -> Any:
        if settings.object_store_backend == "s3":
            kwargs: dict[str, Any] = {}
            if settings.s3_access_key:
                kwargs["aws_access_key_id"] = settings.s3_access_key
            if settings.s3_secret_key:
                kwargs["aws_secret_access_key"] = settings.s3_secret_key
            return S3ObjectStore(
                settings.s3_bucket or "",
                prefix=settings.s3_prefix,
                endpoint_url=settings.s3_endpoint_url,
                region_name=settings.s3_region,
                client_kwargs=kwargs,
            )
        return MemoryObjectStore()

    async def initialize(self) -> None:
        self.settings.work_root.mkdir(parents=True, exist_ok=True)
        initialize = getattr(self.repository, "initialize", None)
        if callable(initialize):
            await initialize()
        recover = getattr(self.repository, "recover_stale_turns", None)
        if callable(recover):
            stale_before = datetime.now(timezone.utc) - timedelta(seconds=self.settings.turn_stale_seconds)
            await recover(stale_before.isoformat())

    async def close(self) -> None:
        for resource in (self.repository, self.object_store):
            close = getattr(resource, "close", None)
            if callable(close):
                await close()

    async def readiness(self) -> dict[str, Any]:
        failures = list(self.settings.static_readiness())
        registry = getattr(self.bundle, "skills", self.bundle)
        skills: dict[str, Any] = {}
        for name in SKILL_NAMES:
            skill = registry.get(name) if hasattr(registry, "get") else None
            skills[name] = (
                skill.as_dict()
                if skill is not None
                else {"ready": False, "available": False, "errors": ["missing"]}
            )
        if not self.bundle.ready:
            failures.append("one or more mandatory skills are unavailable")
        if self.settings.production:
            bundle_paths = [self.bundle.root]
            if self.bundle.root.is_dir():
                bundle_paths.extend(self.bundle.root.rglob("*"))
            if any(path.stat().st_mode & 0o222 for path in bundle_paths if path.exists()):
                failures.append("production Skill bundle must be read-only")
            runtime_paths: list[Path] = []
            for directory in set(self.bundle.runtime_skill_directories.values()):
                runtime_paths.append(directory)
                if directory.is_dir():
                    runtime_paths.extend(directory.rglob("*"))
            if any(os.access(path, os.W_OK) for path in runtime_paths if path.exists()):
                failures.append("production runtime Skill sources must be read-only")
        if self.settings.production and getattr(self.repository, "mode", "") != "postgresql":
            failures.append("Office repository is not PostgreSQL")
        if self.settings.production and getattr(self.object_store, "mode", "") != "s3":
            failures.append("Office object store is not S3")
        return {
            "ready": not failures,
            "failures": failures,
            "providers": {
                "allowed": list(self.settings.allowed_providers),
                "default": self.settings.default_provider,
            },
            "storage": {
                "repository": getattr(self.repository, "mode", "unknown"),
                "object_store": getattr(self.object_store, "mode", "unknown"),
            },
            "skills": skills,
        }

    async def require_ready(self) -> None:
        report = await self.readiness()
        if not report["ready"]:
            raise NotReadyError("; ".join(report["failures"]))

    async def create_session(
        self,
        identity: RequestIdentity,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.repository.create_session(
            identity.tenant_id,
            identity.user_id,
            data={"status": "active", "metadata": metadata},
        )

    async def get_session(
        self,
        identity: RequestIdentity,
        session_id: str,
    ) -> dict[str, Any]:
        record = await self.repository.get_session(
            identity.tenant_id,
            identity.user_id,
            session_id,
        )
        if record is None:
            raise NotFoundError("session not found")
        return record

    async def close_session(
        self,
        identity: RequestIdentity,
        session_id: str,
    ) -> dict[str, Any]:
        async with self.repository.session_lock(
            identity.tenant_id,
            identity.user_id,
            session_id,
        ):
            await self.get_session(identity, session_id)
            record = await self.repository.update_session(
                identity.tenant_id,
                identity.user_id,
                session_id,
                {"status": "closed"},
            )
            return record or {}

    @staticmethod
    def _safe_filename(filename: str) -> str:
        candidate = filename.strip()
        if (
            not candidate
            or len(candidate) > 255
            or candidate in {".", ".."}
            or "/" in candidate
            or "\\" in candidate
            or any(ord(char) < 32 for char in candidate)
        ):
            raise ValueError("invalid file name")
        return candidate

    async def upload_file(
        self,
        identity: RequestIdentity,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        filename = self._safe_filename(filename)
        if not data:
            raise ValueError("file must not be empty")
        if len(data) > self.settings.max_upload_bytes:
            raise ValueError("file exceeds the upload limit")
        if session_id:
            await self.get_session(identity, session_id)
        file_id = uuid.uuid4().hex
        object_key = f"inputs/{file_id}/{filename}"
        stored = await self.object_store.put(
            identity.tenant_id,
            identity.user_id,
            object_key,
            data,
            content_type=content_type,
            metadata={"sha256": hashlib.sha256(data).hexdigest()},
        )
        try:
            return await self.repository.create_file(
                identity.tenant_id,
                identity.user_id,
                file_id,
                session_id=session_id,
                object_key=object_key,
                data={
                    "filename": filename,
                    "content_type": content_type,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "version_id": stored.get("version_id"),
                    "status": "active",
                },
            )
        except Exception:
            await self.object_store.delete(
                identity.tenant_id,
                identity.user_id,
                object_key,
                version_id=stored.get("version_id"),
            )
            raise

    async def get_file(
        self,
        identity: RequestIdentity,
        file_id: str,
    ) -> tuple[dict[str, Any], bytes]:
        record = await self.repository.get_file(
            identity.tenant_id,
            identity.user_id,
            file_id,
        )
        if record is None or _record_data(record).get("status") == "deleted":
            raise NotFoundError("file not found")
        data = await self.object_store.get(
            identity.tenant_id,
            identity.user_id,
            _record_data(record)["object_key"],
            version_id=_record_data(record).get("version_id"),
        )
        if data is None:
            raise NotFoundError("file object not found")
        return record, data

    async def delete_file(
        self,
        identity: RequestIdentity,
        file_id: str,
    ) -> dict[str, Any]:
        record, _ = await self.get_file(identity, file_id)
        updated = await self.repository.update_file(
            identity.tenant_id,
            identity.user_id,
            file_id,
            {"status": "deleted"},
        )
        return updated or record

    async def list_artifacts(
        self,
        identity: RequestIdentity,
        session_id: str,
    ) -> list[dict[str, Any]]:
        await self.get_session(identity, session_id)
        records = await self.repository.list_artifacts(
            identity.tenant_id,
            identity.user_id,
        )
        return [
            record
            for record in records
            if _record_data(record).get("session_id") == session_id
        ]

    async def get_artifact(
        self,
        identity: RequestIdentity,
        artifact_id: str,
    ) -> tuple[dict[str, Any], bytes]:
        record = await self.repository.get_artifact(
            identity.tenant_id,
            identity.user_id,
            artifact_id,
        )
        if record is None:
            raise NotFoundError("artifact not found")
        data = await self.object_store.get(
            identity.tenant_id,
            identity.user_id,
            _record_data(record)["object_key"],
            version_id=_record_data(record).get("version_id"),
        )
        if data is None:
            raise NotFoundError("artifact object not found")
        return record, data

    async def cancel(
        self,
        identity: RequestIdentity,
        session_id: str,
        request_id: str | None,
    ) -> bool:
        await self.get_session(identity, session_id)
        requested = await self.repository.request_turn_cancel(
            identity.tenant_id,
            identity.user_id,
            session_id,
            request_id,
        )
        if requested is None:
            return False
        key = (identity.tenant_id, identity.user_id, session_id)
        async with self._active_lock:
            active = self._active.get(key)
            if active is not None and (request_id is None or active[0] == request_id):
                active[1].cancel()
            return True

    async def _materialize_files(
        self,
        identity: RequestIdentity,
        file_ids: Iterable[str],
        input_dir: Path,
    ) -> None:
        input_dir.mkdir(parents=True, exist_ok=True)
        for file_id in file_ids:
            record, data = await self.get_file(identity, file_id)
            filename = self._safe_filename(
                str(_record_data(record).get("filename", file_id)),
            )
            target = input_dir / f"{file_id}-{filename}"
            target.write_bytes(data)
            target.chmod(0o444)

    async def _materialize_artifacts(
        self,
        identity: RequestIdentity,
        session_id: str,
        artifact_ids: Iterable[str],
        input_dir: Path,
    ) -> dict[str, dict[str, Any]]:
        """Mount prior immutable artifacts read-only for the next turn."""
        requested = tuple(dict.fromkeys(artifact_ids))
        records = await self.list_artifacts(identity, session_id)
        by_id = {str(record["artifact_id"]): record for record in records}
        if requested:
            missing = [artifact_id for artifact_id in requested if artifact_id not in by_id]
            if missing:
                raise NotFoundError("artifact not found")
            selected = [by_id[artifact_id] for artifact_id in requested]
        else:
            selected = [record for record in records if not _record_data(record).get("superseded_by")]
        selected = selected[-self.settings.max_mounted_artifacts:]
        artifact_dir = input_dir / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        mounted: dict[str, dict[str, Any]] = {}
        index: list[dict[str, Any]] = []
        for record in selected:
            data = _record_data(record)
            artifact_id = str(record["artifact_id"])
            blob = await self.object_store.get(
                identity.tenant_id,
                identity.user_id,
                str(data["object_key"]),
                version_id=data.get("version_id"),
            )
            if blob is None:
                raise NotFoundError("artifact object not found")
            filename = self._safe_filename(str(data.get("filename", artifact_id)))
            relative_path = f"input/artifacts/{artifact_id}--{filename}"
            target = artifact_dir / f"{artifact_id}--{filename}"
            target.write_bytes(blob)
            target.chmod(0o444)
            metadata = {
                "artifact_id": artifact_id,
                "path": relative_path,
                "filename": filename,
                "content_type": data.get("content_type"),
                "artifact_version": data.get("artifact_version", 1),
                "supersedes_artifact_id": data.get("supersedes_artifact_id"),
            }
            mounted[artifact_id] = metadata
            index.append(metadata)
        index_path = artifact_dir / "index.json"
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        index_path.chmod(0o444)
        return mounted

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        # Same conservative dependency-free approximation used at the API
        # boundary; AgentScope still performs provider-aware compaction.
        return max(1, (len(text) + 2) // 3)

    async def _bounded_history(
        self,
        identity: RequestIdentity,
        session: dict[str, Any],
        records: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        session_data = _record_data(session)
        summary = str(session_data.get("conversation_summary", ""))
        marker = session_data.get("summary_through_message_id")
        if marker:
            for index, record in enumerate(records):
                if record.get("message_id") == marker:
                    records = records[index + 1:]
                    break
        items = [
            {
                "id": str(record.get("message_id", "")),
                "role": str(_record_data(record).get("role", "user")),
                "content": str(_record_data(record).get("content", "")),
            }
            for record in records
        ]
        evicted: list[dict[str, str]] = []
        while items and (
            len(items) > self.settings.max_history_messages
            or self._estimate_tokens(summary + "\n" + "\n".join(item["content"] for item in items)) > self.settings.max_history_tokens
        ):
            evicted.append(items.pop(0))
        if evicted:
            addition = "\n".join(
                f"[{item['role']}] {item['content'].strip()}" for item in evicted
            )
            summary = (summary + "\n" + addition).strip()[-self.settings.summary_max_chars:]
            await self.repository.update_session(
                identity.tenant_id,
                identity.user_id,
                str(session["session_id"]),
                {
                    "conversation_summary": summary,
                    "summary_through_message_id": evicted[-1]["id"],
                },
            )
        history: list[dict[str, str]] = []
        if summary:
            history.append({"role": "system", "content": "Conversation summary:\n" + summary})
        history.extend({"role": item["role"], "content": item["content"]} for item in items)
        return history

    async def _monitor_turn(
        self,
        identity: RequestIdentity,
        session_id: str,
        turn_id: str,
        owner: asyncio.Task[Any],
    ) -> None:
        while not owner.done():
            await asyncio.sleep(self.settings.cancel_poll_seconds)
            if await self.repository.turn_cancel_requested(
                identity.tenant_id,
                identity.user_id,
                session_id,
                turn_id,
            ):
                owner.cancel()
                return
            await self.repository.update_turn(
                identity.tenant_id,
                identity.user_id,
                session_id,
                turn_id,
                {"heartbeat_at": datetime.now(timezone.utc).isoformat()},
            )

    @staticmethod
    def _final_text(runtime_payload: dict[str, Any], deltas: list[str]) -> str:
        text = "".join(deltas)
        if text:
            return text
        for output in runtime_payload.get("output", []) or []:
            if not isinstance(output, dict):
                continue
            for block in output.get("content", []) or []:
                if isinstance(block, dict) and block.get("text"):
                    text += str(block["text"])
        return text

    def _build_tools(self, ctx: Any) -> list[Any]:
        """Create request-bound tools after the immutable bundle is loaded."""
        from .tools import (
            SkillExecutionContext,
            build_tools,
            run_skill_script as execute_skill_script,
        )

        request_context = dict(
            getattr(ctx.request, "request_context", None) or {},
        )
        execution = SkillExecutionContext(
            root=Path(request_context["work_dir"]),
        )
        execution_key = (
            str(request_context["tenant_id"]),
            str(request_context["user_id"]),
            str(request_context["session_id"]),
            str(request_context["request_id"]),
        )

        def publish_artifact(
            path: str,
            title: str | None = None,
            mime_type: str | None = None,
            supersedes_artifact_id: str | None = None,
        ) -> dict[str, Any]:
            """Verify and stage an output artifact for immutable publication."""
            mounted = self._mounted_artifacts.get(execution_key, {})
            if supersedes_artifact_id is not None and supersedes_artifact_id not in mounted:
                raise ValueError("superseded artifact must be mounted in this turn")
            artifact = execution.publish(path, title, mime_type)
            pending = {
                **artifact.as_dict(),
                "supersedes_artifact_id": supersedes_artifact_id,
            }
            self._published.setdefault(execution_key, []).append(pending)
            return pending

        def run_skill_script(
            skill_id: str,
            argv: list[str],
        ) -> dict[str, Any]:
            """Run one manifest-approved script without a shell."""
            if skill_id not in self.bundle:
                raise ValueError("unknown office skill")
            skill = self.bundle[skill_id]
            scoped = SkillExecutionContext(
                root=Path(request_context["work_dir"]),
                skill_name=skill_id,
                skill_dir=skill.directory,
                manifest=skill.manifest,
            )
            execution = {
                "skill_id": skill_id,
                "argv": list(argv),
            }
            try:
                result = execute_skill_script(scoped, argv).as_dict()
            except Exception as exc:
                execution.update({
                    "status": "failed",
                    "error": type(exc).__name__,
                })
                self._skill_executions.setdefault(execution_key, []).append(execution)
                raise
            execution.update({"status": "completed", **result})
            self._skill_executions.setdefault(execution_key, []).append(execution)
            return result

        tools = build_tools(execution)
        tools["publish_artifact"] = publish_artifact
        tools["run_skill_script"] = run_skill_script
        allowed_tools = {
            tool_name
            for skill in self.bundle.values()
            for tool_name in skill.manifest.allowed_tools
        }
        return [tool for name, tool in tools.items() if name in allowed_tools]

    async def _persist_artifact(
        self,
        identity: RequestIdentity,
        session_id: str,
        turn_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        source_path = Path(str(payload["path"]))
        artifact_id = uuid.uuid4().hex
        filename = self._safe_filename(source_path.name)
        content_type = str(payload.get(
            "mime_type",
            mimetypes.guess_type(filename)[0] or "application/octet-stream",
        ))
        blob = source_path.read_bytes()
        object_key = f"artifacts/{artifact_id}/{filename}"
        stored = await self.object_store.put(
            identity.tenant_id,
            identity.user_id,
            object_key,
            blob,
            content_type=content_type,
            metadata={"sha256": hashlib.sha256(blob).hexdigest()},
        )
        try:
            return await self.repository.create_artifact(
                identity.tenant_id,
                identity.user_id,
                artifact_id,
                supersedes_artifact_id=payload.get("supersedes_artifact_id"),
                data={
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "title": payload.get("title", filename),
                    "verification": payload.get("verification"),
                    "filename": filename,
                    "content_type": content_type,
                    "size": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "object_key": object_key,
                    "version_id": stored.get("version_id"),
                    "verified": True,
                },
            )
        except Exception:
            await self.object_store.delete(
                identity.tenant_id,
                identity.user_id,
                object_key,
                version_id=stored.get("version_id"),
            )
            raise

    async def _persist_skill_execution_records(
        self,
        identity: RequestIdentity,
        session_id: str,
        turn_id: str,
        execution_key: tuple[str, str, str, str],
    ) -> None:
        for execution in self._skill_executions.pop(execution_key, []):
            await self.repository.create_skill_execution(
                identity.tenant_id,
                identity.user_id,
                data={
                    **execution,
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "request_id": identity.request_id,
                },
            )

    async def message_events(
        self,
        identity: RequestIdentity,
        session_id: str,
        request: MessageCreate,
    ) -> AsyncGenerator[OfficeEvent, None]:
        await self.require_ready()
        session = await self.get_session(identity, session_id)
        if _record_data(session).get("status") != "active":
            raise ConflictError("session is closed")

        turn_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            "/".join(
                (identity.tenant_id, identity.user_id, session_id, identity.request_id),
            ),
        ).hex
        existing = await self.repository.get_turn(
            identity.tenant_id,
            identity.user_id,
            session_id,
            turn_id,
        )
        if existing is not None:
            status = _record_data(existing).get("status")
            if status == "completed":
                result = _result_from_record(existing)
                yield OfficeEvent(
                    event="turn.completed",
                    request_id=identity.request_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    sequence=1,
                    data={**result.model_dump(), "replayed": True},
                )
                return
            raise ConflictError("request id is already running or cannot be retried")

        request_hash = hashlib.sha256(
            json.dumps(request.model_dump(), sort_keys=True).encode("utf-8"),
        ).hexdigest()
        try:
            await self.repository.create_turn(
                identity.tenant_id,
                identity.user_id,
                session_id,
                turn_id,
                idempotency_key=identity.request_id,
                request_hash=request_hash,
                data={
                    "status": "queued",
                    "request": request.model_dump(),
                },
            )
        except IdempotencyConflict as exc:
            raise ConflictError(str(exc)) from exc
        except OfficeStorageError as exc:
            concurrent = await self.repository.get_turn(
                identity.tenant_id,
                identity.user_id,
                session_id,
                turn_id,
            )
            if concurrent and concurrent.get("request_hash") == request_hash:
                status = _record_data(concurrent).get("status")
                if status == "completed":
                    result = _result_from_record(concurrent)
                    yield OfficeEvent(
                        event="turn.completed",
                        request_id=identity.request_id,
                        session_id=session_id,
                        turn_id=turn_id,
                        sequence=1,
                        data={**result.model_dump(), "replayed": True},
                    )
                    return
                raise ConflictError("request id is already running") from exc
            raise ConflictError(str(exc)) from exc

        work_dir = self.settings.work_root / identity.tenant_id / identity.user_id / turn_id
        input_dir = work_dir / "input"
        output_dir = work_dir / "output"
        key = (identity.tenant_id, identity.user_id, session_id)
        execution_key = (*key, identity.request_id)
        task = asyncio.current_task()
        sequence = 1
        runtime_payload: dict[str, Any] = {}
        deltas: list[str] = []
        monitor_task: asyncio.Task[None] | None = None
        self._published[execution_key] = []
        self._skill_executions[execution_key] = []
        self._mounted_artifacts[execution_key] = {}

        try:
            async with self.repository.session_lock(
                identity.tenant_id,
                identity.user_id,
                session_id,
            ):
                locked_session = await self.get_session(identity, session_id)
                if _record_data(locked_session).get("status") != "active":
                    raise ConflictError("session is closed")
                locked_turn = await self.repository.get_turn(
                    identity.tenant_id,
                    identity.user_id,
                    session_id,
                    turn_id,
                )
                if locked_turn is None or _record_data(locked_turn).get("status") == "interrupted":
                    raise ConflictError("turn is no longer runnable")
                now = datetime.now(timezone.utc).isoformat()
                await self.repository.update_turn(
                    identity.tenant_id,
                    identity.user_id,
                    session_id,
                    turn_id,
                    {"status": "running", "execution_started_at": now, "heartbeat_at": now},
                )
                history_records = await self.repository.list_messages(
                    identity.tenant_id,
                    identity.user_id,
                    session_id,
                )
                history = await self._bounded_history(identity, locked_session, history_records)
                await self.repository.create_message(
                    identity.tenant_id,
                    identity.user_id,
                    session_id,
                    turn_id=turn_id,
                    role="user",
                    content=request.content,
                )
                output_dir.mkdir(parents=True, exist_ok=True)
                await self._materialize_files(identity, request.file_ids, input_dir)
                mounted = await self._materialize_artifacts(
                    identity,
                    session_id,
                    request.artifact_ids,
                    input_dir,
                )
                self._mounted_artifacts[execution_key] = mounted
                if task is not None:
                    async with self._active_lock:
                        self._active[key] = (identity.request_id, task)
                    monitor_task = asyncio.create_task(
                        self._monitor_turn(identity, session_id, turn_id, task),
                        name=f"office-turn-monitor-{turn_id}",
                    )
                yield OfficeEvent(
                    event="turn.started",
                    request_id=identity.request_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    sequence=sequence,
                    data={"provider": request.provider or self.settings.default_provider},
                )
                runtime_state = _record_data(locked_session).get("runtime_state")
                # Native QwenPaw state already contains the agent context.
                # Replaying persisted API messages as well would duplicate the
                # conversation; bounded history is the state-less recovery path.
                runtime_history = [] if isinstance(runtime_state, dict) and runtime_state else history
                async for signal in self.runtime_host.stream(
                    tenant_id=identity.tenant_id,
                    session_id=session_id,
                    user_id=identity.user_id,
                    request_id=identity.request_id,
                    turn_id=turn_id,
                    content=request.content,
                    provider=request.provider or self.settings.default_provider,
                    work_dir=work_dir,
                    history=runtime_history,
                    session_state=runtime_state,
                ):
                    if signal.kind == "runtime.response":
                        runtime_payload = signal.data
                        continue
                    if signal.kind == "message.delta":
                        deltas.append(str(signal.data.get("delta", "")))
                    sequence += 1
                    yield OfficeEvent(
                        event=signal.kind,
                        request_id=identity.request_id,
                        session_id=session_id,
                        turn_id=turn_id,
                        sequence=sequence,
                        data=signal.data,
                    )

                await self._persist_skill_execution_records(
                    identity,
                    session_id,
                    turn_id,
                    execution_key,
                )
                pending = self._published.get(execution_key, [])
                artifacts = [
                    await self._persist_artifact(
                        identity,
                        session_id,
                        turn_id,
                        item,
                    )
                    for item in pending
                ]
                for artifact in artifacts:
                    sequence += 1
                    yield OfficeEvent(
                        event="artifact.created",
                        request_id=identity.request_id,
                        session_id=session_id,
                        turn_id=turn_id,
                        sequence=sequence,
                        data=artifact,
                    )
                text = self._final_text(runtime_payload, deltas)
                await self.repository.create_message(
                    identity.tenant_id,
                    identity.user_id,
                    session_id,
                    turn_id=turn_id,
                    role="assistant",
                    content=text,
                )
                result = MessageResult(
                    request_id=identity.request_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    status="completed",
                    message=text,
                    artifacts=artifacts,
                    usage=runtime_payload.get("usage", {}),
                )
                await self.repository.update_turn(
                    identity.tenant_id,
                    identity.user_id,
                    session_id,
                    turn_id,
                    {"status": "completed", "result": result.model_dump()},
                )
                sequence += 1
                yield OfficeEvent(
                    event="turn.completed",
                    request_id=identity.request_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    sequence=sequence,
                    data=result.model_dump(),
                )
        except (asyncio.CancelledError, GeneratorExit):
            await self._persist_skill_execution_records(
                identity,
                session_id,
                turn_id,
                execution_key,
            )
            await self.repository.update_turn(
                identity.tenant_id,
                identity.user_id,
                session_id,
                turn_id,
                {"status": "interrupted"},
            )
            raise
        except OfficeServiceError:
            await self._persist_skill_execution_records(
                identity,
                session_id,
                turn_id,
                execution_key,
            )
            failed_turn = await self.repository.get_turn(
                identity.tenant_id,
                identity.user_id,
                session_id,
                turn_id,
            )
            if not failed_turn or _record_data(failed_turn).get("status") != "interrupted":
                await self.repository.update_turn(
                    identity.tenant_id,
                    identity.user_id,
                    session_id,
                    turn_id,
                    {"status": "failed"},
                )
            raise
        except Exception as exc:
            await self._persist_skill_execution_records(
                identity,
                session_id,
                turn_id,
                execution_key,
            )
            await self.repository.update_turn(
                identity.tenant_id,
                identity.user_id,
                session_id,
                turn_id,
                {"status": "failed", "error": type(exc).__name__},
            )
            sequence += 1
            yield OfficeEvent(
                event="turn.failed",
                request_id=identity.request_id,
                session_id=session_id,
                turn_id=turn_id,
                sequence=sequence,
                data={"code": type(exc).__name__, "message": str(exc)},
            )
        finally:
            if monitor_task is not None:
                monitor_task.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor_task
            async with self._active_lock:
                if self._active.get(key, (None,))[0] == identity.request_id:
                    self._active.pop(key, None)
            self._published.pop(execution_key, None)
            self._skill_executions.pop(execution_key, None)
            self._mounted_artifacts.pop(execution_key, None)
            shutil.rmtree(work_dir, ignore_errors=True)


__all__ = [
    "ConflictError",
    "NotFoundError",
    "NotReadyError",
    "OfficeService",
    "OfficeServiceError",
]
