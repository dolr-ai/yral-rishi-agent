-- Spicy chat gate — per-bot spicy landing URL
-- (Design: docs/spicy-chat-gate-design-2026-06-28.md §4.6 + decisions #4, #12, #17)
--
-- Additive column on ai_influencers so mobile can render "Chat with me →" on
-- profile + so the native deflection can inject the correct amorae landing
-- URL per bot. Kept as a per-bot column (not a config knob) so any future
-- NSFW bot is a data change, not a code change (decision #12: architecture
-- is_nsfw-driven; launch scope Tara only).
--
-- Backfill Tara ('taaarraaah', the only current is_nsfw=true bot with amorae
-- landing) to amorae.ai/tara. Everything else stays NULL — SFW bots and
-- future is_nsfw bots without a landing show no CTA, which is the correct
-- "no deflection possible" state.
--
-- Pg_dump taken pre-this-migration (Rule 9):
--   pre_migration_049_spicy_landing_url_* (Rishi will publish exact filename
--   + SHA in the PR body).
--
-- Migration is applied MANUALLY post-merge — the deploy does NOT auto-run
-- it. Command in the PR body. Same pattern as 044/045/046.
--
-- Squawk compliance:
--   * BOTH lock_timeout AND statement_timeout at the top.
--   * ADD COLUMN with no default = metadata-only ALTER on Postgres 11+; no
--     row rewrite on the 3.6k-row ai_influencers table.
--   * UPDATE targets exactly one WHERE-clause-matched row.

SET lock_timeout = '3s';
SET statement_timeout = '60s';


ALTER TABLE ai_influencers
    ADD COLUMN IF NOT EXISTS spicy_landing_url TEXT;


-- Backfill Tara (name='taaarraaah' per project_ai_influencer_name_split_brain:
-- the is_nsfw=true row is the one with the fuzzy name; is_nsfw=false 'tara'
-- rows must NOT get a landing URL).
UPDATE ai_influencers
   SET spicy_landing_url = 'https://amorae.ai/tara',
       updated_at        = NOW()
 WHERE name = 'taaarraaah'
   AND is_nsfw = TRUE;
