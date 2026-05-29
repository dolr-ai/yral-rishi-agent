-- Task 4 / Phase 7.5: Soul File Coach.
-- Creators chat with an AI coach that proposes targeted changes to their
-- bot's system_instructions. Three tables:
--   coach_conversations — one per (creator, bot, session)
--   coach_messages — turn-by-turn history within a coach session
--   system_instructions_history — audit trail for applied changes (rollback)

CREATE TABLE IF NOT EXISTS coach_conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_user_id VARCHAR(255) NOT NULL,
    bot_id VARCHAR(255) NOT NULL REFERENCES ai_influencers(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_coach_conv_creator
    ON coach_conversations(creator_user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_coach_conv_bot
    ON coach_conversations(bot_id, created_at DESC);

CREATE TABLE IF NOT EXISTS coach_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coach_conversation_id UUID NOT NULL
        REFERENCES coach_conversations(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('creator', 'coach')),
    content TEXT NOT NULL,
    -- proposed_changes = the coach's suggested new system_instructions text
    -- (NULL for creator messages and for coach clarifying-question turns)
    proposed_changes TEXT,
    -- reasoning = coach's explanation of why the change improves the bot
    reasoning TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_coach_messages_conv
    ON coach_messages(coach_conversation_id, created_at);

CREATE TABLE IF NOT EXISTS system_instructions_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id VARCHAR(255) NOT NULL REFERENCES ai_influencers(id) ON DELETE CASCADE,
    coach_conversation_id UUID REFERENCES coach_conversations(id),
    coach_message_id UUID REFERENCES coach_messages(id),
    previous_instructions TEXT NOT NULL,
    new_instructions TEXT NOT NULL,
    applied_by VARCHAR(255) NOT NULL,
    applied_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sih_bot
    ON system_instructions_history(bot_id, applied_at DESC);
