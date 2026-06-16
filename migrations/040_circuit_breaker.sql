-- Phase 21α.B6 — cost circuit breaker config + event log tables.
--
-- Two additive tables + one performance index on the existing
-- llm_costs table:
--
--   circuit_breaker_config — hot-editable knobs (mirrors the proven
--     rate_limit_config pattern from Phase 19.1). A single SQL UPDATE
--     disables B6 in 1 second. Cache TTL is 60s so changes propagate
--     fast even without a deploy.
--   circuit_breaker_events  — shadow-mode log + enforce-mode audit
--     trail. Every "would have tripped" lands here whether shadow or
--     enforce. The 7-day shadow-review queries this.
--   idx_llm_costs_user_recent — partial index keeping the per-user
--     daily aggregation sub-millisecond.
--
-- Seed rows ship the breaker in DEFAULT-OPEN state:
--   b6_enabled='false' AND b6_enforce='false'. Code is dormant until
--   Rishi flips b6_enabled='true' via SQL UPDATE (then shadow mode);
--   enforce-flip requires Sarvesh's mobile-503-confirmation per the
--   2026-06-16 brief.
--
-- Rule 9: pg_dump before applying. The auto-pg_dump runner (PR #309)
-- handles it — 6 clean dumps this week from migrations 033-039 prove
-- the path.
--
-- Safety: all changes additive — no ALTER on populated tables, no
-- DROP, no DEFAULT on a populated column. Migration alone changes
-- ZERO runtime behaviour because b6_enabled='false' is the seed.

-- squawk: cap lock-wait + statement duration per I-Mig2 rule (#340).
-- Same 3s/60s as 033 + 034 + 035 + 036 + 038 + 039.
SET lock_timeout = '3s';
SET statement_timeout = '60s';

-- ─── 1. hot-edit config table ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS circuit_breaker_config (
    key         VARCHAR(64)  PRIMARY KEY,
    value       TEXT         NOT NULL,
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by  VARCHAR(255) NOT NULL DEFAULT 'system'
);

COMMENT ON TABLE circuit_breaker_config IS
    'Phase 21α.B6 — hot-editable config for the cost circuit breaker. '
    'Mirrors rate_limit_config pattern. A single UPDATE on b6_enabled '
    'disables B6 in 1 second; Redis cache TTL 60s so live workers '
    'pick up changes within a minute even without restart.';

-- Default-open seed: B6 ships DORMANT. Flipping b6_enabled='true'
-- activates shadow mode. Enforce-flip is a separate UPDATE on
-- b6_enforce='true' — gated on Sarvesh mobile 503 confirmation per
-- 2026-06-16 brief Q2.
INSERT INTO circuit_breaker_config (key, value, updated_by) VALUES
    ('b6_enabled',                  'false', 'migration-040'),
    ('b6_enforce',                  'false', 'migration-040'),
    ('b6_per_user_daily_usd',       '1.0',   'migration-040'),
    ('b6_global_hourly_usd',        '20.0',  'migration-040'),
    ('b6_process_allowlist',        '',      'migration-040'),
    ('b6_cache_ttl_sec',            '10',    'migration-040'),
    ('b6_response_retry_after_sec', '3600',  'migration-040'),
    -- YRAL-team CSV (Q3). Code default seeds Rishi's principal;
    -- Rishi can hot-edit add Sarvesh/Saikat/Neha via SQL UPDATE
    -- once their principals are known. Shadow-mode trip count
    -- on these principal_ids MUST be zero before enforce-flip.
    ('b6_yral_team_principal_ids',
     'k2adj-ox4zs-gaocq-d5ctl-ggx5k-ekucz-rvgnv-4pddz-mkjzc-es4cj-aae',
     'migration-040')
ON CONFLICT (key) DO NOTHING;

-- ─── 2. shadow-mode + enforce-mode event log ──────────────────────────

CREATE TABLE IF NOT EXISTS circuit_breaker_events (
    id             BIGSERIAL    PRIMARY KEY,
    occurred_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    user_id        VARCHAR(255),
    process        VARCHAR(64),
    provider       VARCHAR(32),
    scope          VARCHAR(32)  NOT NULL
                   CHECK (scope IN ('per_user_daily', 'global_hourly')),
    cost_seen_usd  NUMERIC(10, 4) NOT NULL,
    threshold_usd  NUMERIC(10, 4) NOT NULL,
    enforce_mode   BOOLEAN      NOT NULL,
    call_blocked   BOOLEAN      NOT NULL
);

COMMENT ON TABLE circuit_breaker_events IS
    'Phase 21α.B6 — one row per breaker trip. enforce_mode=false rows '
    'are shadow logs (call still ran). enforce_mode=true + '
    'call_blocked=true rows are real blocks. 7-day shadow review '
    'queries this filtered by occurred_at + scope + user_id.';

CREATE INDEX IF NOT EXISTS idx_cb_events_recent
    ON circuit_breaker_events (occurred_at DESC);

-- ─── 3. performance index for per-user-daily aggregation ──────────────
--
-- The B6 check reads `SELECT SUM(cost_usd) FROM llm_costs WHERE user_id
-- = $1 AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC')`.
-- Partial index keeps it slim (only rows with a user_id matter; a lot
-- of background calls have user_id=NULL) and the (user_id, created_at
-- DESC) order matches the query's WHERE + makes it an index-only scan.
CREATE INDEX IF NOT EXISTS idx_llm_costs_user_recent
    ON llm_costs (user_id, created_at DESC)
    WHERE user_id IS NOT NULL;
