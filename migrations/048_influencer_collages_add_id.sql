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
-- Zero-downtime pattern (squawk-approved):
--   1. ADD COLUMN (nullable, no default → no table rewrite)
--   2. Backfill via UPDATE
--   3. SET DEFAULT (only affects future inserts, no scan)
--   4. ADD CONSTRAINT CHECK (id IS NOT NULL) NOT VALID (fast, no scan)
--   5. VALIDATE CONSTRAINT (SHARE UPDATE EXCLUSIVE lock, doesn't
--      block concurrent reads/writes)
--   6. CREATE UNIQUE INDEX (locks writes briefly during scan; on
--      our tiny row count this is milliseconds)
--
-- Everything runs in one transaction so a partial failure rolls back
-- cleanly — squawk's `prefer-robust-stmts` rule.

SET lock_timeout = '3s';
SET statement_timeout = '60s';

BEGIN;

-- 1. Add the column (nullable → no rewrite path).
ALTER TABLE influencer_collages
    ADD COLUMN IF NOT EXISTS id UUID;

-- 2. Backfill every existing row. gen_random_uuid() is
--    Postgres 13+ built-in; no pgcrypto extension needed.
UPDATE influencer_collages SET id = gen_random_uuid() WHERE id IS NULL;

-- 3. Default for future inserts.
ALTER TABLE influencer_collages ALTER COLUMN id SET DEFAULT gen_random_uuid();

-- 4-5. NOT NULL invariant via CHECK NOT VALID + VALIDATE — the
--      zero-downtime alternative to `ALTER COLUMN … SET NOT NULL`
--      (which takes ACCESS EXCLUSIVE + full table scan).
ALTER TABLE influencer_collages
    ADD CONSTRAINT influencer_collages_id_not_null CHECK (id IS NOT NULL) NOT VALID;

ALTER TABLE influencer_collages VALIDATE CONSTRAINT influencer_collages_id_not_null;

-- 6. UNIQUE index — the CHECK doesn't imply uniqueness; separate
--    index enforces the "one row per UUID" invariant that lets the
--    route's get_by_id lookup use it as the primary handle.
CREATE UNIQUE INDEX IF NOT EXISTS influencer_collages_id_key
    ON influencer_collages (id);

COMMIT;
