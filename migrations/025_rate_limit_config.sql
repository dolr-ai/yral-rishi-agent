-- Phase 19.1 — hot-editable rate-limit configuration.
--
-- Per memory feedback-adhd-observability-and-security-baseline:
-- knobs must be hot-editable via admin endpoint, NOT just env vars.
-- The middleware reads from Redis on the request path (fast); writes
-- come in via PUT /admin/rate-limits/config which updates BOTH this
-- table (durable across restarts) AND Redis (so all replicas see the
-- new value immediately, no rolling restart needed).
--
-- Seed values intentionally generous — 60 req/min + 1000 req/hour is
-- well above normal chat usage (a busy user sends maybe 30 msgs/hour)
-- but stops obvious abuse (scraper bot, runaway loop) cold. Rishi can
-- tighten from the admin endpoint after watching dashboards for a week.

CREATE TABLE IF NOT EXISTS rate_limit_config (
    key TEXT PRIMARY KEY,
    value INT NOT NULL CHECK (value > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
);

INSERT INTO rate_limit_config (key, value) VALUES
    ('per_user_per_min', 60),
    ('per_user_per_hour', 1000),
    ('per_ip_per_min', 30),
    ('per_ip_per_hour', 500)
ON CONFLICT (key) DO NOTHING;
