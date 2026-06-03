-- Phase 25.5 — per-call LLM cost recording.
--
-- One row per llm_registry.call / call_stream / call_transcribe that
-- successfully returns. The 4 Phase 25 questions / 5 open questions
-- (Q4 specifically) settled on:
--
--   - Per-user daily cap counts ACROSS providers (no per-provider caps).
--   - Two cost bases: 'real' (Gemini / OpenAI / OpenRouter — actual $
--     to a vendor) and 'synthetic' (internal_vllm / ollama — compute
--     share priced for fair-use accounting at $0.00005/1k tokens, NOT
--     real money). Dashboard splits "real $ spent" vs "compute share
--     consumed" by filtering this column.
--   - per-1k-token rates live in PROVIDERS dict in app/services/llm_registry.py.
--     This table just records the computed $ at write time so reports
--     stay correct even if rates change later.
--
-- Apply manually after pg_dump per Rule 9 — Rishi's standing approval
-- covers the snapshot for 25.5. The 25.5 code handles missing-table
-- gracefully (catches asyncpg errors, logs warning, continues) so the
-- deploy is safe pre-migration; recording starts as soon as the table
-- appears.

CREATE TABLE IF NOT EXISTS llm_costs (
    id BIGSERIAL PRIMARY KEY,
    process VARCHAR(64) NOT NULL,
    provider VARCHAR(32) NOT NULL,
    model VARCHAR(128) NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12, 8) NOT NULL DEFAULT 0,
    -- 'real' (vendor $) or 'synthetic' (compute share, internal_vllm)
    cost_basis VARCHAR(16) NOT NULL,
    -- Nullable attribution columns. Set when the caller knows them;
    -- background loops without a clear user owner leave them NULL.
    user_id VARCHAR(128),
    conversation_id VARCHAR(128),
    request_id VARCHAR(64),
    -- LlmResponse.latency_ms — useful for "is internal_vllm getting faster?"
    -- dashboard charts.
    latency_ms NUMERIC(10, 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Dashboard tile (Phase 19.6 + 25.4 update) reads "today's spend" by
-- (created_at, cost_basis) and "per-user today" by (user_id, created_at).
-- Indexes match those access patterns.
CREATE INDEX IF NOT EXISTS idx_llm_costs_created_at
    ON llm_costs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_costs_user_created
    ON llm_costs (user_id, created_at DESC)
    WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_llm_costs_basis_created
    ON llm_costs (cost_basis, created_at DESC);

COMMENT ON TABLE llm_costs IS
    'Phase 25.5 — per-call LLM cost record. One row per successful registry call. cost_basis splits real vendor $ from synthetic compute share (internal_vllm).';
