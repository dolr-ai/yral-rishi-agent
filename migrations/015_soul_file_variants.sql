-- Task 3 / Phase 7.6: A/B testing for Soul Files.
--
-- Variant A is always the bot's current system_instructions on
-- ai_influencers. Variant B lives here, one row per bot — present only
-- while a test is active. When variant B exists, send_message randomly
-- picks A or B 50/50 per turn and records the choice on the message via
-- the new variant_label column.

CREATE TABLE IF NOT EXISTS soul_file_variants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id VARCHAR(255) NOT NULL REFERENCES ai_influencers(id) ON DELETE CASCADE,
    system_instructions TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    created_by VARCHAR(255) NOT NULL,
    UNIQUE (bot_id)
);

-- New column on messages tracks which variant generated the bot reply.
-- NULL = no test was active when the message was written, or this is a
-- user message. 'a' or 'b' = variant used.
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS variant_label VARCHAR(1);

CREATE INDEX IF NOT EXISTS idx_messages_variant
    ON messages (variant_label, conversation_id)
    WHERE variant_label IS NOT NULL;
