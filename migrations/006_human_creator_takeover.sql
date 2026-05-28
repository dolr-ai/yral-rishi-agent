-- Chat as Human: creator takes over a conversation and replies directly.
-- AI calls are skipped while takeover is active. Auto-releases after 2 min inactivity.

-- Add takeover state to conversations
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS human_creator_takeover_active BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS human_creator_user_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS human_creator_takeover_started_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS user_last_message_at TIMESTAMP;

-- Add takeover markers to messages
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS is_human_creator_takeover BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS human_creator_user_id VARCHAR(255);

-- Allow 'system' role for takeover join/leave announcements
ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_role_check;
ALTER TABLE messages ADD CONSTRAINT messages_role_check
    CHECK (role::text = ANY (ARRAY['user'::varchar, 'assistant'::varchar, 'system'::varchar]::text[]));

-- Partial index: zero overhead for non-takeover conversations, fast scan for active ones
CREATE INDEX IF NOT EXISTS idx_conversations_active_takeover
    ON conversations(id) WHERE human_creator_takeover_active = TRUE;
