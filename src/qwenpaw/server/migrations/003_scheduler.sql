CREATE TABLE IF NOT EXISTS qp_server_users (
    user_id text PRIMARY KEY,
    last_claimed_at timestamptz
);
INSERT INTO qp_server_users(user_id) SELECT DISTINCT user_id FROM qp_server_runs ON CONFLICT DO NOTHING;
