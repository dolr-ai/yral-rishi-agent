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
# A1 DELETION JUSTIFICATION
# -------------------------
# The `downgrade()` function in this migration drops the
# `influencer_metadata` table that the `upgrade()` function in this
# same migration created. This is REVERSIBILITY of THIS migration,
# NOT destruction of pre-existing production data — same precedent
# as the soul-file-library's 001 migration (PR #104 + Rishi's
# 2026-05-19 A1 carve-out decision). Standard Alembic round-trip
# practice; suppressing the `drop_table` would leave the schema
# un-reversible + violate H11 spirit.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from alembic import op
import sqlalchemy as sa


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
        # orchestrator's composer to resolve an L3 row.
        sa.Column("id", sa.Text, primary_key=True),
        # Display + presentation fields — required for the mobile UI.
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("bio", sa.Text, nullable=False),
        sa.Column("avatar_url", sa.Text, nullable=False),
        # archetype — joins to `soul_file_layers.scope_key` where
        # layer = 2 (companion / therapist / coach as of 2026-05-23
        # seeds). NO CHECK / FK constraint per option (γ) above.
        sa.Column("archetype", sa.Text, nullable=False),
        # is_nsfw — A10 routing flag (NSFW → OpenRouter; non-NSFW →
        # default Gemini). Drives orchestrator's provider matrix.
        sa.Column("is_nsfw", sa.Boolean, nullable=False),
        # follower_count — chat-ai-parity field; drives the
        # `/trending` endpoint's ORDER BY in the absence of a real
        # ranking pipeline.
        sa.Column(
            "follower_count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        # creator_user_id — NULL for system-seeded influencers, set
        # for creator-studio-spawned ones (post-cutover feature).
        sa.Column("creator_user_id", sa.Text, nullable=True),
        # is_active — chat-ai-parity tri-state expressed as 2-value
        # TEXT + CHECK constraint below. Default 'active' so a new
        # creator-studio influencer surfaces in the catalog
        # immediately on insert.
        sa.Column(
            "is_active",
            sa.Text,
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        # source — v2-only origin tracking. NULL for rows whose
        # provenance pre-dates this column (none initially); populated
        # going forward.
        sa.Column("source", sa.Text, nullable=True),
        # Audit-trail pair. Both default to NOW() at insertion;
        # `updated_at` is bumped by application-layer writes (no
        # database trigger today — keeps the DB schema portable; if
        # write traffic grows enough that app-layer bumps become
        # error-prone, revisit with a trigger).
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
        # 2-value contract. Named so a future migration can DROP +
        # re-ADD by name without grep-the-DDL.
        sa.CheckConstraint(
            "is_active IN ('active', 'discontinued')",
            name="influencer_metadata_is_active_in_active_or_discontinued",
        ),
    )

    # Trending-endpoint index — partial on `is_active='active'` so the
    # index size stays proportional to ACTIVE influencers (the only
    # rows /trending returns), not the full table. DESC matches the
    # query's ORDER BY direction so the index is used end-to-end.
    op.execute(
        "CREATE INDEX influencer_metadata_active_follower_count "
        "ON influencer_metadata (follower_count DESC) "
        "WHERE is_active = 'active';"
    )

    # Archetype-filter index — future-facing; covers any "list
    # influencers with archetype = X" query the mobile UI may add.
    op.execute(
        "CREATE INDEX influencer_metadata_archetype "
        "ON influencer_metadata (archetype);"
    )


def downgrade() -> None:
    """Drop the `influencer_metadata` table + its indexes.

    WHAT: reverses the `upgrade()` — DROP INDEX (implicit on table
          drop) + DROP TABLE.
    WHEN: invoked by `alembic downgrade base` on a fresh test database
          (round-trip migration test exercises this) or by a manual
          operator rollback (rare; the more common rollback path is
          `git revert` + a new forward migration).
    WHY:  H11 spirit + Alembic round-trip standard. The drop is
          reversible against THIS migration's schema, not against
          any pre-existing real data — see the A1 deletion
          justification in the file header.
    """
    op.drop_table("influencer_metadata")


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
