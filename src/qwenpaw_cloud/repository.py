"""Bounded P0 repositories. PostgreSQL owns time and serializes admission.

This intentionally uses one static admission row (32 queued/running runs).
P2 replaces that bottleneck with tenant/user scheduler rows, not SKIP LOCKED
pretending to provide fairness. File I/O never runs inside these transactions.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import threading

TABLES = (
    "scopes",
    "sessions",
    "messages",
    "runs",
    "attempts",
    "workers",
    "requests",
    "commits",
    "events",
    "snapshots",
    "artifacts",
)


class FixtureRepository:
    """Explicit non-durable fixture; never selected by the production CLI."""

    mode = "fixture"

    def __init__(self):
        self.data = {t: {} for t in TABLES}
        self.lock = threading.RLock()

    @contextmanager
    def transaction(self):
        with self.lock:
            state = deepcopy(self.data)
            yield state, datetime.now(timezone.utc).timestamp()
            self.data = state


class PostgresRepository:
    mode = "postgresql"

    def __init__(self, dsn: str):
        import psycopg

        self.dsn = dsn
        migration = (
            Path(__file__).resolve().parents[2]
            / "cloud/migrations/001_core.sql"
        )
        with psycopg.connect(dsn) as conn:
            conn.execute(migration.read_text())

    @contextmanager
    def transaction(self):
        import psycopg
        from psycopg.types.json import Jsonb

        with psycopg.connect(self.dsn, connect_timeout=3) as conn:
            conn.execute("SET LOCAL lock_timeout = '3s'")
            conn.execute("SET LOCAL statement_timeout = '5s'")
            conn.execute(
                "SELECT id FROM harness.admission WHERE id=1 FOR UPDATE"
            )
            state = {}
            for table in TABLES:
                state[table] = dict(
                    conn.execute(
                        f"SELECT id, data FROM harness.{table} "
                        "ORDER BY id FOR UPDATE"
                    ).fetchall()
                )
            before = deepcopy(state)
            now = conn.execute(
                "SELECT extract(epoch FROM clock_timestamp())"
            ).fetchone()[0]
            yield state, float(now)
            for table in TABLES:
                if not before[table].keys() <= state[table].keys():
                    raise RuntimeError("P0 retains all authoritative records")
                for key, value in state[table].items():
                    if key not in before[table]:
                        conn.execute(
                            f"INSERT INTO harness.{table}(id,data) "
                            "VALUES (%s,%s)",
                            (key, Jsonb(value)),
                        )
                    elif value != before[table][key]:
                        updated = conn.execute(
                            f"UPDATE harness.{table} "
                            "SET data=%s WHERE id=%s AND data=%s",
                            (Jsonb(value), key, Jsonb(before[table][key])),
                        )
                        if updated.rowcount != 1:
                            raise RuntimeError("conditional update lost")
