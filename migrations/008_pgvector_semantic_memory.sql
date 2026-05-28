-- Phase 4.4: semantic memory search via pgvector.
-- Spilo 3.0 image ships pgvector; we just need to enable + add the column.
--
-- Existing rows get embedding=NULL; backfilled by scripts/backfill_memory_embeddings.py.
-- The ivfflat index works fine with NULL rows (they're skipped).
-- 768 dimensions matches Gemini text-embedding-004.

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE user_memories
    ADD COLUMN IF NOT EXISTS embedding vector(768);

-- ivfflat index for cosine distance lookup. lists=100 is the rule-of-thumb for
-- datasets up to ~1M rows; we can tune later as the table grows. Index builds
-- only on non-NULL rows, so it's cheap until backfill finishes.
CREATE INDEX IF NOT EXISTS idx_user_memories_embedding
    ON user_memories USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
