-- Phase 25.5b — outcome + error tracking on llm_costs.
--
-- Originally llm_costs only recorded successful calls (cost > 0). Rishi
-- wants per-day rejection rate + top error messages + latency p50/p95/p99
-- so Anshuman can use the data to improve internal_vllm's self-hosted
-- endpoint. Add outcome + error_message columns and record FAILED calls
-- too (cost_usd = 0, error_message populated).
--
-- Apply manually after pg_dump per Rule 9 — Rishi's standing approval
-- covers the snapshot for 25.5b. The 25.5b code handles missing-columns
-- gracefully (uses INSERT with the new cols; fails the INSERT only if
-- the cols don't exist, which falls through to the existing
-- best-effort try/except — recording is dropped but the LLM call still
-- returns normally).

ALTER TABLE llm_costs
    ADD COLUMN IF NOT EXISTS outcome VARCHAR(32) NOT NULL DEFAULT 'success',
    ADD COLUMN IF NOT EXISTS error_message TEXT;

-- Backfill: every existing row was written from the success path before
-- this migration, so 'success' is the correct default. ADD COLUMN ...
-- NOT NULL DEFAULT 'success' handled it; verify explicitly below.

-- Dashboard queries: per-day rejection rate per process is the load-bearing
-- query. Pre-index it.
CREATE INDEX IF NOT EXISTS idx_llm_costs_outcome_created
    ON llm_costs (outcome, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_costs_process_outcome
    ON llm_costs (process, outcome, created_at DESC);

COMMENT ON COLUMN llm_costs.outcome IS
    'Phase 25.5b — one of: success / rate_limit / server_error / timeout / parse_error / blocked / other. Drives the dashboard rejection-rate tile + Anshuman feedback loop.';
COMMENT ON COLUMN llm_costs.error_message IS
    'Phase 25.5b — exception text on failure, truncated to 500 chars. NULL on success.';
