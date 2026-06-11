-- Phase 21αβ.H10 — backup_drill_runs audit table.
--
-- Surfaces "when did we last prove restore works?" as queryable state
-- so the /admin/backup-health dashboard can render a real timestamp +
-- pass/fail badge instead of guessing. The walg_restore_drill.sh
-- script writes one row on START + UPDATEs the same row on FINISH,
-- so a drill that hangs mid-restore is visible too (finished_at NULL).
--
-- The tile reads MAX(finished_at) WHERE drill_type='walg_restore' +
-- the most recent row to render its pass/fail badge.
--
-- Schema:
--   id              UUID PK
--   drill_type      'walg_restore' (today) — extensible
--   started_at      NOT NULL
--   finished_at     NULL while running
--   exit_code       NULL while running; 0 on PASS, 1-5 per drill
--                   script's documented codes
--   triggered_by    'cron'/'workflow:walg-drill'/'manual:rishi' etc.
--   sanity_results  per-table counts as JSONB (matches drill output)
--   notes           short free-text (e.g. error message tail)
--
-- Same SET lock_timeout/statement_timeout preamble as 033 + 034 + 035
-- per the squawk I-Mig2 rule (#340). This migration only creates a
-- new (empty) table — 3s/60s is conservative.

SET lock_timeout = '3s';
SET statement_timeout = '60s';

CREATE TABLE IF NOT EXISTS backup_drill_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drill_type VARCHAR(32) NOT NULL
        CHECK (drill_type IN ('walg_restore', 'pg_dump_restore')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    exit_code BIGINT,
    triggered_by VARCHAR(255),
    sanity_results JSONB,
    notes TEXT
);

-- Tile query: latest run per drill_type. Partial index over the
-- recent slice keeps the lookup O(log recent) even as the table
-- grows to thousands of rows over the cluster's lifetime.
CREATE INDEX IF NOT EXISTS idx_backup_drill_runs_recent
    ON backup_drill_runs (drill_type, started_at DESC);

COMMENT ON TABLE backup_drill_runs IS
    'Phase 21αβ.H10 — audit row per restore-drill run. Surfaces "last '
    'time we proved restore works" to /admin/backup-health. Written by '
    'scripts/walg_restore_drill.sh; read by app/routes/backup_health_admin.py.';
