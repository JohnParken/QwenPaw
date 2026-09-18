"""SQL transport only. Memory SQLite deliberately does not emulate InnoDB locks."""

from __future__ import annotations

import asyncio
import re
import sqlite3
import ssl
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, unquote, urlsplit


class Result:
    def __init__(self, rows, rowcount):
        self.rows, self.rowcount = rows, rowcount

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, raw, dialect, clock):
        self.raw, self.dialect, self.clock = raw, dialect, clock

    def locked(self, sql):
        return sql + (" FOR UPDATE" if self.dialect == "mysql" else "")

    async def execute(self, sql, params=()):
        if self.dialect == "sqlite":
            cursor = self.raw.execute(sql.replace("%s", "?"), params)
            rows = (
                [dict(row) for row in cursor.fetchall()] if cursor.description else []
            )
            return Result(rows, cursor.rowcount)
        import aiomysql

        async with self.raw.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(sql, params)
            return Result(
                await cursor.fetchall() if cursor.description else [], cursor.rowcount
            )

    async def now(self):
        if self.dialect == "sqlite":
            return float(self.clock())
        result = await self.execute("SELECT UNIX_TIMESTAMP(UTC_TIMESTAMP(6)) AS value")
        return float((await result.fetchone())["value"])


class Database:
    def __init__(self, dsn=None, *, prefix="qp_service_", max_size=10, clock=time.time):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}_", prefix):
            raise ValueError("Invalid table prefix")
        self.prefix, self.dsn, self.max_size, self.clock = prefix, dsn, max_size, clock
        self.dialect = "mysql" if dsn else "sqlite"
        self.pool = None
        self.raw = None
        self.lock = asyncio.Lock()
        self.open_lock = asyncio.Lock()

    async def open(self):
        async with self.open_lock:
            if self.dialect == "sqlite":
                if self.raw is None:
                    self.raw = sqlite3.connect(":memory:", isolation_level=None)
                    self.raw.row_factory = sqlite3.Row
                return
            if self.pool is not None:
                return
            try:
                import aiomysql
            except ImportError as exc:
                raise RuntimeError(
                    "Install QwenPaw server dependencies for TDSQL"
                ) from exc
            url = urlsplit(self.dsn)
            if (
                url.scheme not in {"mysql", "mariadb", "tdsql"}
                or not url.hostname
                or not url.path.strip("/")
            ):
                raise ValueError("Expected mysql://user:password@host:port/database")
            options = parse_qs(url.query, strict_parsing=True)
            if set(options) - {"ssl_ca", "ssl_cert", "ssl_key", "connect_timeout"}:
                raise ValueError("Unsupported TDSQL connection option")
            tls = None
            if options.get("ssl_ca"):
                tls = ssl.create_default_context(cafile=options["ssl_ca"][0])
                if options.get("ssl_cert"):
                    tls.load_cert_chain(
                        options["ssl_cert"][0], options.get("ssl_key", [None])[0]
                    )
            self.pool = await aiomysql.create_pool(
                host=url.hostname,
                port=url.port or 3306,
                user=unquote(url.username or ""),
                password=unquote(url.password or ""),
                db=unquote(url.path[1:]),
                charset="utf8mb4",
                minsize=1,
                maxsize=self.max_size,
                autocommit=True,
                ssl=tls,
                connect_timeout=int(options.get("connect_timeout", ["10"])[0]),
                init_command="SET time_zone = '+00:00'",
            )

    async def close(self):
        if self.pool is not None:
            self.pool.close()
            await self.pool.wait_closed()
            self.pool = None
        if self.raw is not None:
            self.raw.close()
            self.raw = None

    @asynccontextmanager
    async def connection(self, *, transaction=True):
        await self.open()
        if self.dialect == "sqlite":
            async with self.lock:
                if transaction:
                    self.raw.execute("BEGIN IMMEDIATE")
                try:
                    yield Connection(self.raw, "sqlite", self.clock)
                    if transaction:
                        self.raw.execute("COMMIT")
                except BaseException:
                    if transaction:
                        self.raw.execute("ROLLBACK")
                    raise
            return
        async with self.pool.acquire() as raw:
            try:
                if transaction:
                    async with raw.cursor() as cursor:
                        await cursor.execute(
                            "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED"
                        )
                    await raw.begin()
                yield Connection(raw, "mysql", self.clock)
                if transaction:
                    await raw.commit()
            except asyncio.CancelledError:
                # A cancelled driver operation cannot safely be returned to the pool.
                raw.close()
                raise
            except BaseException:
                if transaction:
                    try:
                        await raw.rollback()
                    except BaseException:
                        raw.close()
                raise
