CREATE SCHEMA IF NOT EXISTS __OFFICE_SCHEMA__;

CREATE TABLE IF NOT EXISTS __OFFICE_SCHEMA__.sessions (
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id, record_id)
);

CREATE TABLE IF NOT EXISTS __OFFICE_SCHEMA__.turns (
    LIKE __OFFICE_SCHEMA__.sessions INCLUDING ALL
);
CREATE TABLE IF NOT EXISTS __OFFICE_SCHEMA__.messages (
    LIKE __OFFICE_SCHEMA__.sessions INCLUDING ALL
);
CREATE TABLE IF NOT EXISTS __OFFICE_SCHEMA__.files (
    LIKE __OFFICE_SCHEMA__.sessions INCLUDING ALL
);
CREATE TABLE IF NOT EXISTS __OFFICE_SCHEMA__.artifacts (
    LIKE __OFFICE_SCHEMA__.sessions INCLUDING ALL
);
CREATE TABLE IF NOT EXISTS __OFFICE_SCHEMA__.skill_executions (
    LIKE __OFFICE_SCHEMA__.sessions INCLUDING ALL
);

CREATE UNIQUE INDEX IF NOT EXISTS office_turn_request_uq
ON __OFFICE_SCHEMA__.turns (
    tenant_id,
    user_id,
    (data->>'session_id'),
    (data->>'idempotency_key')
)
WHERE data->>'idempotency_key' IS NOT NULL;

CREATE INDEX IF NOT EXISTS office_messages_session_idx
ON __OFFICE_SCHEMA__.messages (tenant_id, user_id, (data->>'session_id'));
CREATE INDEX IF NOT EXISTS office_artifacts_session_idx
ON __OFFICE_SCHEMA__.artifacts (tenant_id, user_id, (data->>'session_id'));
