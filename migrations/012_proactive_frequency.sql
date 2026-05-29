-- Task D / Phase 5.4: user-configurable proactive frequency.
--
-- Each conversation gets a per-(user, bot) preference:
--   'default' — current behavior (24h threshold)
--   'daily'   — same as default; explicit opt-in
--   'weekly'  — 7-day threshold
--   'off'     — never send a proactive in this conversation
--
-- Existing rows get DEFAULT 'default', so behavior is unchanged on rollout.
-- The engagement loop reads this column and computes per-row thresholds.

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS proactive_frequency VARCHAR(16) DEFAULT 'default'
        CHECK (proactive_frequency IN ('default', 'daily', 'weekly', 'off'));

-- Helpful for the engagement loop scan
CREATE INDEX IF NOT EXISTS idx_conversations_proactive_active
    ON conversations (updated_at)
    WHERE proactive_frequency != 'off' AND influencer_id IS NOT NULL;
