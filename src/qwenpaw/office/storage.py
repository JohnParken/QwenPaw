# -*- coding: utf-8 -*-
"""Tenant-scoped PostgreSQL/S3 ports with in-memory test adapters."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

TABLES = ("sessions", "turns", "messages", "files", "artifacts", "skill_executions")
ID_KEYS = {
    "sessions": "session_id",
    "turns": "turn_id",
    "messages": "message_id",
    "files": "file_id",
    "artifacts": "artifact_id",
    "skill_executions": "execution_id",
}
_SCHEMA = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class OfficeStorageError(RuntimeError):
    pass


class IdempotencyConflict(OfficeStorageError):
    pass


class RecordNotFound(OfficeStorageError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_scope(tenant_id: str, user_id: str) -> None:
    if not tenant_id or not user_id:
        raise ValueError("tenant_id and user_id are required")


def _record(table: str, tenant_id: str, user_id: str, record_id: str, data: Mapping[str, Any], *, version: int = 1, created_at: str | None = None) -> dict[str, Any]:
    payload = copy.deepcopy(dict(data))
    result = {
        "id": record_id,
        ID_KEYS[table]: record_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "version": version,
        "created_at": created_at or _now(),
        "updated_at": _now(),
        "data": payload,
    }
    result.update(payload)
    return result


class _RepositoryMixin:
    async def close(self) -> None:
        return None

    async def initialize(self) -> None:
        return None

    async def _create(self, table: str, tenant_id: str, user_id: str, record_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def _get(self, table: str, tenant_id: str, user_id: str, record_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def _list(self, table: str, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def _update(self, table: str, tenant_id: str, user_id: str, record_id: str, patch: Mapping[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError

    async def create_session(self, tenant_id: str, user_id: str, session_id: str | None = None, *, data: Mapping[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
        return await self._create("sessions", tenant_id, user_id, session_id or uuid.uuid4().hex, {**dict(data or {}), **fields})

    async def get_session(self, tenant_id: str, user_id: str, session_id: str) -> dict[str, Any] | None:
        return await self._get("sessions", tenant_id, user_id, session_id)

    async def list_sessions(self, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        return await self._list("sessions", tenant_id, user_id)

    async def update_session(self, tenant_id: str, user_id: str, session_id: str, patch: Mapping[str, Any]) -> dict[str, Any] | None:
        return await self._update("sessions", tenant_id, user_id, session_id, patch)

    async def create_turn(self, tenant_id: str, user_id: str, session_id: str, turn_id: str | None = None, *, idempotency_key: str | None = None, request_hash: str | None = None, data: Mapping[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
        payload = {**dict(data or {}), **fields, "session_id": session_id, "idempotency_key": idempotency_key, "request_hash": request_hash}
        existing = [item for item in await self._list("turns", tenant_id, user_id) if item.get("session_id") == session_id and item.get("idempotency_key") == idempotency_key and idempotency_key]
        if existing:
            if existing[0].get("request_hash") != request_hash:
                raise IdempotencyConflict("request id reused with different payload")
            # The API layer performs completed-result replay before claiming a
            # turn. Reaching this branch means another instance won the claim,
            # so it must not execute the same request a second time.
            raise OfficeStorageError("turn already exists")
        return await self._create("turns", tenant_id, user_id, turn_id or uuid.uuid4().hex, payload)

    async def get_turn(self, tenant_id: str, user_id: str, session_id: str, turn_id: str) -> dict[str, Any] | None:
        item = await self._get("turns", tenant_id, user_id, turn_id)
        return item if item and item.get("session_id") == session_id else None

    async def list_turns(self, tenant_id: str, user_id: str, session_id: str) -> list[dict[str, Any]]:
        return [item for item in await self._list("turns", tenant_id, user_id) if item.get("session_id") == session_id]

    async def update_turn(self, tenant_id: str, user_id: str, session_id: str, turn_id: str, patch: Mapping[str, Any]) -> dict[str, Any] | None:
        if await self.get_turn(tenant_id, user_id, session_id, turn_id) is None:
            return None
        return await self._update("turns", tenant_id, user_id, turn_id, patch)

    async def create_message(self, tenant_id: str, user_id: str, session_id: str, message_id: str | None = None, *, data: Mapping[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
        return await self._create("messages", tenant_id, user_id, message_id or uuid.uuid4().hex, {**dict(data or {}), **fields, "session_id": session_id})

    async def list_messages(self, tenant_id: str, user_id: str, session_id: str) -> list[dict[str, Any]]:
        return [item for item in await self._list("messages", tenant_id, user_id) if item.get("session_id") == session_id]

    async def create_file(self, tenant_id: str, user_id: str, file_id: str | None = None, *, data: Mapping[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
        return await self._create("files", tenant_id, user_id, file_id or uuid.uuid4().hex, {**dict(data or {}), **fields})

    async def get_file(self, tenant_id: str, user_id: str, file_id: str) -> dict[str, Any] | None:
        return await self._get("files", tenant_id, user_id, file_id)

    async def update_file(self, tenant_id: str, user_id: str, file_id: str, patch: Mapping[str, Any]) -> dict[str, Any] | None:
        return await self._update("files", tenant_id, user_id, file_id, patch)

    async def create_artifact(self, tenant_id: str, user_id: str, artifact_id: str | None = None, *, supersedes_artifact_id: str | None = None, data: Mapping[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
        payload = {**dict(data or {}), **fields}
        version = 1
        if supersedes_artifact_id:
            previous = await self.get_artifact(tenant_id, user_id, supersedes_artifact_id)
            if previous is None:
                raise RecordNotFound("superseded artifact not found")
            if previous.get("superseded_by"):
                raise OfficeStorageError("artifact already has a successor")
            version = int(previous.get("artifact_version", 1)) + 1
        payload.update({"supersedes_artifact_id": supersedes_artifact_id, "artifact_version": version})
        created = await self._create("artifacts", tenant_id, user_id, artifact_id or uuid.uuid4().hex, payload)
        if supersedes_artifact_id:
            await self._update("artifacts", tenant_id, user_id, supersedes_artifact_id, {"superseded_by": created["artifact_id"]})
        return created

    async def get_artifact(self, tenant_id: str, user_id: str, artifact_id: str) -> dict[str, Any] | None:
        return await self._get("artifacts", tenant_id, user_id, artifact_id)

    async def list_artifacts(self, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        return await self._list("artifacts", tenant_id, user_id)

    async def create_skill_execution(self, tenant_id: str, user_id: str, execution_id: str | None = None, *, data: Mapping[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
        return await self._create("skill_executions", tenant_id, user_id, execution_id or uuid.uuid4().hex, {**dict(data or {}), **fields})

    async def request_turn_cancel(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        request_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist a cancellation request for the active turn.

        Concrete repositories override this with an atomic implementation.
        """
        turns = await self.list_turns(tenant_id, user_id, session_id)
        candidates = [
            item for item in turns
            if item.get("status") in {"queued", "running"}
            and (request_id is None or item.get("idempotency_key") == request_id)
        ]
        if not candidates:
            return None
        turn = candidates[-1]
        return await self.update_turn(
            tenant_id,
            user_id,
            session_id,
            turn["turn_id"],
            {
                "cancel_requested": True,
                "cancel_requested_at": _now(),
                **({"status": "interrupted"} if turn.get("status") == "queued" else {}),
            },
        )

    async def turn_cancel_requested(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        turn_id: str,
    ) -> bool:
        turn = await self.get_turn(tenant_id, user_id, session_id, turn_id)
        return bool(turn and turn.get("cancel_requested"))

    async def recover_stale_turns(self, stale_before: str) -> int:
        """Mark abandoned queued/running turns interrupted at startup."""
        return 0


