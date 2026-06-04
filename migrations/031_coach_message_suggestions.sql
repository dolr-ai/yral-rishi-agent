-- Coach UX overhaul (2026-06-04) — opening-message suggestion chips.
--
-- The Feature Strategy session redesigned the "Make your AI Influencer
-- better" flow to have the coach speak FIRST with a warm greeting +
-- three short tappable suggestion chips. Mobile renders the chips as
-- buttons that prefill the creator's next message.
--
-- JSONB is the right shape because suggestion text is short, free-form,
-- and won't be queried structurally. Convention: a list of 3 strings
--   ["Improve their voice", "Make them funnier", "Tighten their bio"]
-- Older messages without an opening (creator turns, follow-up coach
-- turns) leave the column NULL. Mobile MUST tolerate null.
--
-- Apply manually after pg_dump per Rule 9.

ALTER TABLE coach_messages
    ADD COLUMN IF NOT EXISTS suggestions JSONB;

COMMENT ON COLUMN coach_messages.suggestions IS
    'Phase coach-UX-overhaul (2026-06-04) — JSONB list of 3 short
     suggestion chip strings for the OPENING coach message in a
     session. NULL for creator turns and non-opening coach turns.';
