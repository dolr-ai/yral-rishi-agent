-- Phase 25.4 — DB-backed hot-overrides for llm_registry routing.
--
-- Per docs/PHASE-25-DESIGN.md (Q3 + admin endpoint scope):
--   Default config lives in code (LLM_DEFAULTS in llm_registry.py).
--   Production overrides land here. PATCH /admin/llm-routing writes a
--   row; the registry reloads on demand. No redeploy needed to switch
--   a process from gemini → openrouter or change models.
--
-- Apply manually after pg_dump per Rule 9 (don't auto-run on container
-- startup — Rishi reserves the schema-change step). The 25.4 application
-- code uses the table when present and falls back to env/defaults when
-- absent, so deploying the code before the table is safe.

CREATE TABLE IF NOT EXISTS llm_process_config (
    process VARCHAR(64) PRIMARY KEY,
    provider VARCHAR(32) NOT NULL,
    model VARCHAR(128) NOT NULL,
    timeout_sec NUMERIC(5, 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Audit trail — who toggled the routing. Principal ID from the JWT
    -- on the PATCH request.
    updated_by VARCHAR(128)
);

CREATE INDEX IF NOT EXISTS idx_llm_process_config_updated_at
    ON llm_process_config (updated_at DESC);

COMMENT ON TABLE llm_process_config IS
    'Phase 25.4 — per-process provider/model overrides. Empty table means use LLM_DEFAULTS from code. One row per process when an override is active.';
