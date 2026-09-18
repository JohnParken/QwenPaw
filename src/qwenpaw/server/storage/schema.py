"""Versioned, portable relational schema. Opaque identity keys are SHA-256 hex.

Original identities remain in TEXT and are compared in Python; collation never
participates in authorization. No PG types, partial indexes or SQL triggers.
"""

SCHEMA_VERSION = "001"
TABLES = {
    "users": (
        """user_key CHAR(64) PRIMARY KEY, user_id LONGTEXT NOT NULL, last_claimed_at DOUBLE""",
        [],
    ),
    "definitions": (
        """version_key CHAR(64) PRIMARY KEY, version LONGTEXT NOT NULL, payload_json LONGTEXT NOT NULL""",
        [],
    ),
    "sessions": (
        """id CHAR(36) PRIMARY KEY, user_key CHAR(64) NOT NULL, user_id LONGTEXT NOT NULL,
 channel_id LONGTEXT NOT NULL, external_id LONGTEXT NOT NULL, scope_key CHAR(64) NOT NULL UNIQUE,
 state_json LONGTEXT NOT NULL, epoch BIGINT NOT NULL, next_run_seq BIGINT NOT NULL,
 next_message_seq BIGINT NOT NULL, active_run_id CHAR(36), stop_epoch BIGINT NOT NULL,
 created_at DOUBLE NOT NULL, updated_at DOUBLE NOT NULL""",
        [("session_user", "user_key, updated_at")],
    ),
    "runs": (
        """id CHAR(36) PRIMARY KEY, session_id CHAR(36) NOT NULL, user_key CHAR(64) NOT NULL,
 user_id LONGTEXT NOT NULL, channel_id LONGTEXT NOT NULL, request_key CHAR(64) NOT NULL,
 request_id LONGTEXT NOT NULL, input_json LONGTEXT NOT NULL, definition_version LONGTEXT NOT NULL,
 status VARCHAR(32) NOT NULL, epoch BIGINT NOT NULL, worker_id LONGTEXT,
 cancel_requested INTEGER NOT NULL, lease_until DOUBLE, queue_seq BIGINT NOT NULL,
 next_event_seq BIGINT NOT NULL, created_at DOUBLE NOT NULL, updated_at DOUBLE NOT NULL,
 started_at DOUBLE, finished_at DOUBLE, UNIQUE(user_key, request_key)""",
        [
            ("run_queue", "status, created_at"),
            ("run_session", "session_id, queue_seq"),
            ("run_user", "user_key, status"),
            ("run_lease", "status, lease_until"),
        ],
    ),
    "messages": (
        """id CHAR(36) NOT NULL, session_id CHAR(36) NOT NULL, run_id CHAR(36) NOT NULL,
 user_key CHAR(64) NOT NULL, user_id LONGTEXT NOT NULL, seq BIGINT NOT NULL,
 payload_json LONGTEXT NOT NULL, created_at DOUBLE NOT NULL, PRIMARY KEY(session_id,seq)""",
        [],
    ),
    "events": (
        """run_id CHAR(36) NOT NULL, seq BIGINT NOT NULL, payload_json LONGTEXT NOT NULL,
 created_at DOUBLE NOT NULL, PRIMARY KEY(run_id,seq)""",
        [("event_age", "created_at")],
    ),
    "tool_calls": (
        """run_id CHAR(36) NOT NULL, call_key CHAR(64) NOT NULL, call_id LONGTEXT NOT NULL,
 name LONGTEXT NOT NULL, arguments_json LONGTEXT NOT NULL, status VARCHAR(32) NOT NULL,
 result_json LONGTEXT, created_at DOUBLE NOT NULL, updated_at DOUBLE NOT NULL, PRIMARY KEY(run_id,call_key)""",
        [],
    ),
    "approvals": (
        """id CHAR(36) PRIMARY KEY, run_id CHAR(36) NOT NULL, user_key CHAR(64) NOT NULL,
 call_key CHAR(64) NOT NULL, call_id LONGTEXT NOT NULL, status VARCHAR(32) NOT NULL,
 expires_at DOUBLE NOT NULL, created_at DOUBLE NOT NULL, decided_at DOUBLE, UNIQUE(run_id,call_key)""",
        [],
    ),
    "files": (
        """id CHAR(36) PRIMARY KEY, user_key CHAR(64) NOT NULL, user_id LONGTEXT NOT NULL,
 file_key CHAR(64) NOT NULL, file_id LONGTEXT NOT NULL, name LONGTEXT NOT NULL, object_key LONGTEXT NOT NULL,
 size_bytes BIGINT NOT NULL, created_at DOUBLE NOT NULL, UNIQUE(user_key,file_key)""",
        [],
    ),
    "memory_users": (
        """user_key CHAR(64) PRIMARY KEY, user_id LONGTEXT NOT NULL""",
        [],
    ),
    "memories": (
        """id CHAR(36) PRIMARY KEY, user_key CHAR(64) NOT NULL, user_id LONGTEXT NOT NULL,
 session_id CHAR(36) NOT NULL, text LONGTEXT NOT NULL, text_key CHAR(64) NOT NULL,
 created_at DOUBLE NOT NULL, UNIQUE(user_key,text_key)""",
        [],
    ),
    "memory_jobs": (
        """run_id CHAR(36) PRIMARY KEY, user_key CHAR(64) NOT NULL, user_id LONGTEXT NOT NULL,
 status VARCHAR(32) NOT NULL, owner LONGTEXT, epoch BIGINT NOT NULL, lease_until DOUBLE,
 attempts INTEGER NOT NULL, error LONGTEXT, created_at DOUBLE NOT NULL""",
        [("memory_queue", "status, created_at"), ("memory_owner", "user_key,status")],
    ),
    "memory_vectors": (
        """memory_id CHAR(36) NOT NULL, user_key CHAR(64) NOT NULL, model_key CHAR(64) NOT NULL,
 model LONGTEXT NOT NULL, vector_json LONGTEXT NOT NULL, PRIMARY KEY(memory_id,model_key)""",
        [("vector_user", "user_key,model_key")],
    ),
    "knowledge_vectors": (
        """version_key CHAR(64) NOT NULL, version LONGTEXT NOT NULL, document_id BIGINT NOT NULL,
 model_key CHAR(64) NOT NULL, model LONGTEXT NOT NULL, text LONGTEXT NOT NULL,
 vector_json LONGTEXT NOT NULL, PRIMARY KEY(version_key,document_id,model_key)""",
        [],
    ),
    "sandboxes": (
        """session_id CHAR(36) PRIMARY KEY, phase VARCHAR(32) NOT NULL,
 active_calls BIGINT NOT NULL, touched_at DOUBLE NOT NULL""",
        [],
    ),
}


def statements(prefix, dialect):
    result = []
    for name, (columns, indexes) in TABLES.items():
        if dialect == "mysql":
            indexed = "".join(
                f", INDEX {label} ({fields})" for label, fields in indexes
            )
            result.append(
                f"CREATE TABLE IF NOT EXISTS {prefix}{name} ({columns}{indexed}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
            )
        else:
            result.append(
                f'CREATE TABLE IF NOT EXISTS {prefix}{name} ({columns.replace("LONGTEXT", "TEXT")})'
            )
            result.extend(
                f"CREATE INDEX IF NOT EXISTS {prefix}{label} ON {prefix}{name} ({fields})"
                for label, fields in indexes
            )
    return result
