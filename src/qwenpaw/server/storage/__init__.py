"""Storage selection is explicit. Never fail over to an ephemeral backend."""

from .database import Database
from .repository import SQLRepository


class MemoryRepository(SQLRepository):
    """Single-process SQLite memory database, intended for contract tests only."""

    def __init__(self, *, clock=None, prefix="qp_service_"):
        kwargs = {"clock": clock} if clock else {}
        super().__init__(Database(prefix=prefix, **kwargs))


class TDSQLRepository(SQLRepository):
    """MySQL/MariaDB-compatible TDSQL, using InnoDB row transactions."""

    def __init__(self, dsn, *, prefix="qp_service_", max_size=10):
        if not isinstance(dsn, str) or not dsn.startswith(
            ("mysql://", "mariadb://", "tdsql://")
        ):
            raise ValueError("TDSQLRepository requires an explicit TDSQL/MySQL DSN")
        super().__init__(Database(dsn, prefix=prefix, max_size=max_size))


def create_repository(dsn, *, prefix="qp_service_", max_size=10):
    if dsn == "memory://":
        return MemoryRepository(prefix=prefix)
    if dsn.startswith(("mysql://", "mariadb://", "tdsql://")):
        return TDSQLRepository(dsn, prefix=prefix, max_size=max_size)
    raise ValueError(
        "Use a mysql://, mariadb:// or tdsql:// DSN; memory:// is for in-process tests only"
    )
