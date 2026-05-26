-- Materialized view backing /influencers/trending.
-- Refreshed every 15 min by background task in main.py.
-- Replaced a correlated-subquery implementation that had P95 of ~6.7s on 3M+ messages.

CREATE MATERIALIZED VIEW IF NOT EXISTS influencer_trending_stats AS
SELECT
    i.id                                              AS influencer_id,
    COUNT(DISTINCT c.id)                              AS conversation_count,
    COUNT(m.id) FILTER (WHERE m.role = 'user')        AS message_count
FROM ai_influencers i
LEFT JOIN conversations c ON c.influencer_id = i.id
LEFT JOIN messages m      ON m.conversation_id = c.id
GROUP BY i.id
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_influencer_trending_stats_id
    ON influencer_trending_stats(influencer_id);

CREATE INDEX IF NOT EXISTS idx_influencer_trending_stats_msg_count
    ON influencer_trending_stats(message_count DESC, influencer_id);
