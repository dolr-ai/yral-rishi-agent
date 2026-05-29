-- Phase 2 of the S3-based ETL pivot. One row per S3 file the V2 fetcher
-- has applied. Filename is the natural PK — same file applied twice is a
-- no-op via ON CONFLICT.
--
-- rishi-1's exporter writes files at
--   s3://rishi-yral/yral-chat-ai/incremental-sync/<YYYYMMDDTHHMMSSZ>_<table>.csv.gz
-- so filename is unique by construction (1-sec resolution + per-table suffix).

CREATE TABLE IF NOT EXISTS etl_processed_files (
    filename TEXT PRIMARY KEY,
    table_name VARCHAR(50) NOT NULL,
    rows_applied INT NOT NULL,
    rows_in_file INT NOT NULL,
    file_etag TEXT,
    s3_metadata JSONB,
    processed_at TIMESTAMP NOT NULL DEFAULT NOW(),
    runtime_ms INT
);

CREATE INDEX IF NOT EXISTS idx_epf_processed_at
    ON etl_processed_files (processed_at DESC);

CREATE INDEX IF NOT EXISTS idx_epf_table_processed_at
    ON etl_processed_files (table_name, processed_at DESC);
