# P0: TDSQL storage

This document supersedes PostgreSQL deployment/storage instructions in
`multi-user-server.md`. The BFF API and sandbox boundaries remain unchanged.

## Configuration

Install `.[server]`. Set `QWENPAW_SERVER_DATABASE_URL` to a secret-managed
`mysql://user:password@host:3306/database` URL (also accepts `mariadb://` and
`tdsql://`). Percent-encode URL credentials. Optional URL parameters are
`ssl_ca`, `ssl_cert`, `ssl_key`, and `connect_timeout`. Use a trusted CA for TLS.
`QWENPAW_SERVER_STORAGE_TABLE_PREFIX` defaults to `qp_service_`;
`QWENPAW_SERVER_DATABASE_POOL_SIZE` defaults to 10 per process.
All API, Worker, Controller and migration processes must use the same database
and table prefix. Retain the service tokens and assistant definition settings
described in the main deployment document.

Run `qwenpaw serve migrate` once before starting the existing API, Worker and
Controller commands. DDL is versioned and checksum-verified. Run one migration
job at a time. MySQL DDL commits implicitly: each statement is idempotent and
the completed version is recorded only after all statements succeed, so a
failed migration can be retried. Applied DDL must not be edited.

The current serve entry point selects TDSQL explicitly. It does not fall back
to memory when storage fails. It rejects `memory://`, because separate service
processes would otherwise have independent databases. `MemoryRepository` is
only for in-process tests and uses SQLite `:memory:`.

The old PostgreSQL repository and `qp_server_*` migrations are retained as a
historical prototype; the current serve entry point does not select them.
No existing PostgreSQL data is read, migrated, or removed. This release starts
with new platform data in `qp_service_*` tables. Existing personal/local mode
continues to use its original storage.

## Isolation and execution

`server/contracts.py` declares the Repository capabilities. API, Worker,
MemoryService and Controller call this interface rather than pool connections
or SQL. `server/storage` implements the same state machine for memory and
TDSQL. Production SQL targets the conservative MySQL/MariaDB 10.3 feature set:
InnoDB, READ COMMITTED, row locks, and short transactions. It does not require
SKIP LOCKED, RETURNING, JSONB, pgvector, partial indexes, or triggers.

Claims lock user, session, then run rows, rechecking global user quota and
session FIFO. The session's active_run_id is the serialization point. Expired
workers cannot write, and expiry alone does not release the session. Confirmed
sandbox termination must precede interruption. Unknown tool outcomes are not
automatically replayed. Controller teardown/file import intentionally holds
the session lock across Pod deletion/file write to exclude new work; its lock
wait behavior and latency must be checked on the actual production engine.
Ordinary model calls and tool execution do not hold database transactions.

Opaque external IDs are preserved verbatim, indexed by SHA-256 scope keys and
checked against the original strings. Case and trailing-space collations do
not collapse users. Memory extraction has a persistent queue, per-user writer
exclusion, leases and monotonically increasing epochs. Deleting memory revokes
pending extraction jobs, including results arriving late.

Optional embedding vectors are JSON text and are searched by exact cosine
similarity within an authorized user/configuration scope. This requires no
pgvector extension. A scope exceeding 10,000 vectors fails explicitly; this is
not an ANN index or a large-scale vector-store capacity claim. Configure an
indexed adapter before scaling beyond that bound. Without embedding_model,
the configured mode remains lexical retrieval.

## Verification

Run local tests without a database service:

```bash
python -m pytest tests/unit/server/test_storage_contract.py tests/unit/server/test_service.py -q
```

The memory backend validates ownership, idempotency, FIFO/quota behavior,
approvals, cancellation, lease fencing, memory jobs, vectors and rollback using
the same repository logic. SQLite serializes its test transactions; these
results do not prove InnoDB locking, SQL compatibility, deadlocks or capacity.

For real TDSQL, provision a dedicated test database and set the secret URL in
`QWENPAW_TEST_TDSQL_URL`, plus `QWENPAW_TEST_TDSQL_ALLOW_DDL=1`. Never point this
at production. Then run:

```bash
python -m pytest tests/unit/server/test_storage_contract.py -k tdsql -q
```

Each test creates and deletes only tables with its random `qp_test_*` prefix.
Missing configuration produces explicit skips, not success. These contract
tests must pass on the actual TDSQL 10.3 deployment before rollout, followed by
multi-process worker-kill drills and the 100-run/500-SSE capacity acceptance.
No TDSQL test instance is currently available, so that verification is pending.
Historical PostgreSQL benchmarks do not apply to this backend.

For backup/restore, use the TDSQL platform backup or a logical tool matching the
actual engine, together with S3 and PVC snapshots at a consistent recovery
point. Pause writes and drain or interrupt active work first. Do not use the
historical pg_dump/pg_restore instructions for TDSQL. Restore reference
integrity and failure-drill acceptance remain pending on real infrastructure.

Local P0 verification (2026-09-18): server suite 35 passed, 17 skipped.
The skips include 11 unconfigured TDSQL cases, four legacy PostgreSQL cases,
one memory-only persistence exclusion, and one optional sandbox integration.
Compilation, formatting and whitespace checks passed. No real TDSQL result
is claimed.
