-- Assigned after acquiring the session row lock: FIFO follows durable admission,
-- not transaction start times or client clocks.
ALTER TABLE qp_server_runs ADD COLUMN IF NOT EXISTS queue_seq bigserial;
CREATE INDEX IF NOT EXISTS qp_server_session_queue_idx ON qp_server_runs(session_id, queue_seq);
CREATE UNIQUE INDEX IF NOT EXISTS qp_server_one_active_run_idx ON qp_server_runs(session_id)
    WHERE status IN ('running', 'waiting_approval');
