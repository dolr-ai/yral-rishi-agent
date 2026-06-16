-- Phase 21γ.P34.M0 — Discovery Feed migration milestone 0: admin pins.
--
-- One additive table — `trending_overrides` — that lets Rishi pin
-- specific influencers to the top of the discovery feed regardless of
-- whatever the ranking pipeline computes. M0 is operator-CRUD only:
-- the actual feed composer (M2) will JOIN this table to apply the
-- pins. Until M2 ships, M0 changes ZERO user-visible behaviour — the
-- table just sits there waiting.
--
-- Design doc: docs/discovery-feed-design-2026-06-16.md §7 + §10 (M0).
-- Track A, milestone 0 — pure CRUD, no LLM, no vision, no Redis.
--
-- Rule 9: pg_dump BEFORE apply. Auto-runner (PR #309) handles it.
--
-- Squawk: all-additive, no ALTER on populated tables, no DROP. FK
-- targets `ai_influencers(id)` which is a VARCHAR(255) PK from
-- migration 001 — same shape as every other influencer-pointing FK
-- in the schema.

SET lock_timeout = '3s';
SET statement_timeout = '60s';

CREATE TABLE IF NOT EXISTS trending_overrides (
    influencer_id  VARCHAR(255) PRIMARY KEY
                   REFERENCES ai_influencers(id) ON DELETE CASCADE,
    pinned_rank    SMALLINT     NOT NULL
                   CHECK (pinned_rank >= 1 AND pinned_rank <= 1000),
    note           TEXT,
    expires_at     TIMESTAMPTZ,
    created_by     VARCHAR(255),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE trending_overrides IS
    'Phase 21γ.P34.M0 — admin pins for the discovery feed. M2 composer '
    'JOINs this on rank=1..N and overlays the computed ranking. '
    'expires_at NULL = permanent until UNPIN.';

-- Surface the pin list ordered by rank cheaply (M2 query path + the
-- admin GET /pins endpoint).
CREATE INDEX IF NOT EXISTS idx_trending_overrides_rank
    ON trending_overrides (pinned_rank);

-- Filter expired pins out of M2's composer without scanning the whole
-- table (most pins are likely permanent — partial index keeps it slim).
CREATE INDEX IF NOT EXISTS idx_trending_overrides_expires
    ON trending_overrides (expires_at)
    WHERE expires_at IS NOT NULL;
