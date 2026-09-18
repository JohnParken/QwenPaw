-- Public IDs are opaque strings, including invalid UUIDs which must return 404.
CREATE INDEX IF NOT EXISTS qp_server_runs_text_id_idx ON qp_server_runs ((id::text));
CREATE INDEX IF NOT EXISTS qp_server_sessions_text_id_idx ON qp_server_sessions ((id::text));
CREATE TABLE IF NOT EXISTS qp_server_sandboxes (
    session_id uuid PRIMARY KEY REFERENCES qp_server_sessions(id),
    phase text NOT NULL DEFAULT 'stopped', active_calls integer NOT NULL DEFAULT 0,
    touched_at timestamptz NOT NULL DEFAULT now()
);
