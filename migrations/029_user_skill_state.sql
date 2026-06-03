-- Phase 23.1 — per-(user, influencer) state for skilled influencers.
--
-- One row per (user_id, influencer_id) pair when that influencer has a
-- skill_slug assigned (see migration 030). The setup half of state is
-- collected during first-turn onboarding (`SKILLS[slug]['onboarding_prompt']`)
-- and rarely changes. The runtime half is mutated by the proactive
-- engagement loop + chat handler as the user engages.
--
-- Why nullable next_event_at: not all skills are time-driven. V1 is
-- (nutrition_coach scheduled_checkin); future skills like travel_advisor
-- are event-driven and leave next_event_at NULL.
--
-- Apply manually after pg_dump per Rule 9. Rishi's standing approval
-- covers the snapshot. The 23.1 application code handles missing-table
-- gracefully (the routes / repo wrap their queries in try/except so the
-- service doesn't 500 if migrations 029/030 haven't been applied yet).

CREATE TABLE IF NOT EXISTS user_skill_state (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       TEXT NOT NULL,
    influencer_id TEXT NOT NULL REFERENCES ai_influencers(id) ON DELETE CASCADE,
    -- Denormalized from ai_influencers.skill_slug for query speed (the
    -- proactive loop filters by skill type without joining).
    skill_slug    TEXT NOT NULL,
    -- Skill-defined shape. Convention (NOT DB-enforced): state is split
    -- into a "setup" sub-object (onboarding answers, rarely changes) and
    -- a "runtime" sub-object (mutated as the user engages). Every skill
    -- follows the same split so the JSONB doesn't become a junk drawer.
    state         JSONB NOT NULL DEFAULT '{}',
    -- When the next proactive event (check-in, briefing, etc.) fires.
    -- NULL when status != 'active' OR when the skill is event-driven.
    next_event_at TIMESTAMPTZ,
    last_event_at TIMESTAMPTZ,
    -- active | paused | done | onboarding_partial (per design doc
    -- "First-turn onboarding" section — partial extraction stores what
    -- parsed cleanly and asks for the rest on the next turn).
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (user_id, influencer_id)
);

-- Engagement loop's hot query: "what scheduled events are due now?"
-- Partial index keeps it small (only active rows have a real next_event_at).
CREATE INDEX IF NOT EXISTS idx_user_skill_state_due
    ON user_skill_state (next_event_at)
    WHERE status = 'active' AND next_event_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_user_skill_state_user_influencer
    ON user_skill_state (user_id, influencer_id);

CREATE INDEX IF NOT EXISTS idx_user_skill_state_skill_slug
    ON user_skill_state (skill_slug);

COMMENT ON TABLE user_skill_state IS
    'Phase 23.1 — per-(user, influencer) structured state for skilled influencers. setup/runtime split lives in the state JSONB.';
