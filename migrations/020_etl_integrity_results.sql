-- Phase 3 of the S3 ETL pivot. rishi-1 emits integrity snapshots to S3
-- (tick / hourly / sample / sentinel — see scripts/incremental_export.py
-- and app/services/etl_integrity.py). V2 verifies each against its own
-- DB and records the result here.
--
-- Layers:
--   'tick'      — every 5 min:    per-table max_created_at + rows_in_tick
--                                 vs. V2's etl_processed_files in the
--                                 same window
--   'hourly'    — every 1h:       full row counts where
--                                 created_at < NOW() - 10 min
--                                 vs. V2's own count
--   'sample'    — every 6h:       20 random conversations + full column
--                                 compare + per-message sha256 compare
--   'sentinel'  — every 30 min:   chat-ai's latest_message_id exists in
--                                 V2 within 10 min of retries
--
-- One row per snapshot file consumed. snapshot_filename UNIQUE so
-- re-applying the same payload is a no-op.

CREATE TABLE IF NOT EXISTS etl_integrity_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    layer VARCHAR(20) NOT NULL CHECK (layer IN ('tick', 'hourly', 'sample', 'sentinel')),
    snapshot_filename TEXT NOT NULL UNIQUE,
    snapshot_iso TIMESTAMPTZ NOT NULL,
    passed BOOLEAN NOT NULL,
    drift_count INT NOT NULL DEFAULT 0,
    details JSONB,
    runtime_ms INT,
    verified_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_eir_layer_verified
    ON etl_integrity_results (layer, verified_at DESC);

CREATE INDEX IF NOT EXISTS idx_eir_failures
    ON etl_integrity_results (verified_at DESC)
    WHERE passed = false;

CREATE INDEX IF NOT EXISTS idx_eir_snapshot_iso
    ON etl_integrity_results (snapshot_iso DESC);