class MemoryRepository(_RepositoryMixin):
    mode = "memory"

    def __init__(self) -> None:
        self._tables: dict[str, dict[tuple[str, str, str], dict[str, Any]]] = {name: {} for name in TABLES}
        self._data_lock = asyncio.Lock()
        self._session_locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    async def _create(self, table: str, tenant_id: str, user_id: str, record_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        _require_scope(tenant_id, user_id)
        key = (tenant_id, user_id, record_id)
        async with self._data_lock:
            if key in self._tables[table]:
                raise OfficeStorageError(f"{table} record already exists")
            value = _record(table, tenant_id, user_id, record_id, data)
            self._tables[table][key] = value
            return copy.deepcopy(value)

    async def _get(self, table: str, tenant_id: str, user_id: str, record_id: str) -> dict[str, Any] | None:
        _require_scope(tenant_id, user_id)
        async with self._data_lock:
            return copy.deepcopy(self._tables[table].get((tenant_id, user_id, record_id)))

    async def _list(self, table: str, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        _require_scope(tenant_id, user_id)
        async with self._data_lock:
            values = [copy.deepcopy(value) for (tenant, user, _), value in self._tables[table].items() if tenant == tenant_id and user == user_id]
        return sorted(values, key=lambda item: (item["created_at"], item["id"]))

    async def _update(self, table: str, tenant_id: str, user_id: str, record_id: str, patch: Mapping[str, Any]) -> dict[str, Any] | None:
        key = (tenant_id, user_id, record_id)
        async with self._data_lock:
            current = self._tables[table].get(key)
            if current is None:
                return None
            data = {**current["data"], **copy.deepcopy(dict(patch))}
            value = _record(table, tenant_id, user_id, record_id, data, version=current["version"] + 1, created_at=current["created_at"])
            self._tables[table][key] = value
            return copy.deepcopy(value)

    async def create_artifact(self, tenant_id: str, user_id: str, artifact_id: str | None = None, *, supersedes_artifact_id: str | None = None, data: Mapping[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
        """Atomically append one immutable artifact version."""
        _require_scope(tenant_id, user_id)
        artifact_id = artifact_id or uuid.uuid4().hex
        key = (tenant_id, user_id, artifact_id)
        async with self._data_lock:
            if key in self._tables["artifacts"]:
                raise OfficeStorageError("artifact already exists")
            version = 1
            previous: dict[str, Any] | None = None
            if supersedes_artifact_id:
                previous = self._tables["artifacts"].get((tenant_id, user_id, supersedes_artifact_id))
                if previous is None:
                    raise RecordNotFound("superseded artifact not found")
                if previous.get("superseded_by"):
                    raise OfficeStorageError("artifact already has a successor")
                version = int(previous.get("artifact_version", 1)) + 1
            payload = {**dict(data or {}), **fields, "supersedes_artifact_id": supersedes_artifact_id, "artifact_version": version}
            created = _record("artifacts", tenant_id, user_id, artifact_id, payload)
            self._tables["artifacts"][key] = created
            if previous is not None:
                previous_payload = {**previous["data"], "superseded_by": artifact_id}
                self._tables["artifacts"][(tenant_id, user_id, supersedes_artifact_id)] = _record(
                    "artifacts",
                    tenant_id,
                    user_id,
                    supersedes_artifact_id,
                    previous_payload,
                    version=previous["version"] + 1,
                    created_at=previous["created_at"],
                )
            return copy.deepcopy(created)

    async def request_turn_cancel(self, tenant_id: str, user_id: str, session_id: str, request_id: str | None = None) -> dict[str, Any] | None:
        _require_scope(tenant_id, user_id)
        async with self._data_lock:
            candidates = [
                (key, value)
                for key, value in self._tables["turns"].items()
                if key[0] == tenant_id
                and key[1] == user_id
                and value.get("session_id") == session_id
                and value.get("status") in {"queued", "running"}
                and (request_id is None or value.get("idempotency_key") == request_id)
            ]
            if not candidates:
                return None
            key, current = sorted(candidates, key=lambda item: item[1]["created_at"])[-1]
            payload = {
                **current["data"],
                "cancel_requested": True,
                "cancel_requested_at": _now(),
                **({"status": "interrupted"} if current.get("status") == "queued" else {}),
            }
            updated = _record("turns", tenant_id, user_id, key[2], payload, version=current["version"] + 1, created_at=current["created_at"])
            self._tables["turns"][key] = updated
            return copy.deepcopy(updated)

    async def recover_stale_turns(self, stale_before: str) -> int:
        cutoff = datetime.fromisoformat(stale_before.replace("Z", "+00:00"))
        recovered = 0
        async with self._data_lock:
            for key, current in list(self._tables["turns"].items()):
                if current.get("status") not in {"queued", "running"}:
                    continue
                stamp = current.get("heartbeat_at") or current.get("execution_started_at") or current["created_at"]
                observed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if observed >= cutoff:
                    continue
                payload = {**current["data"], "status": "interrupted", "recovery_reason": "stale_turn", "recovered_at": _now()}
                self._tables["turns"][key] = _record("turns", key[0], key[1], key[2], payload, version=current["version"] + 1, created_at=current["created_at"])
                recovered += 1
        return recovered

    @asynccontextmanager
    async def session_lock(self, tenant_id: str, user_id: str, session_id: str, *, timeout: float | None = None) -> AsyncIterator["MemoryRepository"]:
        _require_scope(tenant_id, user_id)
        async with self._data_lock:
            lock = self._session_locks.setdefault((tenant_id, user_id, session_id), asyncio.Lock())
        if timeout is None:
            await lock.acquire()
        else:
            await asyncio.wait_for(lock.acquire(), timeout)
        try:
            yield self
        finally:
            lock.release()


class PostgresRepository(_RepositoryMixin):
    mode = "postgresql"

    def __init__(self, dsn: str, *, schema: str = "office") -> None:
        if not dsn or not _SCHEMA.fullmatch(schema):
            raise ValueError("valid PostgreSQL DSN and schema are required")
        self.dsn = dsn
        self.schema = schema

    def _connect(self):
        try:
            psycopg = importlib.import_module("psycopg")
        except ImportError as exc:  # pragma: no cover
            raise ImportError("PostgreSQL support requires qwenpaw[cloud]") from exc
        return psycopg.connect(self.dsn)

    async def initialize(self) -> None:
        sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8").replace("__OFFICE_SCHEMA__", self.schema)

        def run() -> None:
            with self._connect() as connection:
                connection.execute(sql)

        await asyncio.to_thread(run)

    def _decode(self, table: str, row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        data = row[1]
        if isinstance(data, str):
            data = json.loads(data)
        return _record(table, str(row[2]), str(row[3]), str(row[0]), data or {}, version=int(row[4]), created_at=row[5].isoformat() if hasattr(row[5], "isoformat") else str(row[5]))

    async def _create(self, table: str, tenant_id: str, user_id: str, record_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        _require_scope(tenant_id, user_id)
        query = f"INSERT INTO {self.schema}.{table} (tenant_id,user_id,record_id,data) VALUES (%s,%s,%s,%s::jsonb) RETURNING record_id,data,tenant_id,user_id,version,created_at"  # noqa: S608 - identifiers are fixed/validated
        payload = json.dumps(dict(data), default=str)

        def run():
            with self._connect() as connection:
                return connection.execute(query, (tenant_id, user_id, record_id, payload)).fetchone()

        return self._decode(table, await asyncio.to_thread(run)) or {}

    async def _get(self, table: str, tenant_id: str, user_id: str, record_id: str) -> dict[str, Any] | None:
        _require_scope(tenant_id, user_id)
        query = f"SELECT record_id,data,tenant_id,user_id,version,created_at FROM {self.schema}.{table} WHERE tenant_id=%s AND user_id=%s AND record_id=%s"  # noqa: S608

        def run():
            with self._connect() as connection:
                return connection.execute(query, (tenant_id, user_id, record_id)).fetchone()

        return self._decode(table, await asyncio.to_thread(run))

    async def _list(self, table: str, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        _require_scope(tenant_id, user_id)
        query = f"SELECT record_id,data,tenant_id,user_id,version,created_at FROM {self.schema}.{table} WHERE tenant_id=%s AND user_id=%s ORDER BY created_at,record_id"  # noqa: S608

        def run():
            with self._connect() as connection:
                return connection.execute(query, (tenant_id, user_id)).fetchall()

        return [self._decode(table, row) or {} for row in await asyncio.to_thread(run)]

    async def _update(self, table: str, tenant_id: str, user_id: str, record_id: str, patch: Mapping[str, Any]) -> dict[str, Any] | None:
        _require_scope(tenant_id, user_id)
        query = f"UPDATE {self.schema}.{table} SET data=data || %s::jsonb,version=version+1,updated_at=now() WHERE tenant_id=%s AND user_id=%s AND record_id=%s RETURNING record_id,data,tenant_id,user_id,version,created_at"  # noqa: S608

        def run():
            with self._connect() as connection:
                return connection.execute(query, (json.dumps(dict(patch), default=str), tenant_id, user_id, record_id)).fetchone()

        return self._decode(table, await asyncio.to_thread(run))

    async def create_turn(self, tenant_id: str, user_id: str, session_id: str, turn_id: str | None = None, *, idempotency_key: str | None = None, request_hash: str | None = None, data: Mapping[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
        """Create an idempotent turn without a cross-instance check/insert race."""
        _require_scope(tenant_id, user_id)
        record_id = turn_id or uuid.uuid4().hex
        payload = {**dict(data or {}), **fields, "session_id": session_id, "idempotency_key": idempotency_key, "request_hash": request_hash}
        insert = f"INSERT INTO {self.schema}.turns (tenant_id,user_id,record_id,data) VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING RETURNING record_id,data,tenant_id,user_id,version,created_at"  # noqa: S608
        lookup = f"SELECT record_id,data,tenant_id,user_id,version,created_at FROM {self.schema}.turns WHERE tenant_id=%s AND user_id=%s AND data->>'session_id'=%s AND data->>'idempotency_key'=%s"  # noqa: S608

        def run():
            with self._connect() as connection:
                row = connection.execute(
                    insert,
                    (tenant_id, user_id, record_id, json.dumps(payload, default=str)),
                ).fetchone()
                if row is not None:
                    return row, True
                if not idempotency_key:
                    raise OfficeStorageError("turn record already exists")
                return (
                    connection.execute(
                        lookup,
                        (tenant_id, user_id, session_id, idempotency_key),
                    ).fetchone(),
                    False,
                )

        row, created = await asyncio.to_thread(run)
        result = self._decode("turns", row)
        if result is None:
            raise OfficeStorageError("turn could not be created")
        if result.get("request_hash") != request_hash:
            raise IdempotencyConflict("request id reused with different payload")
        # A conflicting INSERT returns the already-claimed row. Let the API
        # layer translate it to an in-progress conflict (or replay a result
        # that completed between lookup and this check).
        if not created:
            raise OfficeStorageError("turn already exists")
        return result

    async def create_artifact(self, tenant_id: str, user_id: str, artifact_id: str | None = None, *, supersedes_artifact_id: str | None = None, data: Mapping[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
        """Append a version under a PostgreSQL row lock."""
        _require_scope(tenant_id, user_id)
        artifact_id = artifact_id or uuid.uuid4().hex

        def run():
            with self._connect() as connection:
                version = 1
                if supersedes_artifact_id:
                    previous = connection.execute(
                        f"SELECT data FROM {self.schema}.artifacts WHERE tenant_id=%s AND user_id=%s AND record_id=%s FOR UPDATE",  # noqa: S608
                        (tenant_id, user_id, supersedes_artifact_id),
                    ).fetchone()
                    if previous is None:
                        raise RecordNotFound("superseded artifact not found")
                    previous_data = previous[0]
                    if isinstance(previous_data, str):
                        previous_data = json.loads(previous_data)
                    previous_data = dict(previous_data or {})
                    if previous_data.get("superseded_by"):
                        raise OfficeStorageError("artifact already has a successor")
                    version = int(previous_data.get("artifact_version", 1)) + 1
                    previous_data["superseded_by"] = artifact_id
                    connection.execute(
                        f"UPDATE {self.schema}.artifacts SET data=%s::jsonb,version=version+1,updated_at=now() WHERE tenant_id=%s AND user_id=%s AND record_id=%s",  # noqa: S608
                        (json.dumps(previous_data, default=str), tenant_id, user_id, supersedes_artifact_id),
                    )
                payload = {**dict(data or {}), **fields, "supersedes_artifact_id": supersedes_artifact_id, "artifact_version": version}
                return connection.execute(
                    f"INSERT INTO {self.schema}.artifacts (tenant_id,user_id,record_id,data) VALUES (%s,%s,%s,%s::jsonb) RETURNING record_id,data,tenant_id,user_id,version,created_at",  # noqa: S608
                    (tenant_id, user_id, artifact_id, json.dumps(payload, default=str)),
                ).fetchone()

        return self._decode("artifacts", await asyncio.to_thread(run)) or {}

    async def request_turn_cancel(self, tenant_id: str, user_id: str, session_id: str, request_id: str | None = None) -> dict[str, Any] | None:
        _require_scope(tenant_id, user_id)
        request_filter = "AND data->>'idempotency_key'=%s" if request_id is not None else ""
        query = f"""
            WITH target AS (
                SELECT record_id FROM {self.schema}.turns
                WHERE tenant_id=%s AND user_id=%s
                  AND data->>'session_id'=%s
                  AND data->>'status' IN ('queued','running')
                  {request_filter}
                ORDER BY updated_at DESC, record_id DESC
                LIMIT 1
                FOR UPDATE
            )
            UPDATE {self.schema}.turns AS turns
            SET data=turns.data || %s::jsonb ||
                CASE WHEN turns.data->>'status'='queued'
                     THEN '{{"status":"interrupted"}}'::jsonb
                     ELSE '{{}}'::jsonb END,
                version=turns.version+1,
                updated_at=now()
            FROM target
            WHERE turns.tenant_id=%s AND turns.user_id=%s AND turns.record_id=target.record_id
            RETURNING turns.record_id,turns.data,turns.tenant_id,turns.user_id,turns.version,turns.created_at
        """  # noqa: S608
        prefix: tuple[Any, ...] = (tenant_id, user_id, session_id)
        if request_id is not None:
            prefix += (request_id,)
        params = (*prefix, json.dumps({"cancel_requested": True, "cancel_requested_at": _now()}), tenant_id, user_id)

        def run():
            with self._connect() as connection:
                return connection.execute(query, params).fetchone()

        return self._decode("turns", await asyncio.to_thread(run))

    async def recover_stale_turns(self, stale_before: str) -> int:
        query = f"""
            UPDATE {self.schema}.turns
            SET data=data || %s::jsonb, version=version+1, updated_at=now()
            WHERE data->>'status' IN ('queued','running')
              AND COALESCE(
                    NULLIF(data->>'heartbeat_at','')::timestamptz,
                    NULLIF(data->>'execution_started_at','')::timestamptz,
                    created_at
                  ) < %s::timestamptz
        """  # noqa: S608
        patch = json.dumps({"status": "interrupted", "recovery_reason": "stale_turn", "recovered_at": _now()})

        def run() -> int:
            with self._connect() as connection:
                cursor = connection.execute(query, (patch, stale_before))
                return int(cursor.rowcount or 0)

        return await asyncio.to_thread(run)

    @asynccontextmanager
    async def session_lock(self, tenant_id: str, user_id: str, session_id: str, *, timeout: float | None = None) -> AsyncIterator["PostgresRepository"]:
        _require_scope(tenant_id, user_id)
        key = f"{tenant_id}\0{user_id}\0{session_id}"
        connection = await asyncio.to_thread(self._connect)
        try:
            if timeout is not None:
                await asyncio.to_thread(connection.execute, "SET LOCAL lock_timeout = %s", (f"{int(timeout * 1000)}ms",))
            await asyncio.to_thread(connection.execute, "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key,))
            yield self
        finally:
            try:
                await asyncio.to_thread(connection.execute, "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
            finally:
                await asyncio.to_thread(connection.close)


class MemoryObjectStore:
    mode = "memory"

    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str, str], bytes] = {}
        self._latest: dict[tuple[str, str, str], str] = {}
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        return None

    async def put(self, tenant_id: str, user_id: str, key: str, data: bytes, **_kwargs: Any) -> dict[str, Any]:
        _require_scope(tenant_id, user_id)
        version = uuid.uuid4().hex
        async with self._lock:
            self._values[(tenant_id, user_id, key, version)] = bytes(data)
            self._latest[(tenant_id, user_id, key)] = version
        return {"key": key, "version_id": version, "size": len(data), "etag": hashlib.md5(data).hexdigest()}  # noqa: S324

    async def get(self, tenant_id: str, user_id: str, key: str, *, version_id: str | None = None, **_kwargs: Any) -> bytes | None:
        _require_scope(tenant_id, user_id)
        async with self._lock:
            version = version_id or self._latest.get((tenant_id, user_id, key))
            return self._values.get((tenant_id, user_id, key, version)) if version else None

    async def delete(self, tenant_id: str, user_id: str, key: str, *, version_id: str | None = None) -> None:
        _require_scope(tenant_id, user_id)
        async with self._lock:
            latest_key = (tenant_id, user_id, key)
            version = version_id or self._latest.get(latest_key)
            if version is None:
                return
            self._values.pop((tenant_id, user_id, key, version), None)
            if self._latest.get(latest_key) == version:
                remaining = [item[3] for item in self._values if item[:3] == latest_key]
                if remaining:
                    self._latest[latest_key] = remaining[-1]
                else:
                    self._latest.pop(latest_key, None)


class S3ObjectStore:
    mode = "s3"

    def __init__(self, bucket: str, *, prefix: str = "", endpoint_url: str | None = None, region_name: str | None = None, client_kwargs: Mapping[str, Any] | None = None) -> None:
        if not bucket:
            raise ValueError("S3 bucket is required")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.endpoint_url = endpoint_url
        self.region_name = region_name
        self.client_kwargs = dict(client_kwargs or {})
        self._client: Any = None

    async def _get_client(self) -> Any:
        if self._client is None:
            try:
                boto3 = importlib.import_module("boto3")
            except ImportError as exc:  # pragma: no cover
                raise ImportError("S3 support requires qwenpaw[office]") from exc
            self._client = await asyncio.to_thread(boto3.client, "s3", endpoint_url=self.endpoint_url, region_name=self.region_name, **self.client_kwargs)
        return self._client

    def _key(self, tenant_id: str, user_id: str, key: str) -> str:
        return "/".join(part for part in (self.prefix, tenant_id, user_id, key.lstrip("/")) if part)

    async def put(self, tenant_id: str, user_id: str, key: str, data: bytes, *, content_type: str | None = None, metadata: Mapping[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
        _require_scope(tenant_id, user_id)
        client = await self._get_client()
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": self._key(tenant_id, user_id, key), "Body": data, "Metadata": {str(k): str(v) for k, v in dict(metadata or {}).items()}}
        if content_type:
            params["ContentType"] = content_type
        result = await asyncio.to_thread(client.put_object, **params)
        return {"key": key, "version_id": result.get("VersionId"), "size": len(data), "etag": str(result.get("ETag", "")).strip('"')}

    async def get(self, tenant_id: str, user_id: str, key: str, *, version_id: str | None = None, **_kwargs: Any) -> bytes | None:
        _require_scope(tenant_id, user_id)
        client = await self._get_client()
        params = {"Bucket": self.bucket, "Key": self._key(tenant_id, user_id, key)}
        if version_id:
            params["VersionId"] = version_id
        try:
            response = await asyncio.to_thread(client.get_object, **params)
        except Exception as exc:
            if "404" in str(exc) or "NoSuchKey" in str(exc):
                return None
            raise
        body = response["Body"]
        try:
            return await asyncio.to_thread(body.read)
        finally:
            await asyncio.to_thread(body.close)

    async def delete(self, tenant_id: str, user_id: str, key: str, *, version_id: str | None = None) -> None:
        _require_scope(tenant_id, user_id)
        client = await self._get_client()
        params = {"Bucket": self.bucket, "Key": self._key(tenant_id, user_id, key)}
        if version_id:
            params["VersionId"] = version_id
        await asyncio.to_thread(client.delete_object, **params)

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            await asyncio.to_thread(self._client.close)
        self._client = None


__all__ = ["IdempotencyConflict", "MemoryObjectStore", "MemoryRepository", "OfficeStorageError", "PostgresRepository", "RecordNotFound", "S3ObjectStore"]
