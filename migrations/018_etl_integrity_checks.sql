-- Task C / Cutover-readiness: integrity checks recorded by the hourly
-- verifier. One row per check per pass; keeps history so we can chart drift.
--
-- Three check types:
--   'row_count'             — chat_ai vs v2 row count per table; warn if
--                             diff > MAX_DRIFT_ROWS rows
--   'sample_conversations'  — pick N random recent chat-ai conversations,
--                             verify every message is in v2 with matching
--                             content + created_at + message_type
--   'fk_integrity'          — v2-side: count conversations missing
--                             influencer_id, messages missing
--                             conversation_id

CREATE TABLE IF NOT EXISTS etl_integrity_checks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    check_type VARCHAR(50) NOT NULL,
    table_name VARCHAR(50),
    chat_ai_count BIGINT,
    v2_count BIGINT,
    diff BIGINT,
    details JSONB,
    status VARCHAR(20) NOT NULL CHECK (status IN ('pass', 'warn', 'fail')),
    runtime_ms INT,
    checked_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_eic_recent
    ON etl_integrity_checks (checked_at DESC);

CREATE INDEX IF NOT EXISTS idx_eic_type_recent
    ON etl_integrity_checks (check_type, checked_at DESC);
