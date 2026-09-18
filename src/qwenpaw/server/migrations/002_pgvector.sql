-- Optional vector support.  A base PostgreSQL installation can run the
-- repository without pgvector; when the extension is available this creates
-- a side table that can be populated by a future embedding service.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
        EXECUTE 'CREATE EXTENSION IF NOT EXISTS vector';
        EXECUTE $vector$
            CREATE TABLE IF NOT EXISTS qp_server_memory_embeddings (
                memory_id uuid PRIMARY KEY
                    REFERENCES qp_server_memories(id) ON DELETE CASCADE,
                embedding vector(1536) NOT NULL
            )
        $vector$;
    END IF;
END
$$;
