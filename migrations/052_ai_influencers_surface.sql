-- Surface targeting on ai_influencers — which product a persona appears in.
--
-- Requested by the amorae-web session 2026-08-10. amorae.ai (the adult web
-- surface) and the mobile app now share one backend and one ai_influencers
-- catalogue, so the catalogue needs to say where each persona belongs.
--
-- Values: 'mobile' | 'web' | 'both'.
--
-- NOT NULL DEFAULT 'mobile' is the safety-critical part, and it is
-- deliberately the opposite encoding from migration 051's target_markets
-- (where NULL means "everywhere"). The two columns guard opposite
-- directions:
--
--   target_markets  NULL = visible in EVERY market   → open by default
--   surface         DEFAULT 'mobile'                 → closed to web by default
--
-- Market targeting fails safe by showing too much of a catalogue the user
-- is already allowed to see. Surface targeting fails safe only by showing
-- LESS: a persona must be explicitly opted in before it can ever appear on
-- an adult web surface. A NULL-means-everywhere encoding here would mean a
-- single missed backfill silently publishes all 3,804 active mainstream
-- personas to amorae.ai. So: no row is ever "unset".
--
-- The CHECK constraint is worth the bytes because this is an enum whose
-- wrong value has a real-world consequence — a typo like 'Web' silently
-- dropping a persona out of both surfaces, or worse, into the wrong one.
--
-- ADD COLUMN with a CONSTANT default is metadata-only on Postgres 11+ (the
-- default is stored in pg_attribute, not written to every row), so this is
-- NOT a table rewrite even with NOT NULL. All 4,083 rows get 'mobile'
-- without being touched.
--
-- Index: btree, not GIN. 051 needed GIN because target_markets is an array
-- answering `@>` containment; surface is a scalar answering `IN`, which
-- btree serves. Low cardinality (3 values) means the planner will often
-- prefer a seq scan on a table this small — the index earns its keep once
-- the web surface queries it routinely and the distribution is skewed
-- (~1 web row vs ~3,800 mobile).
--
-- Rule 9: handled automatically — scripts/ci/run-migrations.sh takes a
-- per-migration pg_dump to S3 and fails closed. No manual snapshot.
--
-- NOTE (2026-08-08 lesson): ai_influencers is a hot table and ALTER TABLE
-- needs ACCESS EXCLUSIVE. Migration 051 failed its first apply on a 3s
-- lock_timeout purely from acquisition contention and succeeded on a
-- straight re-run. If this one does the same, re-run the deploy workflow
-- before changing anything.
--
-- Squawk compliance: BOTH timeouts before any DDL (the PR #427 lesson).

SET lock_timeout = '3s';
SET statement_timeout = '60s';


ALTER TABLE ai_influencers
    ADD COLUMN IF NOT EXISTS surface TEXT NOT NULL DEFAULT 'mobile';


ALTER TABLE ai_influencers
    DROP CONSTRAINT IF EXISTS ai_influencers_surface_check;

ALTER TABLE ai_influencers
    ADD CONSTRAINT ai_influencers_surface_check
    CHECK (surface IN ('mobile', 'web', 'both')) NOT VALID;

ALTER TABLE ai_influencers
    VALIDATE CONSTRAINT ai_influencers_surface_check;


CREATE INDEX IF NOT EXISTS idx_ai_influencers_surface
    ON ai_influencers (surface);


COMMENT ON COLUMN ai_influencers.surface IS
    'Which product this persona appears in: mobile | web | both. Defaults to '
    '''mobile'' so nothing reaches the amorae.ai adult web surface without an '
    'explicit opt-in — the inverse of target_markets, where NULL means '
    'everywhere. Read via app/services/surface.py. The filter is OPT-IN at the '
    'API: a request with no ?surface= param is unfiltered, so mobile behaviour '
    'is unchanged until mobile chooses to send one.';
