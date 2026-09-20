"""Shared state machine for TDSQL/MySQL and in-process memory tests.

Lock order: scheduler user (claim/submit only), session, run, operation.
Memory writers instead take a separate memory-user row before job/session locks.
No transaction or failed claim automatically replays a tool operation.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from ..contracts import Conflict, ExecutionContext, LeaseLost, NotFound, TERMINAL
from .schema import SCHEMA_VERSION, TABLES, statements

ACTIVE = ("running", "waiting_approval")
BUSY = ("queued", *ACTIVE)


def key(*values):
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def dump(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def exported(row):
    if row is None:
        return None
    result = {}
    for name, value in row.items():
        if name.endswith("_key") and name != "object_key":
            continue
        if name.endswith("_json"):
            result[name[:-5]] = json.loads(value) if value is not None else None
        elif name in {
            "created_at",
            "updated_at",
            "started_at",
            "finished_at",
            "expires_at",
            "decided_at",
            "lease_until",
            "touched_at",
        }:
            result[name] = (
                datetime.fromtimestamp(float(value), timezone.utc)
                if value is not None
                else None
            )
        elif name == "cancel_requested":
            result[name] = bool(value)
        else:
            result[name] = value
    if "object_key" in result:
        result["key"] = result.pop("object_key")
        result["size"] = result.pop("size_bytes")
    return result


def positive(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


class SQLRepository:
    def __init__(self, database):
        self._db = database
        self._prefix = database.prefix

    def table(self, name):
        return self._prefix + name

    async def open(self):
        await self._db.open()

    async def close(self):
        await self._db.close()

    async def migrate(self):
        ddl = statements(self._prefix, self._db.dialect)
        checksum = key(*ddl)
        # MySQL DDL commits implicitly. Every statement is idempotent; record
        # completion only AFTER all statements succeed. Never mutate applied SQL.
        async with self._db.connection(transaction=False) as c:
            suffix = (
                " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
                if c.dialect == "mysql"
                else ""
            )
            await c.execute(
                f'CREATE TABLE IF NOT EXISTS {self.table("schema_migrations")} (version VARCHAR(32) PRIMARY KEY, checksum CHAR(64) NOT NULL)'
                + suffix
            )
            old = await self._one(
                c, "schema_migrations", "version=%s", (SCHEMA_VERSION,)
            )
            if old and old["checksum"] != checksum:
                raise Conflict("Applied storage migration checksum changed")
            if old:
                return
            for sql in ddl:
                await c.execute(sql)
            await self._insert(
                c,
                "schema_migrations",
                {"version": SCHEMA_VERSION, "checksum": checksum},
                ignore=True,
            )
            old = await self._one(
                c, "schema_migrations", "version=%s", (SCHEMA_VERSION,)
            )
            if old["checksum"] != checksum:
                raise Conflict("Concurrent incompatible migration")

    async def drop_test_tables(self):
        if not self._prefix.startswith("qp_test_"):
            raise ValueError("Cleanup is restricted to qp_test_ table prefixes")
        async with self._db.connection(transaction=False) as c:
            for name in reversed([*TABLES, "schema_migrations"]):
                await c.execute(f"DROP TABLE IF EXISTS {self.table(name)}")

    async def _rows(
        self, c, table, where="1=1", args=(), *, order="", limit=None, lock=False
    ):
        sql = f"SELECT * FROM {self.table(table)} WHERE {where}"
        if order:
            sql += " ORDER BY " + order
        if limit is not None:
            sql += " LIMIT %s"
            args = (*args, limit)
        result = await c.execute(c.locked(sql) if lock else sql, args)
        return await result.fetchall()

    async def _one(self, c, table, where, args=(), *, lock=False):
        rows = await self._rows(c, table, where, args, limit=1, lock=lock)
        return rows[0] if rows else None

    async def _insert(self, c, table, values, *, ignore=False):
        fields = list(values)
        sql = f'INSERT INTO {self.table(table)} ({",".join(fields)}) VALUES ({",".join(["%s"] * len(fields))})'
        if ignore:
            sql += (
                f" ON DUPLICATE KEY UPDATE {fields[0]}={fields[0]}"
                if c.dialect == "mysql"
                else " ON CONFLICT DO NOTHING"
            )
        await c.execute(sql, tuple(values.values()))

    async def _update(self, c, table, values, where, args=()):
        return await c.execute(
            f"UPDATE {self.table(table)} SET "
            + ",".join(f"{field}=%s" for field in values)
            + " WHERE "
            + where,
            (*values.values(), *args),
        )

    async def _user(self, c, user, *, memory=False):
        if not isinstance(user, str) or not user:
            raise ValueError("User identity required")
        table = "memory_users" if memory else "users"
        await self._insert(
            c, table, {"user_key": key(user), "user_id": user}, ignore=True
        )
        row = await self._one(c, table, "user_key=%s", (key(user),), lock=True)
        if row["user_id"] != user:
            raise Conflict("Identity key collision")
        return row

    async def _session(self, c, user, ident, *, lock=False):
        row = await self._one(
            c, "sessions", "id=%s AND user_key=%s", (ident, key(user)), lock=lock
        )
        if not row or row["user_id"] != user:
            raise NotFound("Session not found")
        return row

    async def _run(self, c, user, ident, *, lock=False):
        row = await self._one(
            c, "runs", "id=%s AND user_key=%s", (ident, key(user)), lock=lock
        )
        if not row or row["user_id"] != user:
            raise NotFound("Run not found")
        return row

    async def _fenced(self, c, ctx, *, allow_cancelled=True):
        try:
            session = await self._session(c, ctx.user_id, ctx.session_id, lock=True)
            run = await self._run(c, ctx.user_id, ctx.run_id, lock=True)
        except NotFound as exc:
            raise LeaseLost("Execution identity no longer authoritative") from exc
        now = await c.now()
        if (
            run["session_id"] != ctx.session_id
            or run["channel_id"] != ctx.channel_id
            or run["definition_version"] != ctx.definition_version
            or run["epoch"] != ctx.epoch
            or run["worker_id"] != ctx.worker_id
            or run["status"] not in ACTIVE
            or run["lease_until"] is None
            or run["lease_until"] <= now
            or session["active_run_id"] != ctx.run_id
            or session["epoch"] != ctx.epoch
        ):
            raise LeaseLost("Execution lease lost")
        if not allow_cancelled and session["stop_epoch"] >= ctx.epoch:
            raise LeaseLost("Execution sandbox has been stopped")
        if run["cancel_requested"] and not allow_cancelled:
            raise Conflict("Run is cancelled")
        return run, session

    async def validate_execution(self, ctx, allow_cancelled=False):
        async with self._db.connection() as c:
            run, _ = await self._fenced(c, ctx, allow_cancelled=allow_cancelled)
            return exported(run)

    async def ready(self):
        async with self._db.connection() as c:
            row = await self._one(
                c, "schema_migrations", "version=%s", (SCHEMA_VERSION,)
            )
            if not row:
                raise RuntimeError("Run storage migrations before serving traffic")

    async def put_definition(self, version, payload):
        async with self._db.connection() as c:
            await self._insert(
                c,
                "definitions",
                {
                    "version_key": key(version),
                    "version": version,
                    "payload_json": dump(payload),
                },
                ignore=True,
            )
            row = await self._one(c, "definitions", "version_key=%s", (key(version),))
            if row["version"] != version or json.loads(row["payload_json"]) != payload:
                raise Conflict("Definition version is immutable")
        return {"version": version, "payload": json.loads(dump(payload))}

    async def definition(self, version):
        async with self._db.connection() as c:
            row = await self._one(c, "definitions", "version_key=%s", (key(version),))
            if not row or row["version"] != version:
                raise NotFound("Definition not found")
            return json.loads(row["payload_json"])

    async def _message(self, c, session, run_id, payload):
        session["next_message_seq"] += 1
        await self._insert(
            c,
            "messages",
            dict(
                id=str(uuid.uuid4()),
                session_id=session["id"],
                run_id=run_id,
                user_key=session["user_key"],
                user_id=session["user_id"],
                seq=session["next_message_seq"],
                payload_json=dump(payload),
                created_at=await c.now(),
            ),
        )
        await self._update(
            c,
            "sessions",
            {"next_message_seq": session["next_message_seq"]},
            "id=%s",
            (session["id"],),
        )

    async def _events(self, c, run, payloads):
        now = await c.now()
        for payload in payloads:
            run["next_event_seq"] += 1
            await self._insert(
                c,
                "events",
                dict(
                    run_id=run["id"],
                    seq=run["next_event_seq"],
                    payload_json=dump(payload),
                    created_at=now,
                ),
            )
        await self._update(
            c, "runs", {"next_event_seq": run["next_event_seq"]}, "id=%s", (run["id"],)
        )

    async def submit(
        self, user_id, channel_id, session_id, request_id, payload, version
    ):
        if not all(
            isinstance(x, str) and x
            for x in (user_id, channel_id, session_id, request_id, version)
        ) or not isinstance(payload, dict):
            raise ValueError("Invalid submission")
        async with self._db.connection() as c:
            await self._user(c, user_id)
            existing = await self._one(
                c,
                "runs",
                "user_key=%s AND request_key=%s",
                (key(user_id), key(request_id)),
            )
            if existing:
                session = await self._session(c, user_id, existing["session_id"])
                if (
                    existing["user_id"] != user_id
                    or existing["request_id"] != request_id
                    or existing["channel_id"] != channel_id
                    or session["external_id"] != session_id
                    or json.loads(existing["input_json"]) != payload
                ):
                    raise Conflict("Request ID reused with different input")
                return exported(existing)
            scope = key(user_id, channel_id, session_id)
            session = await self._one(
                c, "sessions", "scope_key=%s", (scope,), lock=True
            )
            now = await c.now()
            if not session:
                session = dict(
                    id=str(uuid.uuid4()),
                    user_key=key(user_id),
                    user_id=user_id,
                    channel_id=channel_id,
                    external_id=session_id,
                    scope_key=scope,
                    state_json="{}",
                    epoch=0,
                    next_run_seq=0,
                    next_message_seq=0,
                    active_run_id=None,
                    stop_epoch=0,
                    created_at=now,
                    updated_at=now,
                )
                await self._insert(c, "sessions", session)
            if (session["user_id"], session["channel_id"], session["external_id"]) != (
                user_id,
                channel_id,
                session_id,
            ):
                raise Conflict("Session key collision")
            session["next_run_seq"] += 1
            await self._update(
                c,
                "sessions",
                {"next_run_seq": session["next_run_seq"], "updated_at": now},
                "id=%s",
                (session["id"],),
            )
            run = dict(
                id=str(uuid.uuid4()),
                session_id=session["id"],
                user_key=key(user_id),
                user_id=user_id,
                channel_id=channel_id,
                request_key=key(request_id),
                request_id=request_id,
                input_json=dump(payload),
                definition_version=version,
                status="queued",
                epoch=0,
                worker_id=None,
                cancel_requested=0,
                lease_until=None,
                queue_seq=session["next_run_seq"],
                next_event_seq=0,
                created_at=now,
                updated_at=now,
                started_at=None,
                finished_at=None,
            )
            await self._insert(c, "runs", run)
            message = {"role": "user", "content": payload.get("message", payload)}
            if payload.get("attachments"):
                message["attachments"] = payload["attachments"]
            await self._message(c, session, run["id"], message)
            return exported(run)

    async def get_run(self, user_id, run_id):
        async with self._db.connection() as c:
            return exported(await self._run(c, user_id, run_id))

    async def session(self, user_id, session_id):
        async with self._db.connection() as c:
            return exported(await self._session(c, user_id, session_id))

    async def sessions(self, user_id, limit=100):
        async with self._db.connection() as c:
            return [
                exported(r)
                for r in await self._rows(
                    c,
                    "sessions",
                    "user_key=%s",
                    (key(user_id),),
                    order="updated_at DESC,id",
                    limit=max(0, limit),
                )
                if r["user_id"] == user_id
            ]

    async def messages(self, user_id, session_id, limit=100, after=0):
        if after < 0:
            raise ValueError("Invalid cursor")
        async with self._db.connection() as c:
            await self._session(c, user_id, session_id)
            return [
                exported(r)
                for r in await self._rows(
                    c,
                    "messages",
                    "session_id=%s AND seq>%s",
                    (session_id, after),
                    order="seq",
                    limit=max(0, limit),
                )
            ]

    async def events(self, user_id, run_id, after, limit=100):
        if after < 0:
            raise ValueError("Invalid cursor")
        async with self._db.connection() as c:
            await self._run(c, user_id, run_id)
            return [
                exported(r)
                for r in await self._rows(
                    c,
                    "events",
                    "run_id=%s AND seq>%s",
                    (run_id, after),
                    order="seq",
                    limit=max(0, limit),
                )
            ]

    async def claim(self, worker_id, lease_seconds, user_limit):
        positive(lease_seconds, "lease_seconds")
        positive(user_limit, "user_limit")
        if not worker_id:
            raise ValueError("Worker identity required")
        # Select candidates without retaining locks. Revalidate everything in
        # a short transaction; MariaDB 10.3 does not require SKIP LOCKED.
        async with self._db.connection() as c:
            sql = f"""SELECT r.id,r.user_id,r.session_id FROM {self.table('runs')} r
                JOIN {self.table('users')} u ON u.user_key=r.user_key
                JOIN {self.table('sessions')} s ON s.id=r.session_id
                WHERE r.status='queued' AND s.active_run_id IS NULL
                AND (SELECT COUNT(*) FROM {self.table('runs')} a WHERE a.user_key=r.user_key AND a.status IN ('running','waiting_approval')) < %s
                AND NOT EXISTS (SELECT 1 FROM {self.table('runs')} p WHERE p.session_id=r.session_id AND p.status='queued' AND p.queue_seq<r.queue_seq)
                ORDER BY COALESCE(u.last_claimed_at,0),r.created_at,r.id LIMIT 32"""
            candidates = await (await c.execute(sql, (user_limit,))).fetchall()
        for candidate in candidates:
            async with self._db.connection() as c:
                await self._user(c, candidate["user_id"])
                session = await self._session(
                    c, candidate["user_id"], candidate["session_id"], lock=True
                )
                run = await self._run(
                    c, candidate["user_id"], candidate["id"], lock=True
                )
                if session["active_run_id"] or run["status"] != "queued":
                    continue
                active = await self._rows(
                    c,
                    "runs",
                    "user_key=%s AND status IN ('running','waiting_approval')",
                    (key(candidate["user_id"]),),
                    limit=user_limit,
                )
                prior = await self._one(
                    c,
                    "runs",
                    "session_id=%s AND status='queued' AND queue_seq<%s",
                    (session["id"], run["queue_seq"]),
                )
                if len(active) >= user_limit or prior:
                    continue
                now = await c.now()
                epoch = session["epoch"] + 1
                await self._update(
                    c,
                    "sessions",
                    {"epoch": epoch, "active_run_id": run["id"], "updated_at": now},
                    "id=%s",
                    (session["id"],),
                )
                values = dict(
                    status="running",
                    epoch=epoch,
                    worker_id=worker_id,
                    lease_until=now + lease_seconds,
                    started_at=now,
                    updated_at=now,
                )
                await self._update(c, "runs", values, "id=%s", (run["id"],))
                await self._update(
                    c,
                    "users",
                    {"last_claimed_at": now},
                    "user_key=%s",
                    (session["user_key"],),
                )
                return exported({**run, **values})
        return None

    async def heartbeat(self, ctx, lease_seconds):
        positive(lease_seconds, "lease_seconds")
        async with self._db.connection() as c:
            run, _ = await self._fenced(c, ctx)
            await self._update(
                c,
                "runs",
                {"lease_until": await c.now() + lease_seconds},
                "id=%s",
                (ctx.run_id,),
            )
            return bool(run["cancel_requested"])

    async def append(self, ctx, payloads):
        if not isinstance(payloads, list) or any(
            not isinstance(p, dict) for p in payloads
        ):
            raise ValueError("Events must be dictionaries")
        async with self._db.connection() as c:
            run, _ = await self._fenced(c, ctx)
            await self._events(c, run, payloads)

    async def cancel(self, user_id, run_id):
        async with self._db.connection() as c:
            initial = await self._run(c, user_id, run_id)
            await self._session(c, user_id, initial["session_id"], lock=True)
            run = await self._run(c, user_id, run_id, lock=True)
            if run["status"] in TERMINAL:
                return exported(run)
            values = {"cancel_requested": 1, "updated_at": await c.now()}
            if run["status"] == "queued":
                values.update(status="cancelled", finished_at=await c.now())
                await self._events(
                    c, run, [{"type": "terminal", "status": "cancelled"}]
                )
            await self._update(c, "runs", values, "id=%s", (run_id,))
            return exported({**run, **values})

    async def _terminal(self, c, run, session, status):
        now = await c.now()
        await self._update(
            c,
            "tool_calls",
            {"status": "unknown", "updated_at": now},
            "run_id=%s AND status='running'",
            (run["id"],),
        )
        await self._update(
            c,
            "approvals",
            {"status": "expired", "decided_at": now},
            "run_id=%s AND status='pending'",
            (run["id"],),
        )
        await self._update(
            c,
            "runs",
            dict(status=status, lease_until=None, finished_at=now, updated_at=now),
            "id=%s",
            (run["id"],),
        )
        await self._update(
            c,
            "sessions",
            dict(active_run_id=None, updated_at=now),
            "id=%s",
            (session["id"],),
        )
        from ..timeline import compact_timeline

        journal = await self._rows(c, "events", "run_id=%s", (run["id"],), order="seq")
        timeline = compact_timeline(
            [
                {
                    **json.loads(row["payload_json"]),
                    "persisted_at": datetime.fromtimestamp(
                        row["created_at"], timezone.utc
                    ).isoformat(),
                }
                for row in journal
            ],
            run["id"],
            status,
        )
        if timeline:
            await self._message(c, session, run["id"], timeline)
        await self._events(c, run, [{"type": "terminal", "status": status}])
        if status == "completed":
            await self._insert(
                c,
                "memory_jobs",
                dict(
                    run_id=run["id"],
                    user_key=run["user_key"],
                    user_id=run["user_id"],
                    status="queued",
                    owner=None,
                    epoch=0,
                    lease_until=None,
                    attempts=0,
                    error=None,
                    created_at=now,
                ),
                ignore=True,
            )

    async def finish(self, ctx, status, state, message=None):
        if status not in TERMINAL or not isinstance(state, dict):
            raise ValueError("Invalid terminal snapshot")
        async with self._db.connection() as c:
            run, session = await self._fenced(c, ctx)
            if run["cancel_requested"] and status == "completed":
                status = "cancelled"
            uncertain = await self._one(
                c,
                "tool_calls",
                "run_id=%s AND status IN ('running','unknown')",
                (ctx.run_id,),
            )
            if uncertain and session["stop_epoch"] < ctx.epoch:
                raise Conflict(
                    "Confirm sandbox termination before releasing unfinished tools"
                )
            history = await self._rows(c, "messages", "session_id=%s", (session["id"],))
            seen = {dump(json.loads(row["payload_json"])) for row in history}
            context = state.get("state", {}).get("context", state.get("messages", []))
            outgoing = (
                [
                    m
                    for m in context
                    if isinstance(m, dict) and m.get("role") == "assistant"
                ]
                if isinstance(context, list)
                else []
            )
            if message is not None:
                outgoing.append(message)
            for msg in outgoing:
                signature = dump(msg)
                if signature not in seen:
                    await self._message(c, session, run["id"], msg)
                    seen.add(signature)
            await self._update(
                c, "sessions", {"state_json": dump(state)}, "id=%s", (session["id"],)
            )
            await self._terminal(c, run, session, status)

    async def expired(self):
        async with self._db.connection() as c:
            return [
                exported(r)
                for r in await self._rows(
                    c,
                    "runs",
                    "status IN ('running','waiting_approval') AND lease_until<=%s",
                    (await c.now(),),
                    order="lease_until",
                )
            ]

    async def interrupt(self, run_id, epoch):
        async with self._db.connection() as c:
            first = await self._one(c, "runs", "id=%s", (run_id,))
            if not first:
                raise NotFound("Run not found")
            session = await self._session(
                c, first["user_id"], first["session_id"], lock=True
            )
            run = await self._run(c, first["user_id"], run_id, lock=True)
            if run["epoch"] != epoch:
                raise LeaseLost("Execution epoch changed")
            if run["status"] in TERMINAL:
                return
            if run["status"] not in ACTIVE or run["lease_until"] > await c.now():
                raise Conflict("Only expired running leases may be interrupted")
            if session["stop_epoch"] < epoch:
                raise Conflict("Sandbox termination has not been confirmed")
            await self._terminal(c, run, session, "interrupted")

    async def begin_tool(self, ctx, call_id, name, arguments):
        async with self._db.connection() as c:
            run, _ = await self._fenced(c, ctx, allow_cancelled=False)
            old = await self._one(
                c,
                "tool_calls",
                "run_id=%s AND call_key=%s",
                (ctx.run_id, key(call_id)),
                lock=True,
            )
            if old:
                if (
                    old["call_id"] != call_id
                    or old["name"] != name
                    or json.loads(old["arguments_json"]) != arguments
                ):
                    raise Conflict("Conflicting tool call ID")
                return {"created": False, **exported(old)}
            now = await c.now()
            row = dict(
                run_id=ctx.run_id,
                call_key=key(call_id),
                call_id=call_id,
                name=name,
                arguments_json=dump(arguments),
                status="running",
                result_json=None,
                created_at=now,
                updated_at=now,
            )
            await self._insert(c, "tool_calls", row)
            await self._events(
                c,
                run,
                [
                    {
                        "type": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                        "status": "preparing",
                    }
                ],
            )
            return {"created": True, **exported(row)}

    async def end_tool(self, ctx, call_id, status, result):
        if status not in {"completed", "denied", "unknown", "failed", "cancelled"}:
            raise ValueError("Invalid tool terminal status")
        async with self._db.connection() as c:
            run, _ = await self._fenced(c, ctx)
            old = await self._one(
                c,
                "tool_calls",
                "run_id=%s AND call_key=%s",
                (ctx.run_id, key(call_id)),
                lock=True,
            )
            if not old or old["call_id"] != call_id:
                raise NotFound("Tool call not found")
            if old["status"] != "running":
                if (
                    old["status"] == status
                    and json.loads(old["result_json"] or "null") == result
                ):
                    return
                raise Conflict("Tool call already finalized")
            await self._update(
                c,
                "tool_calls",
                {
                    "status": status,
                    "result_json": dump(result),
                    "updated_at": await c.now(),
                },
                "run_id=%s AND call_key=%s",
                (ctx.run_id, key(call_id)),
            )

            await self._events(
                c,
                run,
                [
                    {
                        "type": "tool",
                        "tool_call_id": call_id,
                        "name": old["name"],
                        "arguments": json.loads(old["arguments_json"]),
                        "status": status,
                        "output": result,
                    }
                ],
            )

    @staticmethod
    def _approval(row):
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "call_id": row["call_id"],
            "status": row["status"],
            "expires_at": datetime.fromtimestamp(
                float(row["expires_at"]), timezone.utc
            ).isoformat(),
        }

    async def request_approval(self, ctx, call_id, timeout):
        positive(timeout, "timeout")
        async with self._db.connection() as c:
            run, _ = await self._fenced(c, ctx, allow_cancelled=False)
            tool = await self._one(
                c, "tool_calls", "run_id=%s AND call_key=%s", (ctx.run_id, key(call_id))
            )
            if not tool or tool["call_id"] != call_id or tool["status"] != "running":
                raise Conflict("Tool call is not pending")
            row = await self._one(
                c, "approvals", "run_id=%s AND call_key=%s", (ctx.run_id, key(call_id))
            )
            if row:
                return self._approval(row)
            now = await c.now()
            row = dict(
                id=str(uuid.uuid4()),
                run_id=ctx.run_id,
                user_key=key(ctx.user_id),
                call_key=key(call_id),
                call_id=call_id,
                status="pending",
                expires_at=now + timeout,
                created_at=now,
                decided_at=None,
            )
            await self._insert(c, "approvals", row)
            await self._update(
                c, "runs", {"status": "waiting_approval"}, "id=%s", (ctx.run_id,)
            )
            details = {
                "tool_call_id": call_id,
                "name": tool["name"],
                "arguments": json.loads(tool["arguments_json"]),
            }
            await self._events(
                c,
                run,
                [
                    {"type": "tool", **details, "status": "awaiting_approval"},
                    {
                        "type": "approval",
                        **details,
                        "approval": {**self._approval(row), **details},
                    },
                ],
            )
            return self._approval(row)

    async def approval(self, ctx, approval_id):
        async with self._db.connection() as c:
            await self._fenced(c, ctx)
            row = await self._one(
                c,
                "approvals",
                "id=%s AND run_id=%s AND user_key=%s",
                (approval_id, ctx.run_id, key(ctx.user_id)),
                lock=True,
            )
            if not row:
                raise NotFound("Approval not found")
            if row["status"] == "pending" and row["expires_at"] <= await c.now():
                row["status"] = "expired"
                await self._update(
                    c, "approvals", {"status": "expired"}, "id=%s", (approval_id,)
                )
            if row["status"] != "pending":
                await self._update(
                    c, "runs", {"status": "running"}, "id=%s", (ctx.run_id,)
                )
            return self._approval(row)

    async def decide(self, user_id, approval_id, approved):
        if not isinstance(approved, bool):
            raise ValueError("Decision must be boolean")
        async with self._db.connection() as c:
            first = await self._one(
                c, "approvals", "id=%s AND user_key=%s", (approval_id, key(user_id))
            )
            if not first:
                raise NotFound("Approval not found")
            initial = await self._run(c, user_id, first["run_id"])
            await self._session(c, user_id, initial["session_id"], lock=True)
            run = await self._run(c, user_id, initial["id"], lock=True)
            row = await self._one(c, "approvals", "id=%s", (approval_id,), lock=True)
            if row["status"] == "pending":
                now = await c.now()
                valid = (
                    run["status"] in ACTIVE
                    and not run["cancel_requested"]
                    and run["lease_until"] > now
                    and row["expires_at"] > now
                )
                row["status"] = (
                    ("approved" if approved else "denied") if valid else "expired"
                )
                row["decided_at"] = now
                await self._update(
                    c,
                    "approvals",
                    {"status": row["status"], "decided_at": now},
                    "id=%s",
                    (approval_id,),
                )
                await self._events(
                    c,
                    run,
                    [
                        {
                            "type": "approval",
                            "tool_call_id": row["call_id"],
                            "approval": self._approval(row),
                        }
                    ],
                )
            return self._approval(row)

    async def add_file(self, user_id, file_id, name, key, size, ctx=None):
        # Alias imported hash helper because API's stable parameter name is key.
        hash_key = globals()["key"]
        if not user_id or not file_id or not name or not key or size < 0:
            raise ValueError("Invalid file metadata")
        async with self._db.connection() as c:
            if ctx:
                if ctx.user_id != user_id:
                    raise NotFound("File not found")
                await self._fenced(c, ctx, allow_cancelled=False)
            existing = await self._one(
                c,
                "files",
                "user_key=%s AND file_key=%s",
                (hash_key(user_id), hash_key(file_id)),
                lock=True,
            )
            if existing:
                if (
                    existing["user_id"],
                    existing["file_id"],
                    existing["name"],
                    existing["object_key"],
                    existing["size_bytes"],
                ) != (user_id, file_id, name, key, size):
                    raise Conflict("Conflicting file ID")
                return exported(existing)
            row = dict(
                id=str(uuid.uuid4()),
                user_key=hash_key(user_id),
                user_id=user_id,
                file_key=hash_key(file_id),
                file_id=file_id,
                name=name,
                object_key=key,
                size_bytes=size,
                created_at=await c.now(),
            )
            await self._insert(c, "files", row)
            return exported(row)

    async def _file(self, c, user_id, file_id, lock=False):
        row = await self._one(
            c,
            "files",
            "user_key=%s AND (id=%s OR file_key=%s)",
            (key(user_id), file_id, key(file_id)),
            lock=lock,
        )
        if (
            not row
            or row["user_id"] != user_id
            or (row["id"] != file_id and row["file_id"] != file_id)
        ):
            raise NotFound("File not found")
        return row

    async def file(self, user_id, file_id):
        async with self._db.connection() as c:
            return exported(await self._file(c, user_id, file_id))

    async def delete_file(self, user_id, file_id):
        async with self._db.connection() as c:
            row = await self._file(c, user_id, file_id, True)
            await c.execute(
                f'DELETE FROM {self.table("files")} WHERE id=%s', (row["id"],)
            )
            return exported(row)

    async def prune_events(self, days=7):
        if days < 0:
            raise ValueError("Invalid event retention")
        async with self._db.connection() as c:
            await c.execute(
                f'DELETE FROM {self.table("events")} WHERE created_at<%s',
                (await c.now() - days * 86400,),
            )

    async def memories(self, user_id, query):
        async with self._db.connection() as c:
            # Escape LIKE wildcards: user search text is literal, not SQL syntax.
            term = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            rows = await self._rows(
                c,
                "memories",
                "user_key=%s AND LOWER(text) LIKE %s ESCAPE '!'",
                (key(user_id), "%" + term.lower() + "%"),
                order="created_at DESC,id",
                limit=100,
            )
            return [exported(r) for r in rows if r["user_id"] == user_id]

    async def _remember(self, c, user_id, session_id, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise ValueError("Memory must contain 1-10000 characters")
        existing = await self._one(
            c, "memories", "user_key=%s AND text_key=%s", (key(user_id), key(text))
        )
        if existing:
            if existing["user_id"] != user_id or existing["text"] != text:
                raise Conflict("Memory key collision")
            return
        await self._insert(
            c,
            "memories",
            dict(
                id=str(uuid.uuid4()),
                user_key=key(user_id),
                user_id=user_id,
                session_id=session_id,
                text=text,
                text_key=key(text),
                created_at=await c.now(),
            ),
        )

    async def remember(self, ctx, text):
        async with self._db.connection() as c:
            await self._user(c, ctx.user_id, memory=True)
            await self._fenced(c, ctx, allow_cancelled=False)
            await self._remember(c, ctx.user_id, ctx.session_id, text)

    async def delete_memories(self, user_id):
        async with self._db.connection() as c:
            await self._user(c, user_id, memory=True)
            await self._update(
                c,
                "memory_jobs",
                {"status": "cancelled"},
                "user_key=%s AND status IN ('running','queued')",
                (key(user_id),),
            )
            await c.execute(
                f'DELETE FROM {self.table("memory_vectors")} WHERE user_key=%s',
                (key(user_id),),
            )
            await c.execute(
                f'DELETE FROM {self.table("memories")} WHERE user_key=%s',
                (key(user_id),),
            )

    async def claim_memory(self, owner, lease_seconds=300):
        positive(lease_seconds, "lease_seconds")
        async with self._db.connection() as c:
            now = await c.now()
            candidates = await (
                await c.execute(
                    f"""SELECT j.* FROM {self.table('memory_jobs')} j
                WHERE (j.status='queued' OR (j.status='running' AND j.lease_until<=%s))
                AND NOT EXISTS (SELECT 1 FROM {self.table('memory_jobs')} other
                    WHERE other.user_key=j.user_key AND other.status='running' AND other.lease_until>%s)
                ORDER BY j.created_at,j.run_id LIMIT 32""",
                    (now, now),
                )
            ).fetchall()
        for candidate in candidates:
            async with self._db.connection() as c:
                await self._user(c, candidate["user_id"], memory=True)
                job = await self._one(
                    c, "memory_jobs", "run_id=%s", (candidate["run_id"],), lock=True
                )
                now = await c.now()
                if job["status"] not in {"queued", "running"} or (
                    job["status"] == "running" and job["lease_until"] > now
                ):
                    continue
                if job["attempts"] >= 3:
                    await self._update(
                        c,
                        "memory_jobs",
                        {"status": "failed"},
                        "run_id=%s",
                        (job["run_id"],),
                    )
                    continue
                other = await self._one(
                    c,
                    "memory_jobs",
                    "user_key=%s AND status='running' AND run_id<>%s AND lease_until>%s",
                    (job["user_key"], job["run_id"], now),
                )
                if other:
                    continue
                # Expired jobs for this user can be reclaimed later, but they
                # must not count as a concurrent active memory writer.
                await self._update(
                    c,
                    "memory_jobs",
                    {"status": "queued"},
                    "user_key=%s AND status='running' AND lease_until<=%s",
                    (job["user_key"], now),
                )
                values = dict(
                    status="running",
                    owner=owner,
                    epoch=job["epoch"] + 1,
                    lease_until=now + lease_seconds,
                    attempts=job["attempts"] + 1,
                )
                await self._update(
                    c, "memory_jobs", values, "run_id=%s", (job["run_id"],)
                )
                return exported({**job, **values})
        return None

    async def _memory_job(self, c, job):
        await self._user(c, job["user_id"], memory=True)
        row = await self._one(
            c, "memory_jobs", "run_id=%s", (job["run_id"],), lock=True
        )
        if (
            not row
            or row["user_id"] != job["user_id"]
            or row["status"] != "running"
            or row["owner"] != job["owner"]
            or row["epoch"] != job["epoch"]
            or row["lease_until"] <= await c.now()
        ):
            return None
        return row

    async def merge_memory(self, job, facts):
        async with self._db.connection() as c:
            row = await self._memory_job(c, job)
            if not row:
                return False
            run = await self._run(c, job["user_id"], job["run_id"])
            for fact in facts[:10]:
                if fact.strip():
                    await self._remember(
                        c, job["user_id"], run["session_id"], fact[:10000]
                    )
            return True

    async def complete_memory(self, job):
        async with self._db.connection() as c:
            if not await self._memory_job(c, job):
                return False
            await self._update(
                c,
                "memory_jobs",
                {"status": "completed", "lease_until": None},
                "run_id=%s",
                (job["run_id"],),
            )
            return True

    async def fail_memory(self, job, error):
        async with self._db.connection() as c:
            row = await self._memory_job(c, job)
            if row:
                await self._update(
                    c,
                    "memory_jobs",
                    {
                        "status": "failed" if row["attempts"] >= 3 else "queued",
                        "error": str(error)[:1000],
                        "lease_until": None,
                    },
                    "run_id=%s",
                    (job["run_id"],),
                )

    async def ensure_vector_support(self):
        await self.ready()

    @staticmethod
    def _vector(vector):
        if (
            not isinstance(vector, list)
            or not vector
            or len(vector) > 16384
            or any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                for v in vector
            )
        ):
            raise ValueError("Invalid embedding vector")
        return vector

    @staticmethod
    def _nearest(rows, vector, limit):
        # Portable exact search is intentionally bounded. Large deployments
        # should replace this repository capability with an indexed vector store.
        if len(rows) > 10000:
            raise Conflict(
                "Vector scope exceeds 10000 entries; configure an indexed vector adapter"
            )
        norm = math.sqrt(sum(v * v for v in vector))

        def score(row):
            candidate = json.loads(row["vector_json"])
            if len(candidate) != len(vector):
                raise Conflict(
                    "Embedding model dimensions changed; reindex with a new model/version"
                )
            denom = norm * math.sqrt(sum(v * v for v in candidate))
            return sum(a * b for a, b in zip(candidate, vector)) / denom if denom else 0

        return sorted(rows, key=score, reverse=True)[:limit]

    async def pending_memory_vectors(self, user_id, model, limit=100):
        async with self._db.connection() as c:
            result = await c.execute(
                f"""SELECT m.id,m.text,m.user_id FROM {self.table('memories')} m
                WHERE m.user_key=%s AND NOT EXISTS (SELECT 1 FROM {self.table('memory_vectors')} v
                WHERE v.memory_id=m.id AND v.model_key=%s) ORDER BY m.created_at,m.id LIMIT %s""",
                (key(user_id), key(model), limit),
            )
            return [
                {"id": r["id"], "text": r["text"]}
                for r in await result.fetchall()
                if r["user_id"] == user_id
            ]

    async def put_memory_vector(self, user_id, memory_id, model, vector):
        self._vector(vector)
        async with self._db.connection() as c:
            await self._user(c, user_id, memory=True)
            memory = await self._one(
                c, "memories", "id=%s AND user_key=%s", (memory_id, key(user_id))
            )
            if not memory or memory["user_id"] != user_id:
                return
            await self._insert(
                c,
                "memory_vectors",
                dict(
                    memory_id=memory_id,
                    user_key=key(user_id),
                    model_key=key(model),
                    model=model,
                    vector_json=dump(vector),
                ),
                ignore=True,
            )

    async def search_memory_vectors(self, user_id, model, vector, limit=10):
        self._vector(vector)
        async with self._db.connection() as c:
            result = await c.execute(
                f"""SELECT m.id,m.text,m.user_id,v.model,v.vector_json FROM {self.table('memories')} m
                JOIN {self.table('memory_vectors')} v ON v.memory_id=m.id
                WHERE m.user_key=%s AND v.user_key=%s AND v.model_key=%s LIMIT 10001""",
                (key(user_id), key(user_id), key(model)),
            )
            rows = [
                r
                for r in await result.fetchall()
                if r["user_id"] == user_id and r["model"] == model
            ]
            return [
                {"id": r["id"], "text": r["text"]}
                for r in self._nearest(rows, vector, limit)
            ]

    async def knowledge_indexed(self, version, model):
        async with self._db.connection() as c:
            return {
                r["document_id"]
                for r in await self._rows(
                    c,
                    "knowledge_vectors",
                    "version_key=%s AND model_key=%s",
                    (key(version), key(model)),
                )
                if r["version"] == version and r["model"] == model
            }

    async def put_knowledge_vector(self, version, document_id, text, model, vector):
        self._vector(vector)
        async with self._db.connection() as c:
            await self._insert(
                c,
                "knowledge_vectors",
                dict(
                    version_key=key(version),
                    version=version,
                    document_id=document_id,
                    model_key=key(model),
                    model=model,
                    text=text,
                    vector_json=dump(vector),
                ),
                ignore=True,
            )

    async def search_knowledge_vectors(self, version, model, vector, limit=10):
        self._vector(vector)
        async with self._db.connection() as c:
            rows = [
                r
                for r in await self._rows(
                    c,
                    "knowledge_vectors",
                    "version_key=%s AND model_key=%s",
                    (key(version), key(model)),
                    limit=10001,
                )
                if r["version"] == version and r["model"] == model
            ]
            return [r["text"] for r in self._nearest(rows, vector, limit)]

    async def sandbox_acquire(self, ctx):
        async with self._db.connection() as c:
            await self._fenced(c, ctx, allow_cancelled=False)
            await self._insert(
                c,
                "sandboxes",
                dict(
                    session_id=ctx.session_id,
                    phase="stopped",
                    active_calls=0,
                    touched_at=await c.now(),
                ),
                ignore=True,
            )
            row = await self._one(
                c, "sandboxes", "session_id=%s", (ctx.session_id,), lock=True
            )
            if row["phase"] == "stopping":
                raise Conflict("Sandbox stopping")
            await self._update(
                c,
                "sandboxes",
                dict(
                    phase="running",
                    active_calls=row["active_calls"] + 1,
                    touched_at=await c.now(),
                ),
                "session_id=%s",
                (ctx.session_id,),
            )

    async def sandbox_release(self, session_id):
        async with self._db.connection() as c:
            # Match the session -> sandbox lock order of acquire/teardown.
            await self._one(c, "sessions", "id=%s", (session_id,), lock=True)
            row = await self._one(
                c, "sandboxes", "session_id=%s", (session_id,), lock=True
            )
            if row:
                await self._update(
                    c,
                    "sandboxes",
                    dict(
                        active_calls=max(0, row["active_calls"] - 1),
                        touched_at=await c.now(),
                    ),
                    "session_id=%s",
                    (session_id,),
                )

    async def sandbox_idle(self, idle_seconds):
        async with self._db.connection() as c:
            result = await c.execute(
                f"""SELECT s.session_id FROM {self.table('sandboxes')} s
                WHERE s.phase='running' AND s.active_calls=0 AND s.touched_at<%s
                AND NOT EXISTS(SELECT 1 FROM {self.table('runs')} r WHERE r.session_id=s.session_id AND r.status IN ('queued','running','waiting_approval'))""",
                (await c.now() - idle_seconds,),
            )
            return [r["session_id"] for r in await result.fetchall()]

    @asynccontextmanager
    async def sandbox_stop_guard(self, session_id, expected=None):
        async with self._db.connection() as c:
            session = await self._one(c, "sessions", "id=%s", (session_id,), lock=True)
            if not session:
                raise NotFound("Session not found")
            if expected and expected.get("run_id"):
                run = await self._one(
                    c,
                    "runs",
                    "id=%s AND session_id=%s",
                    (expected["run_id"], session_id),
                    lock=True,
                )
                allowed = bool(
                    run
                    and run["status"] in ACTIVE
                    and run["epoch"] == expected.get("epoch")
                    and session["active_run_id"] == run["id"]
                )
            else:
                allowed = not await self._one(
                    c,
                    "runs",
                    "session_id=%s AND status IN ('queued','running','waiting_approval')",
                    (session_id,),
                )
            if not allowed:
                yield False
                return
            await self._insert(
                c,
                "sandboxes",
                dict(
                    session_id=session_id,
                    phase="stopped",
                    active_calls=0,
                    touched_at=await c.now(),
                ),
                ignore=True,
            )
            await self._update(
                c, "sandboxes", {"phase": "stopping"}, "session_id=%s", (session_id,)
            )
            yield True
            await self._update(
                c,
                "sandboxes",
                dict(phase="stopped", active_calls=0, touched_at=await c.now()),
                "session_id=%s",
                (session_id,),
            )
            await self._update(
                c, "sessions", {"stop_epoch": session["epoch"]}, "id=%s", (session_id,)
            )

    @asynccontextmanager
    async def session_import_guard(self, user_id, session_id, ctx=None):
        async with self._db.connection() as c:
            session = await self._session(c, user_id, session_id, lock=True)
            if ctx:
                if ctx.user_id != user_id or ctx.session_id != session_id:
                    raise NotFound("Session not found")
                await self._fenced(c, ctx, allow_cancelled=False)
            elif await self._one(
                c,
                "runs",
                "session_id=%s AND status IN ('queued','running','waiting_approval')",
                (session_id,),
            ):
                raise Conflict("Session is busy")
            yield

    async def statistics(self):
        async with self._db.connection() as c:
            counts = {}
            for table in ("runs", "tool_calls"):
                result = await c.execute(
                    f"SELECT status,COUNT(*) AS count FROM {self.table(table)} GROUP BY status"
                )
                counts[table] = {
                    r["status"]: r["count"] for r in await result.fetchall()
                }
            now = await c.now()
            row = await (
                await c.execute(
                    f"SELECT MIN(created_at) AS oldest FROM {self.table('runs')} WHERE status='queued'"
                )
            ).fetchone()
            oldest = max(0, now - row["oldest"]) if row["oldest"] is not None else 0
            row = await (
                await c.execute(
                    f"SELECT COUNT(*) AS count FROM {self.table('sandboxes')} WHERE phase='running'"
                )
            ).fetchone()
            active = row["count"]
            row = await (
                await c.execute(
                    f'SELECT AVG(started_at-created_at) AS mean FROM {self.table("runs")} WHERE started_at>%s',
                    (now - 300,),
                )
            ).fetchone()
            return {
                **counts,
                "oldest_queued_seconds": oldest,
                "active_sandboxes": active,
                "queue_seconds_mean_5m": float(row["mean"] or 0),
            }
