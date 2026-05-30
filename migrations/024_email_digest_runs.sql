-- Phase 24.5 — daily email digest history.
--
-- One row per digest build. The cron loop in app/services/email_digest.py
-- inserts here every day at 08:00 IST. The history is bounded at
-- DIGEST_HISTORY_KEEP (30) rows — older runs are trimmed at write time.
--
-- The preview endpoint (GET /admin/email-digest/preview) reads from
-- here so Rishi can browse recent runs without needing email access.

CREATE TABLE IF NOT EXISTS email_digest_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rendered_at TIMESTAMPTZ NOT NULL,
    for_date TEXT NOT NULL,
    body_json JSONB NOT NULL,
    sent BOOLEAN NOT NULL DEFAULT FALSE,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_email_digest_runs_rendered_at
    ON email_digest_runs (rendered_at DESC);

CREATE INDEX IF NOT EXISTS idx_email_digest_runs_for_date
    ON email_digest_runs (for_date);
