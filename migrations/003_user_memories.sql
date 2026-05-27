-- Tiered user memory: structured facts extracted from conversations.
-- Replaces the flat JSON metadata.memories field on conversations table.
-- Designed to add pgvector embedding column later (when Patroni image is upgraded).

CREATE TABLE IF NOT EXISTS user_memories (
    id VARCHAR(255) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id VARCHAR(255) NOT NULL,
    influencer_id VARCHAR(255) REFERENCES ai_influencers(id) ON DELETE CASCADE,
    category VARCHAR(50) NOT NULL,
    key VARCHAR(100) NOT NULL,
    value TEXT NOT NULL,
    confidence FLOAT DEFAULT 1.0,
    source_message_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_memories_user ON user_memories(user_id);
CREATE INDEX IF NOT EXISTS idx_user_memories_user_influencer ON user_memories(user_id, influencer_id);
CREATE INDEX IF NOT EXISTS idx_user_memories_category ON user_memories(user_id, category);

-- One memory per (user, influencer, key) — upsert on conflict
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_memories_unique_key
    ON user_memories(user_id, influencer_id, key);
