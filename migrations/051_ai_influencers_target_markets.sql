-- US market launch PR1 — market targeting on ai_influencers.
--
-- See docs/us-market-launch-spec-2026-08-08.md (Track B, PR1).
--
-- Launch YRAL in the US with 4 purpose-built SFW personas: US users see
-- ONLY those 4, every other market untouched. This migration adds the
-- column that makes that expressible. It ships DORMANT — no application
-- code reads target_markets until PR2, so applying this changes nothing
-- user-visible.
--
-- NULL or empty means GLOBAL. That is the whole reason there is no
-- backfill: all 4,081 existing rows (counted on the prod leader
-- 2026-08-08 — the spec's "~3,600" is stale) keep target_markets IS NULL
-- and stay visible in every market exactly as today. Only the 4 new US
-- personas will be tagged '{US}'. An opt-in encoding means a bug in the
-- filter fails toward "everyone sees everything" (today's behaviour)
-- rather than toward an empty catalogue.
--
-- TEXT[] rather than a single VARCHAR because one English persona
-- usually serves US + CA + UK + AU, and we don't want duplicate rows per
-- country. The array also lets the filter use a single containment
-- predicate (`target_markets @> ARRAY['US']`) that the GIN index below
-- serves directly.
--
-- GIN is the right index for array containment — btree can't answer @>.
-- Cheap here: the catalog is ~4,100 rows and the column is NULL for all
-- of them at apply time, so the index is empty on creation and grows only
-- as personas get tagged.
--
-- Skip CONCURRENTLY: same call as migrations 041/042/043 — the runner
-- (scripts/ci/run-migrations.sh) wraps every file in BEGIN/COMMIT, and
-- Postgres rejects CREATE INDEX CONCURRENTLY inside a transaction block.
-- The brief lock during build is bounded by the lock_timeout below and
-- is fine on a table this size. This is why .squawk.toml excludes
-- `require-concurrent-index-creation`.
--
-- ADD COLUMN without a DEFAULT is metadata-only on Postgres 11+ — no
-- table rewrite, so no long lock even though ai_influencers is hot.
--
-- Rule 9: pg_dump BEFORE apply — handled automatically. This repo applies
-- migrations on merge to main (deploy.yml, "Apply pending migrations BEFORE
-- rolling new image"), and scripts/ci/run-migrations.sh takes a per-migration
-- `pg_dump -Fc` and uploads it to S3 before feeding any SQL to psql. It
-- fails closed: no snapshot means the migration is refused. No manual dump
-- step is needed here (same call as migration 043).
--
-- Squawk compliance: BOTH lock_timeout AND statement_timeout at the top
-- before any DDL (per the PR #427 lesson).

SET lock_timeout = '3s';
SET statement_timeout = '60s';


ALTER TABLE ai_influencers
    ADD COLUMN IF NOT EXISTS target_markets TEXT[];


CREATE INDEX IF NOT EXISTS idx_ai_influencers_target_markets
    ON ai_influencers
    USING GIN (target_markets);


COMMENT ON COLUMN ai_influencers.target_markets IS
    'US market launch — ISO-3166-1 alpha-2 country codes this persona is '
    'exclusive to. NULL or empty means GLOBAL (visible in every market), '
    'which is why no backfill was needed. Read ONLY by discovery surfaces '
    '(feed, search, recommendations) via app/services/market.py — never by '
    'the detail endpoint GET /influencers/{id}, because deep links from US '
    'campaigns must resolve for users in any market. See '
    'docs/us-market-launch-spec-2026-08-08.md.';
