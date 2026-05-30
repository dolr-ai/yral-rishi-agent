-- Phase 6.3p — add is_nudge column to messages, parallel to is_proactive.
--
-- Background: Phase 5.3p (PR #187) added a 3-message cap to the proactive
-- engagement loop so a stuck user doesn't get spammed when they go quiet.
-- The same bug pattern exists for the Phase 6 nudge loop and was never
-- fixed — a user who falls silent after the greeting can receive up to
-- 3 nudges in 45 min (5 + 10 + 10 + 10 + 10 min spacing) before
-- msg_count > 4 stops the loop. Each nudge is a separate bot message
-- with no record that it was a nudge.
--
-- This column lets us:
--   1. Tag nudge messages at write time (set is_nudge=true)
--   2. Count unanswered nudges since the user's last reply
--   3. Cap at 1 — different from proactive's 3, because nudges fire
--      every 15 min during early-conversation idle. One try is plenty;
--      if the user didn't respond to that, they're not engaging.

ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_nudge BOOLEAN NOT NULL DEFAULT FALSE;

-- Partial index keeps the cap-check query fast even as the messages
-- table grows large. Mirrors the proactive equivalent.
CREATE INDEX IF NOT EXISTS idx_messages_unanswered_nudge
    ON messages (conversation_id, created_at DESC)
    WHERE is_nudge = TRUE;
