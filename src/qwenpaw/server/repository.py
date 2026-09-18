"""Legacy PostgreSQL prototype; not selected by the current serve CLI.

New service storage lives in qwenpaw.server.storage. This implementation and
its migrations are retained for inspection of earlier experimental data.

The repository deliberately has no in-memory fallback.  A server process can
therefore only start when its configured PostgreSQL database is available.
Transactions hold the session row lock across queue/claim/finish operations;
this is the serialization point shared by the API and controller workers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from .contracts import Conflict, ExecutionContext, LeaseLost, NotFound, TERMINAL

try:  # Keep importing the server package possible for tools without extras.
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from psycopg_pool import AsyncConnectionPool
except ModuleNotFoundError:  # pragma: no cover - exercised in dependency checks
    dict_row = None  # type: ignore[assignment]
    Jsonb = None  # type: ignore[assignment,misc]
    AsyncConnectionPool = None  # type: ignore[assignment,misc]


_RUN_COLUMNS = """
    r.id,
    r.session_id,
    r.user_id,
    r.channel_id,
    r.request_id,
    r."input" AS input,
    r.definition_version,
    r.status,
    r.epoch,
    r.worker_id,
    r.cancel_requested,
    r.lease_until,
    r.created_at,
    r.updated_at,
    r.started_at,
    r.finished_at
"""

_RUN_RETURNING_COLUMNS = """
    id,
    session_id,
    user_id,
    channel_id,
    request_id,
    "input" AS input,
    definition_version,
    status,
    epoch,
    worker_id,
    cancel_requested,
    lease_until,
    created_at,
    updated_at,
    started_at,
    finished_at
