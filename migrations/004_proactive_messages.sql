-- Proactive message scheduling: bots can text first.
-- Tracks scheduled messages and their delivery status.

CREATE TABLE IF NOT EXISTS proactive_messages (
    id VARCHAR(255) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    influencer_id VARCHAR(255) NOT NULL REFERENCES ai_influencers(id) ON DELETE CASCADE,
    user_id VARCHAR(255) NOT NULL,
    conversation_id VARCHAR(255) REFERENCES conversations(id) ON DELETE CASCADE,
    trigger_type VARCHAR(50) NOT NULL,
    scheduled_at TIMESTAMP NOT NULL,
    delivered_at TIMESTAMP,
    status VARCHAR(20) DEFAULT 'pending'
        CHECK (status IN ('pending', 'delivered', 'failed', 'cancelled')),
    message_content TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_proactive_pending
    ON proactive_messages(status, scheduled_at)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_proactive_user
    ON proactive_messages(user_id, influencer_id);
