-- Task 1 / Phase 7.7: bot quality scorer.
--
-- Nightly background job scores each AI influencer by sampling its recent
-- conversations and running Gemini-as-judge on a handful of turn pairs.
-- Results stored as a history table — most recent row per bot is the
-- "current" score; older rows let us chart quality drift.

CREATE TABLE IF NOT EXISTS bot_quality_scores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id VARCHAR(255) NOT NULL REFERENCES ai_influencers(id) ON DELETE CASCADE,
    score_overall REAL NOT NULL,
    score_in_character REAL NOT NULL,
    score_response_quality REAL NOT NULL,
    score_engagement REAL NOT NULL,
    -- how many distinct conversations were sampled (target = 20)
    last_n_conversations INT NOT NULL,
    -- how many turn pairs actually scored (some may have failed JSON parse)
    sample_size INT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Latest-per-bot lookup for the endpoint + coach integration
CREATE INDEX IF NOT EXISTS idx_bqs_bot_recent
    ON bot_quality_scores (bot_id, created_at DESC);
