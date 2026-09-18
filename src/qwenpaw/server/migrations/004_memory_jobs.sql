
CREATE TABLE IF NOT EXISTS qp_server_memory_jobs (
    run_id uuid PRIMARY KEY REFERENCES qp_server_runs(id), user_id text NOT NULL,
    status text NOT NULL DEFAULT 'queued', owner uuid, lease_until timestamptz,
    attempts integer NOT NULL DEFAULT 0, error text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS qp_server_memory_text_idx ON qp_server_memories(user_id, md5(text));
CREATE OR REPLACE FUNCTION qp_server_queue_memory() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'completed' AND OLD.status <> 'completed' THEN
    INSERT INTO qp_server_memory_jobs(run_id,user_id) VALUES(NEW.id,NEW.user_id) ON CONFLICT DO NOTHING;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS qp_server_memory_finished ON qp_server_runs;
CREATE TRIGGER qp_server_memory_finished AFTER UPDATE ON qp_server_runs
FOR EACH ROW EXECUTE FUNCTION qp_server_queue_memory();

DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector') THEN
  EXECUTE 'CREATE TABLE IF NOT EXISTS qp_server_search_vectors (memory_id uuid PRIMARY KEY REFERENCES qp_server_memories(id) ON DELETE CASCADE, model text NOT NULL, embedding vector NOT NULL)';
  EXECUTE 'CREATE TABLE IF NOT EXISTS qp_server_knowledge_vectors (version text NOT NULL, document_id integer NOT NULL, text text NOT NULL, model text NOT NULL, embedding vector NOT NULL, PRIMARY KEY(version,document_id,model))';
 END IF;
END $$;
