-- Phase 1 Request Images — add opaque UUID identifier to influencer_collages.
--
-- Sarvesh (mobile) 2026-07-09 flagged the API-shape gap during
-- integration: mobile stores (collage_bot_id, collage_date) in every
-- chat message so it can refetch on subscription-state transitions,
-- but that composite key is fragile if we ever ship "multiple collages
-- per bot per day" (design open question §1c — morning/evening themes,
-- A/B experiments, etc.). Rishi 2026-07-09 chose to future-proof now
-- rather than pay a migration cost later.
--
-- Design: keep the existing composite PK (bot_id, generation_date) as
-- the race-lock anchor — the INSERT … ON CONFLICT DO NOTHING idiom
-- that elects the generator relies on it, and that machinery is
-- proven in production. Add a UNIQUE UUID `id` alongside as the
-- externally-visible identifier that mobile stores. API contract:
--
--   GET /api/v1/influencers/{bot_id}/collage
--     ?collage_id=<uuid>    — preferred: direct fetch
--     &date=YYYY-MM-DD      — fallback: fetch by (bot_id, date)
--     — neither: default to today's UTC date
--
-- Rollout is additive:
--   1. Add nullable column (fast — no rewrite in PG15+)
--   2. Backfill every existing row with gen_random_uuid()
--   3. SET NOT NULL + DEFAULT + UNIQUE index
--
-- Splitting the ADD from the NOT NULL avoids the "full table rewrite
-- on ADD COLUMN NOT NULL DEFAULT non_constant" pitfall. On our
-- current tiny row count each step is instantaneous; the split
-- pattern still matters for future scale.

SET lock_timeout = '3s';
SET statement_timeout = '60s';

-- 1. Add the column (nullable, no default → no rewrite path).
ALTER TABLE influencer_collages
    ADD COLUMN IF NOT EXISTS id UUID;

-- 2. Backfill every existing row. gen_random_uuid() is
--    Postgres 13+ built-in; no pgcrypto extension needed.
UPDATE influencer_collages SET id = gen_random_uuid() WHERE id IS NULL;

-- 3. Enforce NOT NULL + default going forward + UNIQUE.
--    ALTER … SET NOT NULL scans the table under an ACCESS EXCLUSIVE
--    lock; on a small table this is milliseconds. If this table ever
--    grows to millions of rows, this is the migration to break up.
ALTER TABLE influencer_collages ALTER COLUMN id SET NOT NULL;
ALTER TABLE influencer_collages ALTER COLUMN id SET DEFAULT gen_random_uuid();

CREATE UNIQUE INDEX IF NOT EXISTS influencer_collages_id_key
    ON influencer_collages (id);
