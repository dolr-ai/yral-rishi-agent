-- Backend track 1a — Spicy chat gate consent storage
-- (Design: docs/spicy-chat-gate-design-2026-06-28.md §4.3;
--  Dispatch: docs/spicy-chat-gate-dispatch-briefs-2026-06-30.md)
--
-- One row per user who has confirmed the 18+ / adult-content gate on
-- the spicy web brand. The row is the AUDIT + cross-device memory for
-- logged-in users; the actual live gate is the web session cookie on
-- the brand's own domain. Anonymous web visitors get the cookie only —
-- no row here.
--
-- Why v2 and NOT metadata-server: consent is per-account and needs to
-- survive a device swap. Putting it in a third service would create
-- another cross-service identity split (see the
-- ai_influencer_name_split_brain incident). This migration lives in
-- yral_agent_db because the row is the AUDIT of consent — NOT the
-- adult messages themselves, which will land in amorae_db (design §4.4
-- Level 2 isolation).
--
-- Backwards-compat: additive only. New table, no ALTER on existing
-- tables, no destructive DDL. Follow-up track 1b lands the
-- GET/POST /api/v1/users/nsfw-consent endpoints on top of this table.
--
-- Pg_dump taken pre-this-migration (Rule 9):
--   pre_migration_045_nsfw_consent_* (Rishi will publish the exact
--   filename + SHA in the PR body).
--
-- Migration is applied MANUALLY post-merge — the deploy does NOT
-- auto-run it. Command in the PR body.
--
-- Squawk compliance notes (follow the PR #426 conventions):
--   - statement_timeout cap on the DDL.
--   - PK is text (user_id) so IDENTITY syntax doesn't apply — user_id
--     is the natural key from the JWT `sub` claim, mirrors every
--     other v2 table.
--   - No INTEGER columns to widen: only text, timestamptz, inet.

SET statement_timeout = '5s';

CREATE TABLE IF NOT EXISTS user_nsfw_consent (
    user_id       text        PRIMARY KEY,
    confirmed_at  timestamptz NOT NULL,
    -- Null = no expiry (open-ended consent). Non-null = a re-confirm
    -- deadline (design default +90d; the exact horizon is a policy
    -- knob set by the write endpoint in track 1b, not a DB default).
    expires_at    timestamptz,
    -- Audit-only. Populated from the request's IP (X-Forwarded-For
    -- → server socket peer). Nullable so a future test/backfill
    -- without an IP context still inserts.
    source_ip     inet,
    created_at    timestamptz NOT NULL DEFAULT NOW(),
    updated_at    timestamptz NOT NULL DEFAULT NOW()
);

-- Feeds the future "who's due to re-confirm" sweep (a cron / admin
-- endpoint that finds rows with expires_at in the near past). Partial
-- would be tempting but expires_at IS NULL rows are the common case
-- (no expiry) and should never appear in the sweep result anyway.
CREATE INDEX IF NOT EXISTS idx_user_nsfw_consent_expires_at
    ON user_nsfw_consent (expires_at);
