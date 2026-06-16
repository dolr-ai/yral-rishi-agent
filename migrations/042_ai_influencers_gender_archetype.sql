-- Phase 21γ.P34.M1 — Discovery Feed milestone 1: bot classification.
--
-- Two additive columns on `ai_influencers` that the M1 background
-- classifier writes + downstream code reads:
--
--   gender    — male | female | neutral | unknown (default 'unknown')
--   archetype — 5 fixed values matching `ARCHETYPE_PROMPTS` keys
--               in app/services/soul_file.py EXACTLY:
--                 companion · advisor · entertainer · educator · creator
--               (+ 'unknown' for pre-classify)
--
-- Plus one index on archetype for M2 composer's diversity pass.
--
-- ## Why archetype + category (not the rev-7 bot_type 8-value taxonomy)
--
-- Decision locked 2026-06-16 PM by Rishi: 8 buckets conflated
-- persona-style (HOW the bot talks) with topic (WHAT it covers).
-- They're orthogonal axes — same topic × different persona = different
-- bots — so collapsing them lost real ranking signal AND couldn't grow
-- with new topics (food, travel, weather, gaming, crypto, …).
--
-- The two-column orthogonal model:
--   - `category`  (UNCHANGED) — free-form VARCHAR(100), user-facing,
--                  what mobile shows. "Food & Drink", "Travel", etc.
--                  Grows freely; no migration to category.
--   - `archetype` (THIS PR)   — locked 5-value enum mapped 1:1 to
--                  ARCHETYPE_PROMPTS. Stays at 5; do NOT expand to 8.
--
-- Side effect: the existing `archetype = category.lower().strip()`
-- derivation at soul_file.py:274 silently fails on 93% of production
-- bots (3427/3684 active rows have a free-form `category` that doesn't
-- match the 5 magic strings). Promoting archetype to a real column
-- fixes this — every classified bot lands its archetype layer in the
-- prompt, where today most bots skip the layer entirely.
--
-- ## Safety
--
-- ADD COLUMN with a constant default is metadata-only on PG11+ (no
-- table rewrite). Safe on a populated table.
--
-- Rule 9: pg_dump BEFORE apply. Auto-runner (PR #309) handles it.

SET lock_timeout = '3s';
SET statement_timeout = '60s';

-- pg_trgm is the engine behind the M4 category_affinity signal (Stage B
-- ranking — trigram similarity between user-chat-history category
-- distribution and the bot's free-form category). chat-ai already uses
-- pg_trgm for influencer search; CREATE EXTENSION IS IDEMPOTENT, so
-- this is a no-op if already enabled.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE ai_influencers
    ADD COLUMN IF NOT EXISTS gender VARCHAR(10) NOT NULL DEFAULT 'unknown';

ALTER TABLE ai_influencers
    ADD COLUMN IF NOT EXISTS archetype VARCHAR(32) NOT NULL DEFAULT 'unknown';

-- Index for M2 composer's diversity pass — JOINs on archetype to
-- spread persona styles across feed pages. Same shape as the existing
-- category index.
CREATE INDEX IF NOT EXISTS idx_ai_influencers_archetype
    ON ai_influencers (archetype);

-- M4 category_affinity signal — GIN trigram index on LOWER(category)
-- so `category % '<query>'` and `similarity(...)` scans stay cheap on
-- a 3k-row catalog with free-form category strings.
CREATE INDEX IF NOT EXISTS idx_ai_influencers_category_trgm
    ON ai_influencers USING gin (LOWER(category) gin_trgm_ops);

COMMENT ON COLUMN ai_influencers.gender IS
    'Phase 21γ.P34.M1 — classified via influencer_classification LLM; '
    'soft cold-start guardrail only (NEVER a ranking axis). '
    'Values: male|female|neutral|unknown.';

COMMENT ON COLUMN ai_influencers.archetype IS
    'Phase 21γ.P34.M1 — persona style (5-value locked enum). One of: '
    'companion, advisor, entertainer, educator, creator. Plus '
    '"unknown" for pre-classify rows. Maps 1:1 to ARCHETYPE_PROMPTS '
    'in app/services/soul_file.py. Orthogonal to free-form `category`.';
