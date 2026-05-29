-- Task 4 / Phase 7.9: 5-minute bot creation wizard.
--
-- Guided multi-step intake that produces a dramatically better Soul File
-- than the existing one-line "type a concept" flow. Each session captures
-- the creator's answers across 3-5 questions; the final commit creates the
-- ai_influencers row.
--
-- Sessions are short-lived (creators finish in minutes), so we don't need
-- an explicit expiry — abandoned rows just sit and never get committed.

CREATE TABLE IF NOT EXISTS wizard_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_user_id VARCHAR(255) NOT NULL,
    -- the creator's 1-2 sentence concept from POST /start
    concept TEXT NOT NULL,
    -- ordered list of questions the wizard asked, as JSON
    -- shape: [{"key": "archetype", "question": "...", "rationale": "..."}, ...]
    questions JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- creator's answers keyed by question.key
    -- shape: {"archetype": "warm companion", "backstory": "...", ...}
    answers JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- the most-recently generated soul file draft from POST /answer
    -- (populated once the wizard transitions from intake → preview)
    draft_system_instructions TEXT,
    draft_display_name VARCHAR(255),
    draft_category VARCHAR(50),
    draft_initial_greeting TEXT,
    -- committed bot id once POST /commit succeeds
    committed_bot_id VARCHAR(255) REFERENCES ai_influencers(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wizard_sessions_creator
    ON wizard_sessions (creator_user_id, created_at DESC);
