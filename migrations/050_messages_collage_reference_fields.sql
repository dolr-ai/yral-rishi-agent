-- Phase 1 Request Images — collage message reference fields.
--
-- Sarvesh mobile integration surfaced the gap 2026-07-13:
-- migration 047 widened messages.message_type CHECK to allow
-- 'collage', but no columns exist to hold the collage reference.
-- Mobile has been POSTing {message_type: 'collage', collage_id,
-- collage_bot_id, collage_date}; backend silently dropped the 3
-- reference fields → GET returned null → mobile couldn't render
-- the collage on refresh or refetch on subscription flip.
--
-- Design §5 "self-healing historical messages when the user
-- subscribes" (per app/routes/request_images.py:83-87) relies on
-- these 3 fields being persisted + returned so mobile can refetch
-- the collage row by (collage_bot_id, collage_date) — or by
-- collage_id when the UUID path is preferred (post-migration 048).
--
-- Three additive nullable columns:
--
--   collage_id      UUID NULL      — preferred lookup; matches
--                                    influencer_collages.id (migration
--                                    048's UUID PK). Null on non-collage
--                                    messages + legacy pre-migration rows.
--   collage_bot_id  VARCHAR(255)   — legacy compound-key first half.
--                                    Deliberately NOT a FK — deleting a
--                                    bot must not cascade-nuke every
--                                    historical chat message.
--   collage_date    DATE           — compound-key second half. Same
--                                    non-FK rationale.
--
-- Partial index on collage_id supports future reverse lookups
-- ("what messages reference collage X"). Cheap to maintain because
-- non-collage rows all have collage_id IS NULL and get excluded.
--
-- Optional CHECK constraint restricting the 3 fields to co-appear
-- only on message_type='collage' rows is DELIBERATELY out of scope
-- per the brief — adds lock-time cost on the large messages table
-- and the wire contract Sarvesh is already sending guarantees the
-- correct co-appearance from the client side.
--
-- pg_dump taken pre-this-migration (Rule 9):
--   pre_migration_050_messages_collage_ref_* (Rishi publishes exact
--   filename + SHA in PR body).
--
-- Migration applied MANUALLY post-merge, same pattern as
-- 044/045/046/047/048.
--
-- Squawk compliance: BOTH lock_timeout AND statement_timeout at the
-- top before any DDL (per the PR #427 lesson). Additive ADD COLUMN
-- without a default is metadata-only on Postgres 11+ — no row
-- rewrite on the (large) messages table.

SET lock_timeout = '3s';
SET statement_timeout = '60s';


ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS collage_id     UUID,
    ADD COLUMN IF NOT EXISTS collage_bot_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS collage_date   DATE;


CREATE INDEX IF NOT EXISTS idx_messages_collage_id
    ON messages (collage_id)
    WHERE collage_id IS NOT NULL;
