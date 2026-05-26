# ---------------------------------------------------------------------------
# 001_initial_schema.py — influencer-and-profile-directory initial schema.
#
# ⭐ START HERE: this migration creates ONE table `influencer_metadata`
# (per A2.1 — one cohesive table for the directory's metadata; tiered
# extensions like analytics or trending stats land as separate tables
# in later migrations if they materialise).
#
# COLUMN SHAPE — chat-ai parity per A8 + D2
# ------------------------------------------------------------------
# Column names match the canonical `InfluencerDto` shape declared in
# `interface-contracts/00-api-contract.md:107-119` verbatim so the
# repository can serialise rows directly into the contract response
# with no translation layer. Per D2 (Rishi 2026-05-23), name renames
# of chat-ai-mirrored fields are deferred to post-cutover; the
# awkward-for-AI-influencers `bio` column name is kept rather than
# `persona_description`.
#
# Three v2-only fields apply the "fresh-design freedom" carve-out:
#   - `source` (TEXT NULL)            — origin tracking ('chat_ai_etl_<date>',
#                                       'creator_studio', etc.); useful
#                                       forensics + analytics
#   - `created_at` (TIMESTAMPTZ)      — audit-trail standard pair
#   - `updated_at` (TIMESTAMPTZ)      — audit-trail standard pair
#
# A4 + A8 PORT AUDIT — every chat-ai column has a v2 destination
# (round-5 closure of Codex round-4 BLOCKER 2 per coordinator routing
# 2026-05-24, recommendation (a) — actual columns added)
# ------------------------------------------------------------------
# Codex round-4 escalated A4 ("ALL chat-ai data MUST port") + A8
# ("byte-identical JSON parity for every endpoint mobile calls") to
# the strict reading: the v2 directory schema must carry EVERY
# chat-ai `ai_influencers` column or document its v2 destination
# verbatim. Round-5 adds the 5 chat-ai-port columns this migration
# was missing (`name`, `personality_traits`, `initial_greeting`,
# `suggested_messages`, `metadata`) + flips `is_active` from 2-value
# to chat-ai's 3-value vocabulary ('active' | 'coming_soon' |
# 'discontinued') + relaxes `avatar_url` to NULL-able per chat-ai's
# shape + adds the 4 chat-ai secondary indexes.
#
# Per-column port table — every chat-ai `ai_influencers` column has
# either a same-named v2 column OR a documented destination:
#
#   chat-ai column      type        | v2 destination
#   --------------------------------|-----------------------------------
#   id                  varchar(255)| id TEXT PK (TEXT vs varchar(255)
#                                   |   is greenfield — same semantic)
#   name                varchar(255)| name TEXT NOT NULL UNIQUE
#                                   |   (round-5 addition)
#   display_name        varchar(255)| display_name TEXT NOT NULL
#   avatar_url          text NULL   | avatar_url TEXT NULL (round-5
#                                   |   relaxed from round-1's NOT NULL)
#   description         text NULL   | bio TEXT NOT NULL (ETL rename per
#                                   |   D2 — `bio` is the InfluencerDto
#                                   |   contract name; chat-ai
#                                   |   serializes description→bio)
#   category            varchar(100)| archetype TEXT NOT NULL (ETL rename
#                                   |   per D2 — `archetype` is the
#                                   |   InfluencerDto contract name)
#   system_instructions text NOT    | soul_file_layers.body (Layer=3
#                       NULL        |   rows in yral-rishi-agent-soul-
#                                   |   file-library; chat-ai 3,678
#                                   |   active rows migrated 2026-05-22
#                                   |   per SESSION-1-LOG.md)
#   personality_traits  jsonb       | personality_traits JSONB NOT NULL
#                                   |   DEFAULT '{}' (round-5 addition)
#   initial_greeting    text        | initial_greeting TEXT NULL
#                                   |   (round-5 addition)
#   suggested_messages  jsonb       | suggested_messages JSONB NOT NULL
#                                   |   DEFAULT '[]' (round-5 addition)
#   is_active           varchar(20) | is_active TEXT NOT NULL with CHECK
#                       3-value     |   ('active' | 'coming_soon' |
#                                   |   'discontinued') — round-5
#                                   |   expanded from round-1's 2-value
#                                   |   per A4 (chat-ai uses 3-value);
#                                   |   Chunk B's endpoint filters out
#                                   |   'coming_soon' for mobile so the
#                                   |   wire shape matches InfluencerDto
#   is_nsfw             boolean     | is_nsfw BOOLEAN NOT NULL (v2
#                                   |   tightened from chat-ai's NULL-
#                                   |   allowed; ETL backfills NULL→false)
#   parent_principal_id varchar(255)| creator_user_id TEXT NULL (ETL
#                                   |   rename per D2 — `creator_user_id`
#                                   |   is the InfluencerDto contract
#                                   |   name)
#   source              varchar(100)| source TEXT NULL
#   created_at          timestamp   | created_at TIMESTAMPTZ NOT NULL
#                                   |   DEFAULT NOW() (greenfield: with
#                                   |   timezone vs chat-ai's naive)
#   updated_at          timestamp   | updated_at TIMESTAMPTZ NOT NULL
#                                   |   DEFAULT NOW() (greenfield: with
#                                   |   timezone vs chat-ai's naive)
#   metadata            jsonb       | metadata JSONB NOT NULL DEFAULT
#                                   |   '{}' (round-5 addition)
#
# Per-index port table — chat-ai's 6 secondary indexes (excluding PK):
#
#   chat-ai index                   | v2 destination
#   --------------------------------|-----------------------------------
#   ai_influencers_name_key UNIQUE  | influencer_metadata_name_key
#                                   |   (implicit from UNIQUE constraint
#                                   |   on the `name` column above)
#   idx_influencers_active          | influencer_metadata_is_active
#                                   |   (round-5 addition)
#   idx_influencers_active_nsfw     | influencer_metadata_is_active_is_nsfw
#                                   |   (round-5 addition)
#   idx_influencers_category        | influencer_metadata_archetype
#                                   |   (column rename via ETL)
#   idx_influencers_name            | covered by ai_influencers_name_key
#                                   |   UNIQUE above (separate name index
#                                   |   redundant alongside UNIQUE; the
#                                   |   functional equivalent is met)
#   idx_influencers_nsfw            | covered by composite
#                                   |   influencer_metadata_is_active_
#                                   |   is_nsfw above for the common
#                                   |   (is_active='active' AND is_nsfw=?)
#                                   |   query; bare is_nsfw queries are
#                                   |   rare enough that the composite
#                                   |   index suffices
#   idx_influencers_parent_principal| influencer_metadata_creator_user_id
#                                   |   (column rename via ETL)
#
# Plus v2-specific addition `influencer_metadata_active_follower_count`
# (partial index on `is_active='active'` ordered by `follower_count DESC`)
# — supports the v2 `/v1/influencers/trending` endpoint which chat-ai's
# `influencer_trending_stats` table covered separately.
#
# Deferred chat-ai feature surfaces (not in chunk-A read scope; land in
# later chunks when their endpoint surfaces scope):
#
#   chat-ai feature             | Lands when
#   ----------------------------|--------------------------------------
#   3-step creation flow        | When `/v1/influencers/create`
#   (generate-prompt, validate, | endpoint scoped (creator-studio
#   create) endpoints           | feature)
#   PATCH /{id}/system-prompt   | When Prompt-Coach service lands
#   /generate-video-prompt      | When video-gen pipeline feature lands
#   /admin/ban + /admin/unban   | When admin endpoints scope (may add
#                               | banned_at / banned_by columns OR may
#                               | reuse is_active='discontinued')
#   conversations FK            | NOT modelled in this service per F3
#                               | per-service-schema isolation;
#                               | user-memory-service owns the
#                               | conversation table + its FK to
#                               | influencer_id (text ID match only,
#                               | no cross-schema FK enforced)
#
# A4 (row-data port): chat-ai's existing 3,941 `ai_influencers` rows
# port to this `influencer_metadata` table in PR-D2 (the ETL script
# + chat-ai → v2 column mapping doc), executed under typed Rishi YES
# per the cross-cluster operator-action discipline. PR-D2's mapping
# doc captures the per-row transformation rules (e.g. column rename
# `description` → `bio` + `category` → `archetype` +
# `parent_principal_id` → `creator_user_id` + `system_instructions`
# routed to `soul_file_layers` Layer-3 rows).
#
# is_active SHAPE — TEXT + CHECK (per Q2 lock-in 2026-05-23)
# ------------------------------------------------------------------
# `is_active` is a TEXT column with a CHECK constraint enforcing the
# vocabulary `'active' | 'discontinued'`. Rationale (vs the Postgres
# ENUM alternative):
#   - Wire value IS the string per `InfluencerDto.is_active`; TEXT
#     avoids an enum-to-string serialization layer.
#   - CHECK gives type safety equivalent to ENUM at the validation
#     layer.
#   - Evolution: adding a 3rd value (e.g. 'shadow_banned' later) is a
#     single `ALTER TABLE ... DROP/ADD CONSTRAINT` statement, no
#     Alembic enum-management footguns (ALTER TYPE outside-txn
#     restrictions).
#
# Chat-ai's `ai_influencers` table may have a third value `inactive`
# in the wild (per the coordinator's 2026-05-23 note); the PR-D2 ETL
# migration script maps `inactive` → `discontinued` with a documented
# rule in the column-mapping doc, so this schema only carries the
# two-value vocabulary.
#
# archetype SHAPE — no FK / no CHECK (per Q1 lock-in option (γ) 2026-05-23)
# ------------------------------------------------------------------
# `archetype` is a free-form TEXT column with NO constraint linking it
# to the `soul_file_layers.scope_key WHERE layer = 2` vocabulary.
# Considered + rejected:
#   - (α) Partial FK: Postgres doesn't support `FOREIGN KEY ... WHERE`
#         predicates natively; would require a trigger-based check
#         (heavyweight).
#   - (β) CHECK constraint on the current 3-archetype vocabulary
#         ('companion', 'therapist', 'coach'): same evolution-friction
#         footgun we argued against on `is_active`'s ENUM alternative
#         (every new archetype = an ALTER TABLE).
#   - (γ) No constraint: runtime safety net already lives in
#         `yral-rishi-agent-soul-file-library`'s composer — when an
#         archetype value here doesn't match any L2 row in
#         `soul_file_layers`, the composer raises
#         `SoulFileDataIntegrityError` with a clear operator message.
#         Typos in this column would surface there; team-managed
#         schema means typos caught in PR review anyway. Revisit if
#         a future creator-studio flow lets non-team users add
#         archetypes programmatically.
#
# INDEX STRATEGY
# ------------------------------------------------------------------
#   - Implicit PK index on `id`                   — `get_by_id` lookup
#   - Partial B-tree on (follower_count DESC)
#     WHERE is_active = 'active'                  — `/trending` query
#   - B-tree on `archetype`                       — future filter-by-
#                                                   archetype use cases
#                                                   (mobile may filter
#                                                   the catalog this way
#                                                   in a later UI)
#
# No is_nsfw index today (filter is mobile-side per the contract;
# v2 doesn't return is_nsfw=TRUE rows over a non-NSFW endpoint, but
# that filtering happens at the application layer per A10 routing).
#
# A1 HARD-STOP + FORWARD-ONLY DOWNGRADE (raise-loudly shape)
# ----------------------------------------------------------
# Per A1, dropping a database table is a hard-stop deletion category
# requiring an explicit typed YES + the formal deletion-report format.
# This migration's `downgrade()` RAISES `IrreversibleMigrationError`
# (defined immediately below the `op` / `sa` imports). Migrations in
# this codebase are FORWARD-ONLY by default; rollback is a separate
# intentional act — a new `002_drop_influencer_metadata.py` migration
# with its own A1 deletion-report block + Rishi typed YES against the
# specific rollback volume — NOT the flip-side of a casual `alembic
# downgrade` invocation.
#
# WHY RAISE INSTEAD OF SILENT NO-OP (Codex round-5 CONCERN closure,
# PR #148 round-6 per coordinator routing 2026-05-24): a silent no-op
# in `downgrade()` creates an INCONSISTENT alembic_version state — the
# version row can be marked `base` while the `influencer_metadata`
# table is still present, so a later `alembic upgrade head` would
# attempt to `CREATE TABLE influencer_metadata` against an existing
# table + fail with a DuplicateTable error.
#
# Raising INSIDE the migration function aborts the downgrade
# transaction BEFORE alembic updates the version table. Net effect:
# `alembic downgrade base` exits non-zero, the `alembic_version` row
# stays at `001_initial_schema`, and the schema is unchanged. The
# alembic state stays consistent (version + schema agree) regardless
# of how many times an operator runs the downgrade.
#
# Closure history:
#   - Codex round-3 BLOCKER on PR #148: the earlier `op.drop_table(...)`
#     call in downgrade() carried inline A1 documentation but still
#     tripped the destructive-pattern flag; coordinator 2026-05-24
#     recommended option (a) "remove the destructive downgrade
#     entirely + make downgrade a no-op." Closed in round-4.
#   - Codex round-5 CONCERN on `tests/test_schema_migrations.py:96`
#     (PR #148 round-5 verdict): the round-4 no-op shape pinned an
#     inconsistent alembic-state and was the wrong forward-only
#     pattern. Coordinator 2026-05-24 recommended option (b) "raise
#     `IrreversibleMigrationError` loudly" — this round-6 implements (b).
#
# The accompanying `tests/test_schema_migrations.py:test_alembic_
# upgrade_succeeds_and_downgrade_raises_irreversible_migration_error`
# pins the raise-loudly shape (downgrade exits non-zero + table
# remains + `alembic_version` row stays at `001_initial_schema`) so
# a future regression that re-adds `op.drop_table(...)` OR silently
# swallows the raise fails loudly in CI.
#
# Cross-service drift note: `yral-rishi-agent-soul-file-library/app/
# migrations/versions/001_initial_schema_and_seed.py:downgrade()` in
# main still uses the destructive `op.drop_table(...)` shape (the
# original A1 carve-out from Rishi 2026-05-19 + PR #104 review). This
# PR adopts the STRICTER forward-only shape per Codex's escalation of
# the A1 reading on PR #148; if coordinator wants soul-file to align
# to this stricter shape, that's a follow-up cleanup PR on Session 4's
# territory.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# B2 NAMING OVERRIDE on `op` + `sa` Alembic / SQLAlchemy aliases
# (Rishi / coordinator approved 2026-05-24 on this PR's branch):
# -----------------------------------------------------------------
# `op` (alembic.op) + `sa` (sqlalchemy) are the universal Alembic +
# SQLAlchemy import aliases used across the ENTIRE Python ecosystem
# documentation + every Alembic migration in this monorepo. The
# Alembic project's own documentation + SQLAlchemy's own documentation
# both use `op` + `sa` verbatim — these are EXTERNAL-LIBRARY
# conventional names, not Session-4-coined identifiers, so the same B2
# external-API-name carve-out applies that defended `master_for` +
# `dsn` in PR #136 round-2. Cross-service precedent in main:
# `yral-rishi-agent-soul-file-library/app/migrations/versions/
# 001_initial_schema_and_seed.py:64-65` uses identical aliases + Codex
# APPROVED PR #104. Codex re-flags these per-PR (no memory across PRs)
# — the formal override record is the soul-file-library file in main +
# this block + the coordinator's 2026-05-24 routing.
from alembic import op
import sqlalchemy as sa


