-- Phase 23.1 — add skill_slug to ai_influencers.
--
-- Nullable: most influencers have no skill (companion archetypes etc.).
-- The few that do (Kareena gets `nutrition_coach` in 23.7) reference
-- one row in `app/services/skills.py:SKILLS` by string slug.
--
-- Why a string column instead of an FK: the SKILLS catalog lives in
-- Python for V1 (per design doc — converts to a `skills` table when
-- creators edit skills via the Soul File Coach, not before). FK to a
-- non-existent table is impossible; we'd add the FK constraint at the
-- same time we add the table. For V1, string slug + application-level
-- validation is the symmetric move.
--
-- Apply manually after pg_dump per Rule 9.

ALTER TABLE ai_influencers
    ADD COLUMN IF NOT EXISTS skill_slug TEXT;

-- Hot query for the proactive loop's skill-aware fetches.
CREATE INDEX IF NOT EXISTS idx_ai_influencers_skill_slug
    ON ai_influencers (skill_slug)
    WHERE skill_slug IS NOT NULL;

COMMENT ON COLUMN ai_influencers.skill_slug IS
    'Phase 23.1 — slug into app/services/skills.py:SKILLS. NULL = no skill (default for companion archetypes). One skill per influencer in V1.';
