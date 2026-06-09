-- Phase 21αβ — Coach Fix 1 PR-A
-- Add per-bot global-rule overrides so Coach can carve out exceptions to
-- platform-wide rules (e.g. response_length, language_mirror) WITHOUT
-- editing the bot's system_instructions. Saikat's 2026-06-09 alpha
-- session surfaced the bug: GLOBAL_RULES' "1-3 sentences max" silently
-- wrapped his bot's longer-replies instruction, so Coach's edits did
-- nothing.
--
-- Schema:
--   {"response_length": "long_allowed", "language_mirror": "always_english", ...}
-- Today only `response_length` is wired; the column ships ready for
-- additional keys per Coach PR-B.
--
-- Safety: ADD COLUMN with a DEFAULT of a small constant value is
-- metadata-only on pg11+ (no row rewrite). Safe on a live table with
-- ~3,941 rows.
--
-- 2026-06-09: added `set lock_timeout` + `set statement_timeout` per
-- squawk migration linter recommendation. Even though the pg11+
-- metadata-only optimization makes this fast in practice, the
-- timeouts are a defense-in-depth net against unexpected lock
-- contention from concurrent writes during the rollout window.

SET lock_timeout = '3s';
SET statement_timeout = '60s';

ALTER TABLE ai_influencers
    ADD COLUMN IF NOT EXISTS global_rule_overrides JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN ai_influencers.global_rule_overrides IS
    'Per-bot opt-outs for GLOBAL_RULES keys in soul_file.compose(). '
    'Schema: {rule_key: value}. Setting any value disables the platform '
    'default for that key on this bot. Coach (creator_coach route) is the '
    'only writer; bots cannot self-modify.';
