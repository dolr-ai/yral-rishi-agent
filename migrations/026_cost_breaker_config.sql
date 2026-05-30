-- Phase 19.2 — runaway cost circuit breaker config + audit log.
--
-- The protection: cap each user's daily LLM spend. Run-once-and-then-
-- some prompts (jailbreak loops, prompt injection making the model
-- spew, accidental client retry storms) can rack up real money very
-- fast. Before each LLM call we check the user's day-to-date spend
-- against the cap; if over → 429 with a "daily cap reached" payload
-- instead of issuing the call.
--
-- Per memory feedback-adhd-observability-and-security-baseline: cap
-- is hot-editable via admin endpoint. PUT writes BOTH this table AND
-- Redis so all replicas see new values immediately.
--
-- Two config rows seeded (USD cents):
--   per_user_daily_cents — default $1.00/day (= 100 cents). Enough for
--     normal use (a busy user uses ~5-10 cents/day at Gemini Flash
--     prices), well under the level where a single user's runaway
--     hits real money.
--   per_user_daily_alert_cents — Sentry alert when crossed even before
--     the hard cap. Lets ops investigate emerging hotspots.

CREATE TABLE IF NOT EXISTS cost_breaker_config (
    key TEXT PRIMARY KEY,
    value_cents BIGINT NOT NULL CHECK (value_cents > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
);

INSERT INTO cost_breaker_config (key, value_cents) VALUES
    ('per_user_daily_cents', 100),
    ('per_user_daily_alert_cents', 50)
ON CONFLICT (key) DO NOTHING;

-- Per-user-per-day spend log. We could compute from Redis alone but a
-- durable record helps with: (a) post-mortem on a runaway spend, (b)
-- end-of-month billing reconciliation, (c) restoring counters when
-- Redis is restarted/flushed.
CREATE TABLE IF NOT EXISTS llm_cost_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    day DATE NOT NULL,
    model TEXT NOT NULL,
    input_tokens INT NOT NULL,
    output_tokens INT NOT NULL,
    cost_cents NUMERIC(12, 4) NOT NULL,
    request_id TEXT,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_cost_log_user_day
    ON llm_cost_log (user_id, day);

CREATE INDEX IF NOT EXISTS idx_llm_cost_log_day
    ON llm_cost_log (day);

-- Trip events log — one row per circuit-breaker rejection so the
-- dashboard can show "who got blocked when". Bounded by trim-at-write
-- in the application code (keep last 1000).
CREATE TABLE IF NOT EXISTS cost_breaker_trips (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    day DATE NOT NULL,
    spent_cents NUMERIC(12, 4) NOT NULL,
    cap_cents BIGINT NOT NULL,
    tripped_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cost_breaker_trips_tripped_at
    ON cost_breaker_trips (tripped_at DESC);
