-- Drop the vestigial etl_sync_state table.
--
-- This table was the per-table cursor store for the chat-ai → V2 ETL,
-- created by migration 017. The cursor write path (_advance_cursor) was
-- a constant source of asyncpg type-codec bugs (PRs #217, #218, #219).
-- In PR #219 we removed _advance_cursor entirely and derive cursors
-- from etl_processed_files at query time (single source of truth).
--
-- Since #219 nothing reads or writes etl_sync_state. Dropping it now
-- so the schema doesn't carry dead surface area.
--
-- Verified before this migration:
--   grep -rn "etl_sync_state" app/ migrations/ tests/
-- only matches:
--   - migrations/017_etl_sync_state.sql (original creation, kept)
--   - a stale docstring + a test that asserts the table is NOT used
--     (cleaned up in the same PR)

DROP TABLE IF EXISTS etl_sync_state;
