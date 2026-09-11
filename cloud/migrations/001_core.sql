-- P0 bounded static admission. All writes lock this row, then scope/run/attempt.
CREATE SCHEMA IF NOT EXISTS harness;
CREATE TABLE IF NOT EXISTS harness.admission(id integer PRIMARY KEY CHECK(id=1));
INSERT INTO harness.admission VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS harness.scopes(id text PRIMARY KEY, data jsonb NOT NULL,
 CHECK(data->>'tenant_id' IS NOT NULL AND data->>'owner_user_id' IS NOT NULL),
 CHECK((data->>'revision')::bigint >= 0));
CREATE TABLE IF NOT EXISTS harness.runs(id text PRIMARY KEY, data jsonb NOT NULL,
 CHECK(data->>'scope_key' IS NOT NULL),
 CHECK(data->>'state' IN ('QUEUED','STARTING','RUNNING','RECOVERING','SUCCEEDED','FAILED','CANCELLED','TIMED_OUT')));
CREATE TABLE IF NOT EXISTS harness.attempts(id text PRIMARY KEY, data jsonb NOT NULL,
 CHECK((data->>'lease_epoch')::bigint > 0),
 CHECK(data->>'state' IN ('ASSIGNED','STARTING','RUNNING','COMPLETED','STOPPED','LOST','FAILED')),
 CHECK(data->>'cleanup' IN ('PENDING','CONFIRMED','QUARANTINED')));
CREATE UNIQUE INDEX IF NOT EXISTS one_valid_attempt ON harness.attempts((data->>'run_id'))
 WHERE data->>'state' IN ('ASSIGNED','STARTING','RUNNING');
CREATE UNIQUE INDEX IF NOT EXISTS one_worker_slot ON harness.attempts((data->>'worker_id'))
 WHERE data->>'cleanup' = 'PENDING';
CREATE TABLE IF NOT EXISTS harness.workers(id text PRIMARY KEY, data jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS harness.requests(id text PRIMARY KEY, data jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS harness.commits(id text PRIMARY KEY, data jsonb NOT NULL,
 CHECK(data->>'state' IN ('PREPARING','PINNED','COMMITTED','ABORTED')));
CREATE TABLE IF NOT EXISTS harness.events(id text PRIMARY KEY, data jsonb NOT NULL);
