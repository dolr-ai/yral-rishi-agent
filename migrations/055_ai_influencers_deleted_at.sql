-- Free a deleted persona's name for re-use, without freeing a BANNED one.
--
-- Deleting an account soft-deletes its personas, but the name stayed taken
-- forever: `create_influencer` 409s on `get_by_name`, which has no liveness
-- filter (issue #513). 267 names are held hostage in production today.
--
-- Filtering `get_by_name` alone is NOT enough, and would be worse than the
-- bug. `name` carries a table-wide UNIQUE constraint from 001_initial, and
-- the insert is `ON CONFLICT (id) DO NOTHING` — which covers the primary key,
-- not the name. Passing the application check just moves the failure into the
-- INSERT as an uncaught UniqueViolationError: a clean 409 becomes a 500.
-- Uniqueness has to stop applying to deleted rows in the DATABASE.
--
-- Why a new column instead of a new `is_active` value: `soft_delete` and `ban`
-- both write `is_active = 'discontinued'` today, so the two are
-- indistinguishable, and NINE queries across the app filter
-- `is_active != 'discontinued'` to exclude both. Introducing a distinct state
-- would make every one of those start serving deleted bots. `deleted_at` is
-- additive: all nine keep working untouched, and ban stays name-locked
-- because it never sets the column. A banned handle must not become
-- re-registerable.

SET lock_timeout = '3s';
SET statement_timeout = '60s';

-- NULL = not deleted. Ban does not set this; only soft_delete does.
-- timestamptz, not timestamp: `timestamp` drops the UTC offset, and this
-- column decides whether a name is claimable — an ambiguous instant is not
-- something to hang that on. Matches 054's claimed_at; the older columns on
-- this table predate the convention.
ALTER TABLE ai_influencers
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- Backfill the personas already soft-deleted. `display_name = 'Deleted Bot'`
-- is what soft_delete writes and ban does not, so it distinguishes the two
-- retroactively — the only signal that survives from before this column.
-- updated_at is when the soft-delete ran, so it is the honest timestamp.
UPDATE ai_influencers
SET deleted_at = updated_at
WHERE deleted_at IS NULL
  AND is_active = 'discontinued'
  AND display_name = 'Deleted Bot';

-- Swap the table-wide uniqueness for liveness-scoped uniqueness. Still
-- enforced by the database, so two concurrent creates of the same name still
-- cannot both win — the app's 409 check is a nicety, this is the guarantee.
ALTER TABLE ai_influencers
    DROP CONSTRAINT IF EXISTS ai_influencers_name_key;

CREATE UNIQUE INDEX IF NOT EXISTS ai_influencers_name_live_key
    ON ai_influencers (name)
    WHERE deleted_at IS NULL;

-- get_by_name filters on (name, deleted_at); the partial unique index above
-- already covers that lookup, and idx_influencers_name from 001 stays for
-- queries that want every row including deleted ones.
