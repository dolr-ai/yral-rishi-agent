-- Phase 1 Request Images — server-side pre-blurred blob variants
-- + messages.message_type='collage' widening.
--
-- Two additive changes. Both deferred by migration 046's own comment
-- block ("image_urls_blurred / subscription-gated variants" +
-- "ALTER TABLE messages to add 'collage' to the message_type CHECK
-- constraint") — this migration fills those in for the real-user
-- (non-YRAL-team) Phase 1 rollout.
--
-- Why now: the client can send `is_subscribed` in the request body
-- (Rishi choice 2026-07-08) but the backend paywall enforcement
-- rests on serving PRE-BLURRED URLs to non-subscribers — never
-- shipping clear pixels to a device the operator can't trust. Client
-- flag decides WHICH URLs go out; server-side blur means even a
-- spoofed flag can only get clear URLs when they're actually meant
-- to be given out.
--
-- Timeouts + lock guard follow the canonical migration 041 pattern
-- (Rishi 2026-05-30): a `SET lock_timeout` alongside `SET
-- statement_timeout` prevents a slow ALTER from wedging concurrent
-- writers past a bounded window — squawk lint enforces both.

SET lock_timeout = '3s';
SET statement_timeout = '60s';

-- 1. image_urls_blurred — parallel array to image_urls, backfilled
--    at collage-generate time (Pillow gaussian blur → uploaded to
--    S3). Default ARRAY[]::TEXT[] so existing rows (from Phase 0's
--    YRAL-team-only cohort) don't break the NOT NULL constraint;
--    the route falls back to serving `image_urls` when
--    `image_urls_blurred` is empty (pre-blur backfill hasn't run
--    yet for that row), keeping the old cohort's flow working.

ALTER TABLE influencer_collages
    ADD COLUMN IF NOT EXISTS image_urls_blurred TEXT[]
        NOT NULL DEFAULT ARRAY[]::TEXT[];

-- 2. Widen messages.message_type CHECK so the mobile client can
--    persist a collage message referencing `(collage_bot_id,
--    collage_date)` — no image URLs in the message payload, so the
--    render pass can refetch on subscription transitions per Rishi's
--    2026-07-08 design ("self-healing historical messages when the
--    user subscribes").
--
--    Widening the allowed set from 4 → 5 values keeps every existing
--    row valid (they all match one of the old 4). ADD CONSTRAINT
--    revalidates in-place — no rewrite needed on a WIDER predicate,
--    but PG still scans; the 60s statement_timeout above bounds the
--    worst case.

ALTER TABLE messages
    DROP CONSTRAINT IF EXISTS messages_message_type_check;

-- Two-phase widening per squawk canon: ADD NOT VALID takes a fast
-- lock without a table scan; the VALIDATE step scans under
-- SHARE UPDATE EXCLUSIVE which does NOT block writers. Since the
-- new predicate is a strict superset of the old (all existing
-- rows must have message_type IN the original 4 values, all of
-- which are in the new 5), VALIDATE is guaranteed to pass — no
-- data-quality risk from the split.

ALTER TABLE messages
    ADD CONSTRAINT messages_message_type_check
    CHECK (message_type IN ('text', 'multimodal', 'image', 'audio', 'collage'))
    NOT VALID;

ALTER TABLE messages
    VALIDATE CONSTRAINT messages_message_type_check;
