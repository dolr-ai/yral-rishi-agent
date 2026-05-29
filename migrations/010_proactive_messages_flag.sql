-- Task 2 (Phase 5 polish): proactive message quality fix.
--
-- Adds a boolean flag on messages so we can:
--   1. Cap unanswered proactive messages per conversation (Motorola showed
--      bots sending 3-4 unanswered "hey what's up" messages)
--   2. Show the last 3 proactive messages to Gemini as "don't repeat these"
--      context when generating the next one
--
-- DEFAULT FALSE → existing rows are non-proactive (correct: they were all
-- written via the user-driven send-message path before this PR).
--
-- Partial index on TRUE so the count + recent-list queries don't scan the
-- whole table.

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS is_proactive BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_messages_proactive_conv
    ON messages (conversation_id, created_at DESC)
    WHERE is_proactive = TRUE;
