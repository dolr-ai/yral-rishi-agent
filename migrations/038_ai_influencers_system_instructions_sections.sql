-- Phase 21αβ — Coach Bucket 2 PR-1
-- Adds the `system_instructions_sections` JSONB column on ai_influencers
-- so a bot's personality can live as an ordered list of typed sections
-- instead of one opaque blob of text. See
-- docs/designs/coach-bucket-2-sections-contract.md for the full shape
-- + rollout plan.
--
-- Section shape (one element of the array):
--   {
--     "id": "core_personality",        -- lowercase snake_case slug, unique within bot
--     "heading": "Core personality",    -- display label, mobile renders this
--     "body": "<the instruction text>",-- THE thing Coach edits + the LLM sees at chat time
--     "editable": true                 -- when false, mobile UI + Coach refuse to edit
--   }
--
-- Default '[]'::jsonb so the migration is metadata-only on pg11+ (no
-- row rewrite on the 3,941 ai_influencers rows). Bots without sections
-- keep using the flat system_instructions column exactly as today —
-- soul_file.compose() prefers sections only when len(sections) > 0
-- AND the COACH_SECTIONED_V2_ENABLED flag is on. See PR-2 for the
-- compose() + Coach META_PROMPT + /apply dispatch wiring.
--
-- Safety: ADD COLUMN with a JSONB DEFAULT of a small literal value is
-- metadata-only on pg11+ (no row rewrite). Same pattern proven by
-- migration 033 (global_rule_overrides '{}'::jsonb).
--
-- Rule 9: Rishi's standing GO via the auto-pg_dump runner (PR #309)
-- covers this — 4 prior dumps this week from 033/034/035/036 into
-- s3://rishi-yral/yral-rishi-agent-pre-migration-dumps/.

-- squawk: cap lock-wait + statement duration per the migration linter
-- (I-Mig2, #340). 30s lock_timeout (NOT the 3s default from earlier
-- migrations) because ai_influencers is the hottest read table on this
-- service — every chat-send loads the influencer row. The first deploy
-- attempt of 038 (2026-06-11T09:46Z) timed out at 3s waiting for the
-- brief AccessExclusive lock to flip the catalog. 30s gives the ALTER
-- enough headroom to slot in between in-flight reads. The actual write
-- is metadata-only on PG11+ (no row rewrite), so once the lock IS
-- acquired the work is sub-millisecond.
SET lock_timeout = '30s';
SET statement_timeout = '60s';

ALTER TABLE ai_influencers
    ADD COLUMN IF NOT EXISTS system_instructions_sections JSONB NOT NULL
        DEFAULT '[]'::jsonb;

COMMENT ON COLUMN ai_influencers.system_instructions_sections IS
    'Coach Bucket 2 PR-1. Ordered list of typed personality sections '
    '({id, heading, body, editable}). When non-empty AND '
    'COACH_SECTIONED_V2_ENABLED=true, soul_file.compose() renders L4 '
    '(per-influencer) from these sections instead of the flat '
    'system_instructions blob. Coach proposes against ONE section per '
    'turn via the proposed_section_change shape (see migration 039 + '
    'PR-2). Empty default = backwards-compatible — bot keeps using '
    'flat system_instructions until the creator opts in via the Soul '
    'File page.';
