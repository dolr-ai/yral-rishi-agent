-- Phase 21γ.P34.Search — discovery search endpoint backing index.
--
-- One additive GIN trigram index on a concatenated lowercased
-- expression spanning the four user-visible text fields + the M1
-- archetype. Backs `GET /api/v2/discovery/search` (added in the
-- same PR) per docs/discovery-feed-search-addendum-2026-06-18.md.
--
-- Concatenation order (display_name first, then name, then the
-- soft signals) matches the design's expected weighting — a query
-- that matches the bot's display name should rank higher than one
-- that only matches words in its description. similarity() weighs
-- earlier tokens more heavily.
--
-- pg_trgm extension was already enabled by migration 042
-- (idx_ai_influencers_category_trgm); CREATE INDEX here just adds
-- the new search expression's index.
--
-- Skip CONCURRENTLY: catalog is ~3,700 rows; the brief AccessShare
-- lock during build is fine. Matches the broader pattern of the
-- migration-runner already wrapping each file in BEGIN/COMMIT
-- (which is why .squawk.toml excludes `require-concurrent-index-
-- creation` — see migrations/041 + 042 for the same call).
--
-- Rule 9: pg_dump BEFORE apply. Auto-runner (PR #309) handles it.

SET lock_timeout = '3s';
SET statement_timeout = '60s';

CREATE INDEX IF NOT EXISTS idx_ai_influencers_search_trgm
    ON ai_influencers
    USING gin (
        LOWER(
            display_name
            || ' ' || name
            || ' ' || COALESCE(category,   '')
            || ' ' || COALESCE(archetype,  '')
            || ' ' || COALESCE(description, '')
        ) gin_trgm_ops
    );

COMMENT ON INDEX idx_ai_influencers_search_trgm IS
    'Phase 21γ.P34.Search — backs GET /api/v2/discovery/search. '
    'Concatenation order (display_name → name → category → archetype '
    '→ description) drives similarity weighting; do NOT reorder '
    'without updating the matching SELECT expression in '
    'app/services/discovery_search.py (the expression MUST be '
    'byte-identical for the planner to pick this index up).';
