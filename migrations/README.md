# Migrations

Numbered raw-SQL migrations applied by `scripts/ci/run-migrations.sh` on deploy.

## How the runner works

- It globs `migrations/*.sql` (excluding `*.down.sql`), **sorts by filename**, and
  applies any file not yet recorded in the `schema_migrations` table
  (`filename` is the primary key).
- **Numbering does not need to be contiguous.** Files are tracked individually by
  name; a missing number changes nothing. Ordering is lexical on the filename, so
  the zero-padded `NNN_` prefix is what sequences them.
- Each applied file is inserted into `schema_migrations`, so re-runs are no-ops.
- One safety refusal: if `schema_migrations` is empty but the schema is clearly
  already populated (`ai_influencers` exists), the runner exits rather than replay
  `001_initial.sql` onto a live DB. Override only via
  `FORCE_RUN_ON_EMPTY_SCHEMA_MIGRATIONS=true`. See the script's header comment.

## Numbering gaps (as of 2026-07-28)

`037`, `044`, and `049` are absent on `main`. This is intentional/expected — not
lost migrations — but the reasons differ, so **do not blindly reuse these numbers**:

| # | Status | Detail |
|---|--------|--------|
| **037** | Skipped — never existed | No commit anywhere ever introduced a `037_*.sql`. A number was skipped when `038_ai_influencers_system_instructions_sections.sql` landed. Harmless. Safe to leave permanently unused; do **not** backfill. |
| **044** | **Reserved by an open PR** | Claimed by the l0-eval feature (PR #426, commit `d3bb844`). It lands as `044_*.sql` when that PR merges. **Do not author a new `044`** or it will collide. |
| **049** | **Reserved by an open PR** | Claimed by the spicy native-SFW feature (PR #454, commit `e34e8cc`). It lands as `049_*.sql` when that PR merges. **Do not author a new `049`.** |

## Adding a migration

1. Use the next free number **above the highest on `main`** (currently `050`, so
   next is `051`) — never fill an old gap, to avoid colliding with an in-flight
   branch that already claimed it (see 044/049 above).
2. Zero-pad to three digits: `051_short_description.sql`.
3. Migrations are forward-only in practice; a `.down.sql` is optional and is
   ignored by the runner.
4. Per CLAUDE.md Rule 9: take a `pg_dump` snapshot before applying any schema
   change to production.
