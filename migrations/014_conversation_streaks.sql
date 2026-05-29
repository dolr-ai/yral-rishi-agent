-- Task 5 / Phase 5.6: streak tracking.
-- Reward consistent chatters with a per-conversation streak count.
-- Daily background job updates these columns; mobile shows them when ready.
--
-- Logic:
--   - User sends a message → if last_streak_date is today: no change
--                          → if last_streak_date is yesterday: ++current_streak_days
--                          → if last_streak_date is older than yesterday: current_streak_days = 1
--                          → in all cases, longest_streak_days = max(longest, current)
-- Today the daily-job approach is simpler than gating every user message.

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS current_streak_days INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS longest_streak_days INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_streak_date DATE;

-- Index helps the daily-pass scan find conversations active in the last few days
CREATE INDEX IF NOT EXISTS idx_conversations_last_streak_date
    ON conversations (last_streak_date);
