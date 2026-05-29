-- Task B / Cutover-readiness: continuous incremental ETL from chat-ai.
--
-- One row per table we sync. last_sync_ts is the cursor: the next ETL pass
-- pulls source rows with created_at > last_sync_ts. last_run_at + last_error
-- + last_runtime_ms are diagnostics for /admin/etl-status (planned) and the
-- Task C integrity verifier.

CREATE TABLE IF NOT EXISTS etl_sync_state (
    table_name VARCHAR(50) PRIMARY KEY,
    last_sync_ts TIMESTAMP NOT NULL DEFAULT '1970-01-01'::timestamp,
    last_run_at TIMESTAMP,
    rows_pulled_total BIGINT NOT NULL DEFAULT 0,
    rows_pulled_last_run INT NOT NULL DEFAULT 0,
    last_error TEXT,
    last_runtime_ms INT,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Seed the three tables we sync (ai_influencers, conversations, messages).
-- Existing v2 data was loaded via the snapshot ETL on Day 9 — the initial
-- last_sync_ts of epoch (1970-01-01) plus an ON CONFLICT DO NOTHING insert
-- pattern means the first ETL pass will pull "everything newer than epoch"
-- but ON CONFLICT skips rows that already exist, so the first run becomes
-- a backfill of anything created since the Day 9 snapshot.
INSERT INTO etl_sync_state (table_name) VALUES
    ('ai_influencers'),
    ('conversations'),
    ('messages')
ON CONFLICT (table_name) DO NOTHING;