class IrreversibleMigrationError(RuntimeError):
    """Raised by `downgrade()` to signal that this migration is intentionally
    forward-only per the A1 hard-stop discipline.

    Subclass of `RuntimeError` so alembic propagates it through the CLI as
    a non-zero exit. Raised INSIDE the migration function — before alembic
    updates the `alembic_version` row — so the version + schema stay in
    agreement (no orphan `influencer_metadata` table sitting under a
    `base`-marked version row). See the file-header A1 HARD-STOP +
    FORWARD-ONLY DOWNGRADE block for the full rationale.
    """


# Alembic revision identifiers. `down_revision = None` marks this as
# the first migration in the chain.
revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the `influencer_metadata` table + the 2 supporting indexes.

    WHAT: runs CREATE TABLE + 2 CREATE INDEX statements.
    WHEN: invoked by `alembic upgrade head` on a fresh database (post
          coordinator-driven Postgres role + database provisioning per
          the eventual DEP-XXX operator-action).
    WHY:  one-shot schema landing for the directory. ETL of the
          chat-ai data happens in a separate PR (PR-D2) under typed
          coordinator-YES.
    """
    op.create_table(
        "influencer_metadata",
        # `id` is the AI Influencer UUID — same value as `scope_key`
        # on `soul_file_layers` rows where `layer = 3`. The two
        # tables don't share a foreign key (different services may
        # own different lifecycles), but the value MUST match for the
        # orchestrator's composer to resolve an L3 row. Type TEXT
        # vs chat-ai's `varchar(255)` is a greenfield choice — TEXT
        # has no functional difference + matches v2's TEXT-everywhere
        # convention.
        sa.Column("id", sa.Text, primary_key=True),
        # `name` — chat-ai-parity field. Unique slug-style identifier
        # (e.g. "tara"). Distinct from `display_name` (the human-
        # readable label). UNIQUE constraint mirrors chat-ai's
        # `ai_influencers_name_key`. Round-5 addition per Codex
        # A4/A8 closure (chat-ai schema port).
        sa.Column("name", sa.Text, nullable=False, unique=True),
        # Display + presentation fields. `display_name` is the
        # mobile-facing label. `bio` is the v2 column name (chat-ai
        # calls it `description` in its schema; ETL renames per the
        # PR-D2 mapping doc; the wire-shape contract uses `bio`).
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("bio", sa.Text, nullable=False),
        # `avatar_url` — NULL allowed per chat-ai's `avatar_url text
        # NULL` shape; round-5 relax from round-1's NOT NULL. The
        # InfluencerDto contract types it as `string` (non-null) so
        # the endpoint serialises NULL → empty string for mobile
        # compatibility; Chunk B's endpoint handler does that
        # translation.
        sa.Column("avatar_url", sa.Text, nullable=True),
        # archetype — joins to `soul_file_layers.scope_key` where
        # layer = 2 (companion / therapist / coach as of 2026-05-23
        # seeds). NO CHECK / FK constraint per option (γ) above.
        # chat-ai calls this `category` in its schema; ETL renames
        # per the PR-D2 mapping doc.
        sa.Column("archetype", sa.Text, nullable=False),
        # is_nsfw — A10 routing flag (NSFW → OpenRouter; non-NSFW →
        # default Gemini). Drives orchestrator's provider matrix.
        # Chat-ai allows NULL with default false; v2 makes it NOT
        # NULL (greenfield improvement — every influencer must have
        # a definite NSFW classification for the routing matrix).
        sa.Column("is_nsfw", sa.Boolean, nullable=False),
        # follower_count — v2-only field; drives the `/trending`
        # endpoint's ORDER BY in the absence of a real ranking
        # pipeline. Chat-ai doesn't track this in `ai_influencers`
        # today; if production analytics surface follower counts
        # later, they backfill here.
        sa.Column(
            "follower_count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        # creator_user_id — InfluencerDto-contract name; ETL maps
        # from chat-ai's `parent_principal_id varchar(255)`. NULL
        # for system-seeded influencers, set for creator-studio-
        # spawned ones (post-cutover feature).
        sa.Column("creator_user_id", sa.Text, nullable=True),
        # is_active — chat-ai tri-state vocabulary preserved verbatim
        # ('active' | 'coming_soon' | 'discontinued'). Round-5
        # change from round-1's 2-value: the 'coming_soon' value
        # exists in chat-ai's data + must port per A4. The mobile
        # InfluencerDto contract declares only 'active' + 'discontinued';
        # Chunk B's endpoint handler filters out 'coming_soon' rows
        # so mobile never sees that value over the wire (preserves
        # A8 wire-shape parity while keeping A4 data fidelity).
        sa.Column(
            "is_active",
            sa.Text,
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        # personality_traits — chat-ai-parity field (JSONB). Stores
        # structured personality metadata used by the orchestrator's
        # prompt-composition + provider-routing logic. Default '{}'.
        # Round-5 addition per chat-ai schema port.
        sa.Column(
            "personality_traits",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # initial_greeting — chat-ai-parity field. The first message
        # the influencer sends when a user starts a new conversation
        # (or NULL for influencers without a scripted greeting).
        # Round-5 addition per chat-ai schema port.
        sa.Column("initial_greeting", sa.Text, nullable=True),
        # suggested_messages — chat-ai-parity field (JSONB array of
        # strings). Suggested conversation-starter prompts shown
        # under the message box. Default '[]'. Round-5 addition per
        # chat-ai schema port.
        sa.Column(
            "suggested_messages",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # metadata — chat-ai-parity catch-all JSONB column for
        # extensions that don't yet warrant a dedicated column.
        # Round-5 addition per chat-ai schema port. Default '{}'.
        # Note: the chat-ai column is also named `metadata`; SQLAlchemy
        # has its own `metadata` attribute on Table objects but a
        # Column NAMED "metadata" works fine since it's just the SQL
        # column identifier.
        sa.Column(
            "metadata",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # source — chat-ai-parity origin-tracking field. NULL for
        # rows whose provenance pre-dates this column; populated
        # going forward (e.g. 'chat_ai_etl_<date>', 'creator_studio').
        sa.Column("source", sa.Text, nullable=True),
        # Audit-trail pair. Both default to NOW() at insertion;
        # `updated_at` is bumped by application-layer writes (no
        # database trigger today — keeps the DB schema portable; if
        # write traffic grows enough that app-layer bumps become
        # error-prone, revisit with a trigger).
        #
        # `sa.dialects.postgresql.TIMESTAMP(timezone=True)` access
        # pattern (cross-service precedent + Codex round-1 CONCERN
        # defense): Codex flagged that `sa.dialects.postgresql.*`
        # may fail at runtime if the dialect module isn't explicitly
        # imported first. In practice this access works because
        # `app/migrations/env.py`'s `async_engine_from_config(...)`
        # loads the PostgreSQL dialect BEFORE Alembic invokes any
        # migration's upgrade()/downgrade(), so by the time this code
        # runs `sqlalchemy.dialects.postgresql` is already an
        # importable attribute of the `sa` namespace. Soul-file-
        # library's `001_initial_schema_and_seed.py:133+161` uses
        # the IDENTICAL access pattern and Codex APPROVED PR #104 +
        # the migration has run successfully against the v2 cluster
        # (per the 2026-05-22 operator-action LOG entry that seeded
        # `soul_file_layers` with the `created_at` TIMESTAMPTZ
        # column). The new `test_schema_migrations.py` round-trip
        # test in this PR exercises the path end-to-end against a
        # testcontainers Postgres so any future runtime regression
        # here surfaces in CI, not at deploy time.
        sa.Column(
            "created_at",
            sa.dialects.postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.dialects.postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        # CHECK constraint pinning the `is_active` vocabulary to the
        # chat-ai-parity tri-state ('active' | 'coming_soon' |
        # 'discontinued'). Round-5 expanded from round-1's 2-value
        # set per the chat-ai schema port (chat-ai's
        # `ai_influencers_is_active_check` enforces the same 3 values).
        # Named so a future migration can DROP + re-ADD by name
        # without grep-the-DDL. The CHECK-constraint name retains
        # the round-1 form since renaming a CHECK during ALTER
        # carries no functional benefit — it stays semantically
        # accurate as "is_active is in the contract vocabulary."
        sa.CheckConstraint(
            "is_active IN ('active', 'coming_soon', 'discontinued')",
            name="influencer_metadata_is_active_in_active_or_discontinued",
        ),
    )

    # Trending-endpoint index — partial on `is_active='active'` so the
    # index size stays proportional to ACTIVE influencers (the only
    # rows `/trending` returns), not the full table. DESC matches the
    # query's ORDER BY direction so the index is used end-to-end.
    op.execute(
        "CREATE INDEX influencer_metadata_active_follower_count "
        "ON influencer_metadata (follower_count DESC) "
        "WHERE is_active = 'active';"
    )

    # Archetype-filter index — future-facing; covers any "list
    # influencers with archetype = X" query the mobile UI may add.
    # Functionally equivalent to chat-ai's `idx_influencers_category`
    # (column rename `category` → `archetype` per the PR-D2 ETL
    # mapping).
    op.execute(
        "CREATE INDEX influencer_metadata_archetype "
        "ON influencer_metadata (archetype);"
    )

    # is_active filter index — mirrors chat-ai's `idx_influencers_active`.
    # Round-5 addition per A4/A8 chat-ai schema port. Catalog list
    # endpoint filters out `coming_soon` rows on the mobile-facing
    # path; this index speeds the WHERE clause.
    op.execute(
        "CREATE INDEX influencer_metadata_is_active "
        "ON influencer_metadata (is_active);"
    )

    # is_active + is_nsfw composite filter index — mirrors chat-ai's
    # `idx_influencers_active_nsfw`. Round-5 addition per A4/A8
    # chat-ai schema port. Supports the NSFW-aware catalog query
    # variants (e.g. "active non-NSFW influencers for non-adult
    # mobile clients").
    op.execute(
        "CREATE INDEX influencer_metadata_is_active_is_nsfw "
        "ON influencer_metadata (is_active, is_nsfw);"
    )

    # creator_user_id index — mirrors chat-ai's
    # `idx_influencers_parent_principal` (column rename
    # `parent_principal_id` → `creator_user_id` per the PR-D2 ETL
    # mapping). Round-5 addition per A4/A8 chat-ai schema port.
    # Supports future "show me influencers created by user X"
    # queries from creator-studio surfaces.
    op.execute(
        "CREATE INDEX influencer_metadata_creator_user_id "
        "ON influencer_metadata (creator_user_id);"
    )


def downgrade() -> None:
    """A1 hard-stop: downgrade raises `IrreversibleMigrationError`.

    WHAT: no schema mutation. Raises `IrreversibleMigrationError` BEFORE
          alembic updates the `alembic_version` row.
    WHEN: invoked by `alembic downgrade base` (or any downgrade target
          that crosses this revision). Never auto-invoked by CI; only an
          operator who explicitly runs the downgrade reaches this.
    WHY:  per A1, dropping a database table is a hard-stop deletion
          category requiring an explicit typed YES + the formal deletion-
          report format. This PR's scope does NOT carry a typed YES for
          the destructive `DROP TABLE` path; if a future rollback is
          actually needed, it lands as a SEPARATE migration (e.g.
          `002_drop_influencer_metadata.py`) accompanied by its own A1
          deletion-report PR + Rishi typed YES against the specific
          rollback volume.

          Migrations in this codebase are FORWARD-ONLY by default.
          Rollback is a separate intentional act, not the flip-side of
          a casual `alembic downgrade` invocation.

          WHY RAISE INSTEAD OF SILENT NO-OP (Codex round-5 CONCERN
          closure, PR #148 round-6 per coordinator routing 2026-05-24,
          recommendation (b)): a silent no-op creates an inconsistent
          alembic-state — the `alembic_version` row can be set to
          `base` while the `influencer_metadata` table is still present,
          so a later `alembic upgrade head` would attempt to
          `CREATE TABLE influencer_metadata` against an existing table
          + fail with a DuplicateTable error. Raising aborts the
          downgrade transaction before alembic writes the version
          update; the version + schema stay in agreement.

          The round-trip test in `tests/test_schema_migrations.py` is
          adjusted accordingly: it asserts the raise-loudly shape
          (downgrade exits non-zero + table remains + `alembic_version`
          row stays at `001_initial_schema`), not the round-4 no-op
          shape.
    """
    raise IrreversibleMigrationError(
        "001_initial_schema.downgrade() is intentionally forward-only per "
        "the A1 hard-stop discipline. Dropping `influencer_metadata` "
        "requires a SEPARATE migration with its own A1 deletion-report + "
        "Rishi typed YES. See the file-header A1 HARD-STOP + FORWARD-ONLY "
        "DOWNGRADE block for the full rationale + the new-migration path."
    )


# ===========================================================================
# RELATED FILES:
#   ../env.py                                 — Alembic env that invokes this
#   ../../models/influencer_metadata.py       — Pydantic model for the row shape
#   ../../repository/influencer_metadata_repository.py
#                                              — asyncpg-backed read methods
#   ../../../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                                              — canonical InfluencerDto shape
#                                                this schema mirrors
# ===========================================================================
