-- Creator earnings: tracks per-influencer revenue from user subscriptions/purchases.
-- Aggregated from billing.yral.com events (webhook or periodic sync).

CREATE TABLE IF NOT EXISTS creator_earnings (
    id VARCHAR(255) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    influencer_id VARCHAR(255) NOT NULL REFERENCES ai_influencers(id) ON DELETE CASCADE,
    creator_id VARCHAR(255) NOT NULL,
    amount_cents INTEGER NOT NULL DEFAULT 0,
    currency VARCHAR(10) DEFAULT 'USD',
    source VARCHAR(50) NOT NULL,
    period_start TIMESTAMP NOT NULL,
    period_end TIMESTAMP NOT NULL,
    status VARCHAR(20) DEFAULT 'pending'
        CHECK (status IN ('pending', 'confirmed', 'paid_out')),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_earnings_creator ON creator_earnings(creator_id);
CREATE INDEX IF NOT EXISTS idx_earnings_influencer ON creator_earnings(influencer_id);
CREATE INDEX IF NOT EXISTS idx_earnings_period ON creator_earnings(period_start, period_end);
