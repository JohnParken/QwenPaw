CREATE SCHEMA IF NOT EXISTS files;

CREATE TABLE IF NOT EXISTS files.files (
    file_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    scope JSONB NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    status TEXT NOT NULL,
    blob_path TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (file_id, version)
);

CREATE TABLE IF NOT EXISTS files.reference_sets (
    set_id TEXT PRIMARY KEY,
    scope JSONB NOT NULL,
    digest TEXT NOT NULL,
    refs JSONB NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
