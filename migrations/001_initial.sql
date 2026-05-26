-- Schema ported from yral-chat-ai. Same tables, same indexes, same triggers.

CREATE TABLE IF NOT EXISTS ai_influencers (
    id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) UNIQUE NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    avatar_url TEXT,
    description TEXT,
    category VARCHAR(100),
    system_instructions TEXT NOT NULL,
    personality_traits JSONB DEFAULT '{}',
    initial_greeting TEXT,
    suggested_messages JSONB DEFAULT '[]',
    is_active VARCHAR(20) DEFAULT 'active'
        CHECK (is_active IN ('active', 'coming_soon', 'discontinued')),
    is_nsfw BOOLEAN DEFAULT FALSE,
    parent_principal_id VARCHAR(255),
    source VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_influencers_name ON ai_influencers(name);
CREATE INDEX IF NOT EXISTS idx_influencers_category ON ai_influencers(category);
CREATE INDEX IF NOT EXISTS idx_influencers_active ON ai_influencers(is_active);
CREATE INDEX IF NOT EXISTS idx_influencers_nsfw ON ai_influencers(is_nsfw);
CREATE INDEX IF NOT EXISTS idx_influencers_active_nsfw ON ai_influencers(is_active, is_nsfw);
CREATE INDEX IF NOT EXISTS idx_influencers_parent_principal ON ai_influencers(parent_principal_id);

CREATE TABLE IF NOT EXISTS conversations (
    id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    influencer_id VARCHAR(255) REFERENCES ai_influencers(id) ON DELETE CASCADE,
    conversation_type VARCHAR(20) NOT NULL DEFAULT 'ai_chat'
        CHECK (conversation_type IN ('ai_chat', 'human_chat')),
    participant_b_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_influencer_id ON conversations(influencer_id);
CREATE INDEX IF NOT EXISTS idx_conversations_type ON conversations(conversation_type);
CREATE INDEX IF NOT EXISTS idx_conversations_participant_b ON conversations(participant_b_id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_user_influencer
    ON conversations(user_id, influencer_id) WHERE influencer_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_human_chat
    ON conversations(user_id, participant_b_id)
    WHERE conversation_type = 'human_chat' AND participant_b_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS messages (
    id VARCHAR(255) PRIMARY KEY,
    conversation_id VARCHAR(255) NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant')),
    sender_id VARCHAR(255),
    content TEXT,
    message_type VARCHAR(20) NOT NULL
        CHECK (message_type IN ('text', 'multimodal', 'image', 'audio')),
    media_urls JSONB DEFAULT '[]',
    audio_url TEXT,
    audio_duration_seconds INTEGER,
    token_count INTEGER,
    client_message_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    metadata JSONB DEFAULT '{}',
    status VARCHAR(20) DEFAULT 'delivered',
    is_read BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
    ON messages(conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_unread
    ON messages(conversation_id, role, is_read);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_conversation_client_id
    ON messages(conversation_id, client_message_id)
    WHERE client_message_id IS NOT NULL;

-- Auto-update conversations.updated_at on new message
CREATE OR REPLACE FUNCTION update_conversation_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE conversations SET updated_at = NOW() WHERE id = NEW.conversation_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_conversation_timestamp ON messages;
CREATE TRIGGER trigger_update_conversation_timestamp
AFTER INSERT ON messages
FOR EACH ROW EXECUTE FUNCTION update_conversation_timestamp();

-- Auto-update ai_influencers.updated_at on modification
CREATE OR REPLACE FUNCTION update_influencer_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_influencer_timestamp ON ai_influencers;
CREATE TRIGGER trigger_update_influencer_timestamp
BEFORE UPDATE ON ai_influencers
FOR EACH ROW EXECUTE FUNCTION update_influencer_timestamp();
