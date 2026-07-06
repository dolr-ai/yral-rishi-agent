-- Phase 0 track A — Request Images backend storage
-- (Design: docs/request-images-design-2026-07-06.md
--  §1a reservation-row race lock + §4 New tables (migration 046))
--
-- Three additive changes for the Phase 0 slice:
--
--   1. `influencer_collages` — one row per (bot, UTC date). The PK
--      IS the race lock: `INSERT ... ON CONFLICT DO NOTHING` elects
--      exactly one requester as the generator; the rest attach to
--      the row and poll. Same idiom as the ETL.
--
--   2. `user_image_requests` — audit + rate limit. PK
--      (user_id, bot_id, request_date) enforces "one request per
--      user-bot-day" without any application-layer counter.
--
--   3. `ai_influencers` gains three columns for the crown-jewel
--      face/body consistency work (LoRA reference + trained
--      weights + version bump). Nullable + no default → no row
--      rewrite. Additive only.
--
-- Phase 0 slice — NOT shipped here (design mentions but explicitly
-- deferred to a follow-up migration):
--
--   * `influencer_collage_themes` table — Tara ships with a single
--     hardcoded theme in the service layer for v1.
--   * `image_urls_blurred` / subscription-gated variants — subscription
--     is stubbed for the YRAL team cohort in Phase 0, so we don't
--     need a separate URL column yet.
--   * ALTER TABLE messages to add `'collage'` to the message_type
--     CHECK constraint — lands with the send-collage endpoint.
--
-- Pg_dump taken pre-this-migration (Rule 9):
--   pre_migration_046_request_images_* (Rishi will publish the exact
--   filename + SHA in the PR body).
--
-- Migration is applied MANUALLY post-merge — the deploy does NOT
-- auto-run it. Command in the PR body.
--
-- Squawk compliance (lessons from PR #426 + #427):
--   * BOTH lock_timeout AND statement_timeout at the top, before any
--     DDL (CREATE + ALTER both count as potentially-slow statements).
--   * No BIGSERIAL — both new tables use composite natural-key PKs.
--   * lora_version is BIGINT per prefer-bigint-over-int, even though
--     the value will realistically stay tiny.
--   * ALTER ... ADD COLUMN with no default is metadata-only in
--     Postgres 11+; no row rewrite on the 3.6k-row ai_influencers
--     table.

SET lock_timeout = '3s';
SET statement_timeout = '60s';


CREATE TABLE IF NOT EXISTS influencer_collages (
    bot_id           VARCHAR(255) NOT NULL
                     REFERENCES ai_influencers(id) ON DELETE CASCADE,
    generation_date  DATE NOT NULL,
    theme            TEXT NOT NULL,
    -- 6 URLs per config (image_count = 6). TEXT[] keeps ordering,
    -- avoids a child table for a fixed-N array.
    image_urls       TEXT[] NOT NULL,
    cost_usd         NUMERIC(10, 4) NOT NULL DEFAULT 0,
    -- 'reserved' | 'succeeded' | 'failed'. reserved = elected
    -- generator is still working; succeeded = image_urls populated
    -- and safe to serve; failed = watchdog can retry-elect.
    state            TEXT NOT NULL DEFAULT 'reserved',
    generated_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Composite PK = race lock. Exactly one row per bot per UTC day;
    -- ON CONFLICT DO NOTHING elects the generator.
    PRIMARY KEY (bot_id, generation_date)
);


CREATE TABLE IF NOT EXISTS user_image_requests (
    user_id       VARCHAR(255) NOT NULL,
    bot_id        VARCHAR(255) NOT NULL
                  REFERENCES ai_influencers(id) ON DELETE CASCADE,
    request_date  DATE NOT NULL,
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Composite PK = "one request per user-bot-day" rate limit.
    -- INSERT ... ON CONFLICT DO NOTHING; a rejected insert is the
    -- rate-limit hit.
    PRIMARY KEY (user_id, bot_id, request_date)
);


-- Face + body consistency (design §2). All nullable + no default so
-- the ALTER is metadata-only on the 3.6k-row ai_influencers table.
ALTER TABLE ai_influencers
    ADD COLUMN IF NOT EXISTS reference_image_url TEXT,
    ADD COLUMN IF NOT EXISTS lora_weights_url    TEXT,
    ADD COLUMN IF NOT EXISTS lora_version        BIGINT;
