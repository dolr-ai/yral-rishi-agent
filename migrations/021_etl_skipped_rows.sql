-- Option A of the chat-ai → V2 ETL: skip duplicate/orphaned rows with audit.
--
-- chat-ai allows multiple conversations per (user_id, influencer_id) pair;
-- V2's schema enforces at most one via idx_unique_user_influencer
-- (UNIQUE WHERE influencer_id IS NOT NULL). When ETL re-imports a
-- chat-ai conversation whose (user, influencer) pair already exists in V2,
-- the row is skipped — V2's existing conversation stays canonical (matches
-- V2's one-active-conversation-per-pair UX).
--
-- Messages inherit the FK problem: if their parent conversation didn't
-- land in V2 (either skipped above or never existed), the message
-- can't insert either. Those rows are also recorded here as 'orphan'.
--
-- Reasons:
--   'conflict' — row would violate a UNIQUE constraint
--   'orphan'   — row references a parent that doesn't exist in V2
--
-- The composite UNIQUE makes re-processing the same file idempotent:
-- re-applying the same CSV produces the same skip log without duplicates.

CREATE TABLE IF NOT EXISTS etl_skipped_rows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename TEXT NOT NULL,
    table_name VARCHAR(50) NOT NULL,
    row_id TEXT NOT NULL,
    reason VARCHAR(20) NOT NULL CHECK (reason IN ('conflict', 'orphan')),
    skipped_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (filename, table_name, row_id, reason)
);

CREATE INDEX IF NOT EXISTS idx_esr_skipped_at
    ON etl_skipped_rows (skipped_at DESC);

CREATE INDEX IF NOT EXISTS idx_esr_reason_recent
    ON etl_skipped_rows (reason, skipped_at DESC);

CREATE INDEX IF NOT EXISTS idx_esr_table
    ON etl_skipped_rows (table_name, skipped_at DESC);