"""


def _public_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _public_row(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {key: _public_value(value) for key, value in dict(row).items()}


class PostgresRepository:
    """Async repository implementing :class:`qwenpaw.server.contracts.Repository`.

    ``open()`` is intentionally explicit so callers can fail startup before
    accepting requests.  Methods also call it defensively, which makes the
    repository convenient in small workers and tests.
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        timeout: float = 30.0,
    ) -> None:
        if AsyncConnectionPool is None:
            raise RuntimeError(
                "PostgresRepository requires psycopg[binary] and psycopg_pool"
            )
        if not dsn:
            raise ValueError("a PostgreSQL DSN is required")
        if min_size < 1 or max_size < min_size:
            raise ValueError("pool sizes must satisfy 1 <= min_size <= max_size")
        self.pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            open=False,
            kwargs={"row_factory": dict_row},
        )
        self._open_lock = asyncio.Lock()
        self._opened = False
        self._closed = False

    async def open(self) -> None:
        """Open and warm the async connection pool."""

        if self._closed:
            raise RuntimeError("repository is closed")
        if self._opened:
            return
        async with self._open_lock:
            if not self._opened:
                await self.pool.open(wait=True)
                self._opened = True

    async def close(self) -> None:
        """Close the pool; repeated calls are harmless."""

        if self._closed:
            return
        self._closed = True
        if self._opened:
            await self.pool.close()
            self._opened = False

    async def __aenter__(self) -> "PostgresRepository":
        await self.open()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[Any]:
        await self.open()
        async with self.pool.connection() as conn:
            async with conn.transaction():
                yield conn

    @staticmethod
    def _json(value: dict[str, Any]) -> Any:
        if Jsonb is None:  # pragma: no cover - methods cannot run without driver
            raise RuntimeError("psycopg JSON support is unavailable")
        return Jsonb(value)

    @staticmethod
    def _positive(value: int, name: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _limit(value: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("limit must be an integer")
        return max(0, value)

    async def migrate(self) -> None:
        """Apply bundled idempotent migrations under a database advisory lock."""

        migration_dir = Path(__file__).with_name("migrations")
        files = sorted(migration_dir.glob("*.sql"))
        if not files:
            raise RuntimeError("server migration files are missing")
        async with self._transaction() as conn:
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("qwenpaw.server.schema",),
            )
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS qp_server_schema_migrations (
                version text PRIMARY KEY, checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now())"""
            )
            for path in files:
                sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                cursor = await conn.execute(
                    "SELECT checksum FROM qp_server_schema_migrations WHERE version=%s",
                    (path.name,),
                )
                existing = await cursor.fetchone()
                if existing:
                    if existing["checksum"] != checksum:
                        raise Conflict("Applied migration changed: " + path.name)
                    continue
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO qp_server_schema_migrations(version,checksum) VALUES(%s,%s)",
                    (path.name, checksum),
                )

    async def put_definition(
        self, version: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Store an immutable assistant definition snapshot."""

        if not isinstance(version, str) or not version:
            raise ValueError("definition version must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("definition payload must be a dictionary")
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT version, payload
                FROM qp_server_definitions
                WHERE version = %s
                """,
                (version,),
            )
            row = await cursor.fetchone()
            if row is not None:
                if row["payload"] != payload:
                    raise Conflict(
                        "definition version already contains a different payload"
                    )
            else:
                await conn.execute(
                    """
                    INSERT INTO qp_server_definitions (version, payload)
                    VALUES (%s, %s)
                    ON CONFLICT (version) DO NOTHING
                    """,
                    (version, self._json(payload)),
                )
                # A concurrent immutable insert may have won after the
                # initial read. Compare the committed winner.
                cursor = await conn.execute(
                    """
                    SELECT version, payload
                    FROM qp_server_definitions
                    WHERE version = %s
                        """,
                    (version,),
                )
                row = await cursor.fetchone()
                if row is None or row["payload"] != payload:
                    raise Conflict(
                        "definition version already contains a different payload"
                    )
            return {"version": version, "payload": payload}

    async def definition(self, version: str) -> dict[str, Any]:
        """Load the immutable JSON snapshot used by a queued run."""

        async with self._transaction() as conn:
            cursor = await conn.execute(
                "SELECT payload FROM qp_server_definitions WHERE version = %s",
                (version,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise NotFound("definition not found")
            return dict(row["payload"])

    async def _session_for_submit(
        self,
        conn: Any,
        user_id: str,
        channel_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Find or create a session and retain its row lock until commit."""

        cursor = await conn.execute(
            """
            SELECT id, user_id, channel_id, external_id, state, epoch,
                   created_at, updated_at
            FROM qp_server_sessions
            WHERE user_id = %s AND channel_id = %s AND external_id = %s
            FOR UPDATE
            """,
            (user_id, channel_id, session_id),
        )
        row = await cursor.fetchone()
        if row is None:
            await conn.execute(
                """
                INSERT INTO qp_server_sessions
                    (id, user_id, channel_id, external_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, channel_id, external_id) DO NOTHING
                """,
                (uuid.uuid4(), user_id, channel_id, session_id),
            )
            cursor = await conn.execute(
                """
                SELECT id, user_id, channel_id, external_id, state, epoch,
                       created_at, updated_at
                FROM qp_server_sessions
                WHERE user_id = %s AND channel_id = %s AND external_id = %s
                FOR UPDATE
                """,
                (user_id, channel_id, session_id),
            )
            row = await cursor.fetchone()
        if row is None:  # pragma: no cover - guarded by unique key
            raise Conflict("unable to create session")
        return row

    async def _session_by_id(
        self, conn: Any, user_id: str, session_id: str, *, lock: bool = False
    ) -> dict[str, Any]:
        # Public read endpoints use internal UUIDs only. External identifiers
        # require a channel and are resolved exclusively during submission.
        query = """
            SELECT id, user_id, channel_id, external_id, state, epoch,
                   created_at, updated_at
            FROM qp_server_sessions
            WHERE user_id = %s AND id::text = %s
        """
        params: tuple[Any, ...] = (user_id, session_id)
        if lock:
            query += " FOR UPDATE"
        cursor = await conn.execute(query, params)
        row = await cursor.fetchone()
        if row is None:
            raise NotFound("session not found")
        return row

    @staticmethod
    def _user_message(payload: dict[str, Any]) -> dict[str, Any]:
        """Build the durable chat message created at queue time."""

        message = payload.get("message")
        if isinstance(message, str):
            result: dict[str, Any] = {"role": "user", "content": message}
            attachments = payload.get("attachments")
            if isinstance(attachments, list) and attachments:
                result["attachments"] = list(attachments)
            return result
        # Non-chat callers still get an auditable user message.
        return {"role": "user", "content": dict(payload)}

    @staticmethod
    def _assistant_messages(state: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract JSON-safe assistant messages from an agent snapshot."""

        if not isinstance(state, dict):
            return []
        nested = state.get("state")
        context = nested.get("context") if isinstance(nested, dict) else None
        if not isinstance(context, list):
            context = state.get("messages")
        if not isinstance(context, list):
            return []
        return [
            item
            for item in context
            if isinstance(item, dict) and item.get("role") == "assistant"
        ]

    async def _insert_message(
        self,
        conn: Any,
        session_id: Any,
        run_id: Any,
        user_id: str,
        payload: dict[str, Any],
        *,
        deduplicate: bool = False,
    ) -> bool:
        """Insert one message while the caller holds the session row lock."""

        if deduplicate:
            cursor = await conn.execute(
                """
                SELECT payload
                FROM qp_server_messages
                WHERE session_id = %s
                """,
                (session_id,),
            )
            async for existing in cursor:
                if existing["payload"] == payload:
                    return False
        cursor = await conn.execute(
            """
            SELECT COALESCE(MAX(seq), 0) AS seq
            FROM qp_server_messages
            WHERE session_id = %s
            """,
            (session_id,),
        )
        seq = int((await cursor.fetchone())["seq"]) + 1
        await conn.execute(
            """
            INSERT INTO qp_server_messages
                (id, session_id, run_id, user_id, seq, payload)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (uuid.uuid4(), session_id, run_id, user_id, seq, self._json(payload)),
        )
        return True

    async def submit(
        self,
        user_id: str,
        channel_id: str,
        session_id: str,
        request_id: str,
        payload: dict[str, Any],
        version: str,
    ) -> dict[str, Any]:
        if not user_id or not channel_id or not request_id or not version:
            raise ValueError("user, channel, request, and version are required")
        if not isinstance(payload, dict):
            raise ValueError("run payload must be a dictionary")
        async with self._transaction() as conn:
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("submit:" + repr((user_id, request_id)),),
            )
            await conn.execute(
                "INSERT INTO qp_server_users(user_id) VALUES(%s) ON CONFLICT DO NOTHING",
                (user_id,),
            )
            existing_cursor = await conn.execute(
                f"""
                SELECT {_RUN_COLUMNS}
                FROM qp_server_runs AS r
                WHERE r.user_id = %s AND r.request_id = %s
                FOR UPDATE
                """,
                (user_id, request_id),
            )
            existing = await existing_cursor.fetchone()
            if existing is not None:
                session_cursor = await conn.execute(
                    """
                    SELECT id, external_id, channel_id
                    FROM qp_server_sessions
                    WHERE id = %s
                    """,
                    (existing["session_id"],),
                )
                existing_session = await session_cursor.fetchone()
                same_session = existing_session is not None and (
                    session_id == existing_session["external_id"]
                )
                if (
                    not same_session
                    or existing["channel_id"] != channel_id
                    or existing["input"] != payload
                ):
                    raise Conflict("request_id already used with a different payload")
                return _public_row(existing)

            session = await self._session_for_submit(
                conn, user_id, channel_id, session_id
            )
            run_id = uuid.uuid4()
            cursor = await conn.execute(
                f"""
                INSERT INTO qp_server_runs
                    (id, session_id, user_id, channel_id, request_id,
                     "input", definition_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING {_RUN_RETURNING_COLUMNS}
                """,
                (
                    run_id,
                    session["id"],
                    user_id,
                    channel_id,
                    request_id,
                    self._json(payload),
                    version,
                ),
            )
            row = await cursor.fetchone()
            if row is None:  # pragma: no cover - INSERT RETURNING always returns
                raise RuntimeError("run insert returned no row")
            await self._insert_message(
                conn,
                session["id"],
                row["id"],
                user_id,
                self._user_message(payload),
            )
            return _public_row(row)

    async def get_run(self, user_id: str, run_id: str) -> dict[str, Any]:
        async with self._transaction() as conn:
            cursor = await conn.execute(
                f"""
                SELECT {_RUN_COLUMNS}
                FROM qp_server_runs AS r
                WHERE r.id::text = %s AND r.user_id = %s
                """,
                (run_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise NotFound("run not found")
            return _public_row(row)

    async def sessions(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        if not limit:
            return []
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT id, user_id, channel_id, external_id, state, epoch,
                       created_at, updated_at
                FROM qp_server_sessions
                WHERE user_id = %s
                ORDER BY updated_at DESC, id
                LIMIT %s
                """,
                (user_id, limit),
            )
            return [_public_row(row) async for row in cursor]

    async def session(self, user_id: str, session_id: str) -> dict[str, Any]:
        async with self._transaction() as conn:
            row = await self._session_by_id(conn, user_id, session_id)
            return _public_row(row)

    async def messages(
        self, user_id: str, session_id: str, limit: int = 100, after: int = 0
    ) -> list[dict[str, Any]]:
        if after < 0:
            raise ValueError("Message cursor must be non-negative")
        limit = self._limit(limit)
        if not limit:
            return []
        async with self._transaction() as conn:
            session = await self._session_by_id(conn, user_id, session_id)
            cursor = await conn.execute(
                """
                SELECT id, session_id, run_id, user_id, seq, payload, created_at
                FROM qp_server_messages
                WHERE session_id = %s AND user_id = %s AND seq > %s
                ORDER BY seq
                LIMIT %s
                """,
                (session["id"], user_id, after, limit),
            )
            return [_public_row(row) async for row in cursor]

    async def events(
        self, user_id: str, run_id: str, after: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise ValueError("event cursor must be a non-negative integer")
        limit = self._limit(limit)
        if not limit:
            return []
        async with self._transaction() as conn:
            run_cursor = await conn.execute(
                """
                SELECT id
                FROM qp_server_runs
                WHERE id::text = %s AND user_id = %s
                """,
                (run_id, user_id),
            )
            run = await run_cursor.fetchone()
            if run is None:
                raise NotFound("run not found")
            cursor = await conn.execute(
                """
                SELECT run_id, seq, payload, created_at
                FROM qp_server_events
                WHERE run_id = %s AND seq > %s
                ORDER BY seq
                LIMIT %s
                """,
                (run["id"], after, limit),
            )
            return [_public_row(row) async for row in cursor]

    async def cancel(self, user_id: str, run_id: str) -> dict[str, Any]:
        async with self._transaction() as conn:
            cursor = await conn.execute(
                f"""
                SELECT {_RUN_COLUMNS}
                FROM qp_server_runs AS r
                WHERE r.id::text = %s AND r.user_id = %s
                FOR UPDATE
                """,
                (run_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise NotFound("run not found")
            if row["status"] in TERMINAL:
                return _public_row(row)
            if row["status"] == "queued":
                await self._session_by_id(
                    conn, user_id, str(row["session_id"]), lock=True
                )
                update = await conn.execute(
                    f"""
                    UPDATE qp_server_runs
                    SET status = 'cancelled', cancel_requested = TRUE,
                        lease_until = NULL, updated_at = CURRENT_TIMESTAMP,
                        finished_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING {_RUN_RETURNING_COLUMNS}
                    """,
                    (row["id"],),
                )
                row = await update.fetchone()
                return _public_row(row)
            await conn.execute(
                """
                UPDATE qp_server_runs
                SET cancel_requested = TRUE, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (row["id"],),
            )
            result = _public_row(row)
            # The persisted lifecycle remains ``running`` until the worker
            # fences a terminal finish; the API response communicates intent.
            result["status"] = "cancel_requested"
            result["cancel_requested"] = True
            return result

    async def claim(
        self, worker_id: str, lease_seconds: int, user_limit: int
    ) -> dict[str, Any] | None:
        self._positive(lease_seconds, "lease_seconds")
        self._positive(user_limit, "user_limit")
        if not worker_id:
            raise ValueError("worker_id is required")
        async with self._transaction() as conn:
            cursor = await conn.execute(
                f"""
                SELECT r.id, r.session_id, r.user_id
                FROM qp_server_runs AS r
                JOIN qp_server_sessions AS s ON s.id = r.session_id
                JOIN qp_server_users AS u ON u.user_id = r.user_id
                WHERE r.status = 'queued'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM qp_server_runs AS prior
                      WHERE prior.session_id = r.session_id
                        AND prior.status IN ('queued', 'running', 'waiting_approval')
                        AND prior.queue_seq < r.queue_seq
                  )
                  AND (
                      SELECT count(*)
                      FROM qp_server_runs AS busy
                      WHERE busy.user_id = r.user_id
                        AND busy.status IN ('running', 'waiting_approval')
                  ) < %s
                ORDER BY u.last_claimed_at NULLS FIRST, r.queue_seq
                FOR UPDATE OF r, s, u SKIP LOCKED
                LIMIT 1
                """,
                (user_limit,),
            )
            candidate = await cursor.fetchone()
            if candidate is None:
                return None
            counts = await conn.execute(
                """SELECT count(*) AS count FROM qp_server_runs
                WHERE user_id=%s AND status IN ('running','waiting_approval')""",
                (candidate["user_id"],),
            )
            if (await counts.fetchone())["count"] >= user_limit:
                return None
            await conn.execute(
                "UPDATE qp_server_users SET last_claimed_at=now() WHERE user_id=%s",
                (candidate["user_id"],),
            )
            session_cursor = await conn.execute(
                """
                UPDATE qp_server_sessions
                SET epoch = epoch + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING epoch
                """,
                (candidate["session_id"],),
            )
            session_epoch = await session_cursor.fetchone()
            if session_epoch is None:  # pragma: no cover - FK guarantees this
                raise Conflict("execution session no longer exists")
            update = await conn.execute(
                f"""
                UPDATE qp_server_runs AS r
                SET status = 'running', epoch = %s,
                    worker_id = %s,
                    lease_until = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = 'queued'
                RETURNING {_RUN_RETURNING_COLUMNS}
                """,
                (session_epoch["epoch"], worker_id, lease_seconds, candidate["id"]),
            )
            row = await update.fetchone()
            if row is None:  # pragma: no cover - candidate lock prevents this
                return None
            return _public_row(row)

    async def _fence(self, conn: Any, ctx: ExecutionContext) -> dict[str, Any]:
        cursor = await conn.execute(
            f"""
            SELECT {_RUN_COLUMNS}
            FROM qp_server_runs AS r
            WHERE r.id::text = %s
              AND r.session_id::text = %s
              AND r.user_id = %s
              AND r.worker_id = %s
              AND r.epoch = %s
              AND r.status IN ('running', 'waiting_approval')
              AND r.lease_until > CURRENT_TIMESTAMP
            FOR UPDATE
            """,
            (
                ctx.run_id,
                ctx.session_id,
                ctx.user_id,
                ctx.worker_id,
                ctx.epoch,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise LeaseLost("execution lease is no longer authoritative")
        return row

    async def heartbeat(self, ctx: ExecutionContext, lease_seconds: int) -> bool:
        self._positive(lease_seconds, "lease_seconds")
        async with self._transaction() as conn:
            row = await self._fence(conn, ctx)
            cursor = await conn.execute(
                """
                UPDATE qp_server_runs
                SET lease_until = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING cancel_requested
                """,
                (lease_seconds, row["id"]),
            )
            updated = await cursor.fetchone()
            return bool(updated["cancel_requested"])

    async def append(
        self, ctx: ExecutionContext, payloads: list[dict[str, Any]]
    ) -> None:
        if not isinstance(payloads, list) or any(
            not isinstance(payload, dict) for payload in payloads
        ):
            raise ValueError("event payloads must be a list of dictionaries")
        async with self._transaction() as conn:
            row = await self._fence(conn, ctx)
            cursor = await conn.execute(
                """
                SELECT COALESCE(MAX(seq), 0) AS seq
                FROM qp_server_events
                WHERE run_id = %s
                """,
                (row["id"],),
            )
            current = int((await cursor.fetchone())["seq"])
            for offset, payload in enumerate(payloads, start=1):
                await conn.execute(
                    """
                    INSERT INTO qp_server_events (run_id, seq, payload)
                    VALUES (%s, %s, %s)
                    """,
                    (row["id"], current + offset, self._json(payload)),
                )

    async def finish(
        self,
        ctx: ExecutionContext,
        status: str,
        state: dict[str, Any],
        message: dict[str, Any] | None = None,
    ) -> None:
        if status not in TERMINAL:
            raise Conflict("finish status must be terminal")
        if not isinstance(state, dict):
            raise ValueError("session state must be a dictionary")
        if message is not None and not isinstance(message, dict):
            raise ValueError("message must be a dictionary")
        async with self._transaction() as conn:
            row = await self._fence(conn, ctx)
            if row["cancel_requested"] and status == "completed":
                status = "cancelled"
            # Finish and session-state/message writes share this transaction.
            session_cursor = await conn.execute(
                """
                SELECT id
                FROM qp_server_sessions
                WHERE id = %s AND user_id = %s
                FOR UPDATE
                """,
                (row["session_id"], ctx.user_id),
            )
            session = await session_cursor.fetchone()
            if session is None:
                raise LeaseLost("execution session no longer exists")
            # The worker supplies a state snapshot containing the actual model
            # conversation.  Persist assistant entries exactly once across
            # resumed snapshots, then retain the explicit terminal message
            # (if supplied) for callers that use a lighter state shape.
            history = await conn.execute(
                "SELECT payload FROM qp_server_messages WHERE session_id=%s",
                (session["id"],),
            )
            fingerprint = lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":")
            )
            seen = {fingerprint(item["payload"]) async for item in history}
            outgoing = self._assistant_messages(state)
            if message is not None:
                outgoing.append(message)
            for assistant in outgoing:
                signature = fingerprint(assistant)
                if signature not in seen:
                    await self._insert_message(
                        conn, session["id"], row["id"], ctx.user_id, assistant
                    )
                    seen.add(signature)
            await conn.execute(
                """
                UPDATE qp_server_sessions
                SET state = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (self._json(state), session["id"]),
            )
            await conn.execute(
                """
                UPDATE qp_server_runs
                SET status = %s, lease_until = NULL,
                    updated_at = CURRENT_TIMESTAMP,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (status, row["id"]),
            )
            await self._close_operations(conn, row["id"])
            event_cursor = await conn.execute(
                """
                SELECT COALESCE(MAX(seq), 0) AS seq
                FROM qp_server_events
                WHERE run_id = %s
                """,
                (row["id"],),
            )
            event_seq = int((await event_cursor.fetchone())["seq"]) + 1
            await conn.execute(
                """
                INSERT INTO qp_server_events (run_id, seq, payload)
                VALUES (%s, %s, %s)
                """,
                (
                    row["id"],
                    event_seq,
                    self._json({"type": "terminal", "status": status}),
                ),
            )

    async def _close_operations(self, conn, run_id):
        await conn.execute(
            "UPDATE qp_server_tool_calls SET status='unknown' WHERE run_id=%s AND status='running'",
            (run_id,),
        )
        await conn.execute(
            "UPDATE qp_server_approvals SET status='expired' WHERE run_id=%s AND status='pending'",
            (run_id,),
        )

    async def expired(self) -> list[dict[str, Any]]:
        """List expired leases without releasing their session exclusion."""

        async with self._transaction() as conn:
            cursor = await conn.execute(f"""
                SELECT {_RUN_COLUMNS}
                FROM qp_server_runs AS r
                WHERE r.status IN ('running', 'waiting_approval')
                  AND r.lease_until <= CURRENT_TIMESTAMP
                ORDER BY r.lease_until, r.created_at, r.id
                """)
            return [_public_row(row) async for row in cursor]

    async def interrupt(self, run_id: str, epoch: int) -> None:
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
            raise ValueError("epoch must be a non-negative integer")
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT id, status, epoch
                FROM qp_server_runs
                WHERE id::text = %s
                FOR UPDATE
                """,
                (run_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise NotFound("run not found")
            if row["epoch"] != epoch:
                raise LeaseLost("execution epoch is no longer authoritative")
            if row["status"] in TERMINAL:
                return
            if row["status"] not in {"running", "waiting_approval"}:
                raise Conflict("only a running lease can be interrupted")
            await conn.execute(
                """
                UPDATE qp_server_runs
                SET status = 'interrupted', lease_until = NULL,
                    updated_at = CURRENT_TIMESTAMP,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = %s AND epoch = %s
                """,
                (row["id"], epoch),
            )
            await self._close_operations(conn, row["id"])
            await conn.execute(
                """INSERT INTO qp_server_events(run_id,seq,payload)
                SELECT %s,COALESCE(MAX(seq),0)+1,%s FROM qp_server_events WHERE run_id=%s""",
                (
                    row["id"],
                    self._json({"type": "terminal", "status": "interrupted"}),
                    row["id"],
                ),
            )

    async def begin_tool(
        self,
        ctx: ExecutionContext,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if not call_id or not name or not isinstance(arguments, dict):
            raise ValueError("tool call id, name, and arguments are required")
        async with self._transaction() as conn:
            run = await self._fence(conn, ctx)
            if run["cancel_requested"]:
                raise Conflict("Run is cancelled")
            cursor = await conn.execute(
                """
                SELECT run_id, call_id, name, arguments, status, result
                FROM qp_server_tool_calls
                WHERE run_id = %s AND call_id = %s
                FOR UPDATE
                """,
                (run["id"], call_id),
            )
            row = await cursor.fetchone()
            if row is not None:
                if row["name"] != name or row["arguments"] != arguments:
                    raise Conflict("tool call id already contains different arguments")
                return {
                    "created": False,
                    "call_id": call_id,
                    "status": row["status"],
                    "result": row["result"],
                }
            await conn.execute(
                """
                INSERT INTO qp_server_tool_calls
                    (run_id, call_id, name, arguments, status)
                VALUES (%s, %s, %s, %s, 'running')
                """,
                (run["id"], call_id, name, self._json(arguments)),
            )
            return {
                "created": True,
                "call_id": call_id,
                "status": "running",
                "result": None,
            }

    async def end_tool(
        self,
        ctx: ExecutionContext,
        call_id: str,
        status: str,
        result: dict[str, Any],
    ) -> None:
        if not call_id or not status or not isinstance(result, dict):
            raise ValueError("tool call id, status, and result are required")
        async with self._transaction() as conn:
            run = await self._fence(conn, ctx)
            cursor = await conn.execute(
                """
                SELECT status, result
                FROM qp_server_tool_calls
                WHERE run_id = %s AND call_id = %s
                FOR UPDATE
                """,
                (run["id"], call_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise NotFound("tool call not found")
            if row["status"] != "running":
                if row["status"] == status and row["result"] == result:
                    return
                raise Conflict("tool call has already been finalized")
            await conn.execute(
                """
                UPDATE qp_server_tool_calls
                SET status = %s, result = %s, updated_at = CURRENT_TIMESTAMP
                WHERE run_id = %s AND call_id = %s
                """,
                (status, self._json(result), run["id"], call_id),
            )

    async def _approval_public(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": _public_value(row["id"]),
            "run_id": _public_value(row.get("run_id")),
            "call_id": row.get("call_id"),
            "status": row["status"],
            "expires_at": row["expires_at"].isoformat(),
        }

    async def request_approval(
        self, ctx: ExecutionContext, call_id: str, timeout: int
    ) -> dict[str, Any]:
        self._positive(timeout, "timeout")
        if not call_id:
            raise ValueError("call_id is required")
        async with self._transaction() as conn:
            run = await self._fence(conn, ctx)
            cursor = await conn.execute(
                """
                SELECT id, run_id, call_id, status, expires_at
                FROM qp_server_approvals
                WHERE run_id = %s AND call_id = %s
                FOR UPDATE
                """,
                (run["id"], call_id),
            )
            row = await cursor.fetchone()
            if row is None:
                insert = await conn.execute(
                    """
                    INSERT INTO qp_server_approvals
                        (id, run_id, user_id, call_id,
                         status, expires_at)
                    VALUES (
                        %s, %s, %s, %s, 'pending',
                        CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                    )
                    RETURNING id, run_id, call_id, status, expires_at
                    """,
                    (uuid.uuid4(), run["id"], ctx.user_id, call_id, timeout),
                )
                row = await insert.fetchone()
            elif row["status"] == "pending" and row["expires_at"] <= await self._now(
                conn
            ):
                update = await conn.execute(
                    """
                    UPDATE qp_server_approvals
                    SET status = 'expired', decided_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING id, run_id, call_id, status, expires_at
                    """,
                    (row["id"],),
                )
                row = await update.fetchone()
            if row["status"] == "pending":
                await conn.execute(
                    """
                    UPDATE qp_server_runs
                    SET status = 'waiting_approval', updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND status = 'running'
                    """,
                    (run["id"],),
                )
            return await self._approval_public(row)

    @staticmethod
    async def _now(conn: Any) -> Any:
        cursor = await conn.execute("SELECT CURRENT_TIMESTAMP AS now")
        return (await cursor.fetchone())["now"]

    async def approval(self, ctx: ExecutionContext, approval_id: str) -> dict[str, Any]:
        async with self._transaction() as conn:
            run = await self._fence(conn, ctx)
            cursor = await conn.execute(
                """
                SELECT id, run_id, call_id, status, expires_at
                FROM qp_server_approvals
                WHERE id::text = %s AND run_id = %s
                FOR UPDATE
                """,
                (approval_id, run["id"]),
            )
            row = await cursor.fetchone()
            if row is None:
                raise NotFound("approval not found")
            if row["status"] == "pending" and row["expires_at"] <= await self._now(
                conn
            ):
                update = await conn.execute(
                    """
                    UPDATE qp_server_approvals
                    SET status = 'expired', decided_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING id, run_id, call_id, status, expires_at
                    """,
                    (row["id"],),
                )
                row = await update.fetchone()
            if row["status"] != "pending":
                await conn.execute(
                    """
                    UPDATE qp_server_runs
                    SET status = 'running', updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND status = 'waiting_approval'
                    """,
                    (run["id"],),
                )
            return await self._approval_public(row)

    async def decide(
        self, user_id: str, approval_id: str, approved: bool
    ) -> dict[str, Any]:
        if not isinstance(approved, bool):
            raise ValueError("approved must be a boolean")
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT id, run_id, call_id, status, expires_at
                FROM qp_server_approvals
                WHERE id::text = %s AND user_id = %s
                FOR UPDATE
                """,
                (approval_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise NotFound("approval not found")
            if row["status"] == "pending":
                now = await self._now(conn)
                if row["expires_at"] <= now:
                    status = "expired"
                else:
                    status = "approved" if approved else "denied"
                update = await conn.execute(
                    """
                    UPDATE qp_server_approvals
                    SET status = %s, decided_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING id, run_id, call_id, status, expires_at
                    """,
                    (status, row["id"]),
                )
                row = await update.fetchone()
            return await self._approval_public(row)

    async def add_file(
        self,
        user_id: str,
        file_id: str,
        name: str,
        key: str,
        size: int,
        ctx: ExecutionContext | None = None,
    ) -> dict[str, Any]:
        if not user_id or not file_id or not name or not key:
            raise ValueError("file metadata is incomplete")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("size must be a non-negative integer")
        async with self._transaction() as conn:
            if ctx is not None:
                if ctx.user_id != user_id:
                    raise NotFound("File not found")
                await self._fence(conn, ctx)
            cursor = await conn.execute(
                """
                SELECT id, user_id, file_id, name, object_key, size_bytes, created_at
                FROM qp_server_files
                WHERE user_id = %s AND file_id = %s
                FOR UPDATE
                """,
                (user_id, file_id),
            )
            row = await cursor.fetchone()
            if row is not None:
                if (
                    row["name"] != name
                    or row["object_key"] != key
                    or row["size_bytes"] != size
                ):
                    raise Conflict("file id already contains different metadata")
            else:
                insert = await conn.execute(
                    """
                    INSERT INTO qp_server_files
                        (id, user_id, file_id, name, object_key, size_bytes)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, user_id, file_id, name, object_key,
                              size_bytes, created_at
                    """,
                    (uuid.uuid4(), user_id, file_id, name, key, size),
                )
                row = await insert.fetchone()
            return self._file_public(row)

    @staticmethod
    def _file_public(row: dict[str, Any]) -> dict[str, Any]:
        result = _public_row(row)
        if "object_key" in result:
            result["key"] = result.pop("object_key")
        if "size_bytes" in result:
            result["size"] = result.pop("size_bytes")
        return result

    async def file(self, user_id: str, file_id: str) -> dict[str, Any]:
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT id, user_id, file_id, name, object_key, size_bytes, created_at
                FROM qp_server_files
                WHERE user_id = %s AND (file_id = %s OR id::text = %s)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, file_id, file_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise NotFound("file not found")
            return self._file_public(row)

    async def delete_file(self, user_id: str, file_id: str) -> dict[str, Any]:
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT id, user_id, file_id, name, object_key, size_bytes, created_at
                FROM qp_server_files
                WHERE user_id = %s AND (file_id = %s OR id::text = %s)
                ORDER BY created_at DESC
                LIMIT 1
                FOR UPDATE
                """,
                (user_id, file_id, file_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise NotFound("file not found")
            await conn.execute(
                "DELETE FROM qp_server_files WHERE id = %s", (row["id"],)
            )
            return self._file_public(row)

    async def memories(self, user_id: str, query: str) -> list[dict[str, Any]]:
        if not isinstance(query, str):
            raise ValueError("memory query must be a string")
        async with self._transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT id, user_id, session_id, text, created_at
                FROM qp_server_memories
                WHERE user_id = %s
                  AND (%s = '' OR text ILIKE ('%%' || %s || '%%'))
                ORDER BY created_at DESC, id
                LIMIT 100
                """,
                (user_id, query, query),
            )
            return [_public_row(row) async for row in cursor]

    async def remember(self, ctx: ExecutionContext, text: str) -> None:
        if not isinstance(text, str) or not text:
            raise ValueError("memory text must be non-empty")
        async with self._transaction() as conn:
            run = await self._fence(conn, ctx)
            await conn.execute(
                """
                INSERT INTO qp_server_memories (id, user_id, session_id, text)
                VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
                """,
                (uuid.uuid4(), ctx.user_id, run["session_id"], text),
            )

    async def delete_memories(self, user_id: str) -> None:
        async with self._transaction() as conn:
            check = await conn.execute(
                "SELECT to_regclass('qp_server_memory_jobs') AS table_name"
            )
            if (await check.fetchone())["table_name"]:
                await conn.execute(
                    "UPDATE qp_server_memory_jobs SET status='cancelled' WHERE user_id=%s AND status IN ('queued','running')",
                    (user_id,),
                )
            await conn.execute(
                "DELETE FROM qp_server_memories WHERE user_id=%s", (user_id,)
            )

    async def prune_events(self, days: int = 7) -> None:
        if not isinstance(days, int) or isinstance(days, bool) or days < 0:
            raise ValueError("days must be a non-negative integer")
        async with self._transaction() as conn:
            await conn.execute(
                """
                DELETE FROM qp_server_events
                WHERE created_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
                """,
                (days,),
            )


# A short alias is useful to callers that name their concrete store ``Pg``.
PgRepository = PostgresRepository

__all__ = ["PostgresRepository", "PgRepository"]
