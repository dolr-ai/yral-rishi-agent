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
-- Zero-downtime pattern (squawk-approved: each statement is either
-- idempotent-guarded or in its own implicit transaction so a
-- partial-failure rerun succeeds cleanly, and NOT VALID + VALIDATE
-- run in SEPARATE transactions per squawk's `constraint-missing-not-
-- valid` rule so the validate step doesn't hold long ACCESS EXCLUSIVE):
--   1. ADD COLUMN IF NOT EXISTS (nullable, no default → no rewrite)
--   2. Backfill via UPDATE WHERE id IS NULL (rerun-safe)
--   3. SET DEFAULT (idempotent — replaces)
--   4. ADD CONSTRAINT via DO block IF NOT EXISTS (rerun-safe)
--   5. VALIDATE CONSTRAINT (implicit txn separate from #4; validate
--      of an already-validated constraint is a no-op)
--   6. CREATE UNIQUE INDEX IF NOT EXISTS

SET lock_timeout = '3s';
SET statement_timeout = '60s';

-- 1. Add the column (nullable → no rewrite path).
ALTER TABLE influencer_collages
    ADD COLUMN IF NOT EXISTS id UUID;

-- 2. Backfill every existing row. gen_random_uuid() is Postgres 13+
--    built-in; no pgcrypto extension needed. WHERE id IS NULL makes
--    this rerun-safe.
UPDATE influencer_collages SET id = gen_random_uuid() WHERE id IS NULL;

-- 3. Default for future inserts (SET DEFAULT is idempotent — replaces).
ALTER TABLE influencer_collages ALTER COLUMN id SET DEFAULT gen_random_uuid();

-- 4. NOT NULL invariant via CHECK NOT VALID — implicit txn 1.
--    The DO block wraps ADD CONSTRAINT with an IF NOT EXISTS check
--    against pg_constraint (Postgres 15 doesn't support IF NOT
--    EXISTS on ADD CONSTRAINT syntactically, hence the DO block).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'influencer_collages_id_not_null'
    ) THEN
        ALTER TABLE influencer_collages
            ADD CONSTRAINT influencer_collages_id_not_null
            CHECK (id IS NOT NULL) NOT VALID;
    END IF;
END $$;

-- 5. VALIDATE — implicit txn 2 (SEPARATE from step 4 to satisfy
--    squawk's zero-downtime rule and to keep the ACCESS EXCLUSIVE
--    lock window as short as possible). Validating an already-valid
--    constraint is a no-op.
ALTER TABLE influencer_collages VALIDATE CONSTRAINT influencer_collages_id_not_null;

-- 6. UNIQUE index — the CHECK doesn't imply uniqueness; the separate
--    index enforces "one row per UUID" so the route's get_by_id
--    lookup uses it as the primary handle.
CREATE UNIQUE INDEX IF NOT EXISTS influencer_collages_id_key
    ON influencer_collages (id);
