# ---------------------------------------------------------------------------
# 001_initial_schema_and_seed.py — Soul File Library initial schema + seeds.
#
# ⭐ START HERE: this migration creates ONE table `soul_file_layers` (per
# E8 — 4 layers; per A2.1 — single table, not four) with the columns +
# indexes the Day-4 directive specifies, then seeds:
#   - 1 × Layer 1 (global)        scope_key='' archetype=NULL
#   - 3 × Layer 2 (archetypes)    scope_key in {'companion','therapist','coach'} archetype=NULL
#   - 3 × Layer 4 (user segments) scope_key in {'new','paying','dormant'} archetype=NULL
#
# Layer 3 (per-influencer) is NOT seeded today — that's the F11 data port
# from chat-ai's `ai_influencers.system_prompt`, deferred to its own PR
# per the Day-4 directive's "Out of scope" list.
#
# WHY ONE TABLE FOR ALL 4 LAYERS
# Per A2.1 verbatim: "one table for all 4 layers (NOT four tables).
# Resist over-engineering rollback features past version-pin-hash +
# is_current bool." Single table + `layer` column + `scope_key` column
# is enough to model every layer + its current/historic versions.
#
# WHY THE EXTRA `archetype TEXT NULL` COLUMN
# Schema-spec gap surfaced during Day-4 design review (see PR body +
# SESSION-4-LOG.md Day-4 entry): the directive's composer needs to
# resolve "archetype derived from influencer" for the Layer 2 lookup,
# but the spec'd columns don't carry that mapping. Resolution chosen
# was the smallest delta — one `archetype TEXT NULL` column where, on
# L3 rows, the archetype the composer joins on is stored alongside
# the body. For L1/L2/L4 rows the column is NULL.
#
# PLACEHOLDER SEED CONTENT
# Bracketed `[v2 phase-1 day-4 ...]` placeholders match the same
# obviously-stub pattern Day-3 H4 uses for the crisis-helpline copy.
# Product owns the real Layer 1/2/4 content (separate PR); the
# placeholders are recognisable in logs + traces so a future reader
# spots "still on Day-4 stub copy".
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from alembic import op
import sqlalchemy as sa


# Alembic revision identifiers. `down_revision = None` marks this as the
# first migration in the chain.
revision = "001_initial_schema_and_seed"
down_revision = None
branch_labels = None
depends_on = None


# ===========================================================================
# Seed content (kept inside the migration so a fresh DB always boots with
# a complete L1/L2/L4 set; Layer 3 deferred to data-port PR)
# ===========================================================================

LAYER_1_GLOBAL_BODY = (
    "[v2 phase-1 day-4 Layer 1 placeholder — real global tone in "
    "day-5+ once product writes it]"
)

LAYER_2_ARCHETYPE_BODIES: dict[str, str] = {
    "companion": (
        "[v2 phase-1 day-4 Layer 2 companion archetype placeholder — "
        "real archetype copy from product on day-5+]"
    ),
    "therapist": (
        "[v2 phase-1 day-4 Layer 2 therapist archetype placeholder — "
        "real archetype copy from product on day-5+]"
    ),
    "coach": (
        "[v2 phase-1 day-4 Layer 2 coach archetype placeholder — "
        "real archetype copy from product on day-5+]"
    ),
}

LAYER_4_SEGMENT_BODIES: dict[str, str] = {
    "new": (
        "[v2 phase-1 day-4 Layer 4 user-segment 'new' placeholder — "
        "real segment copy from product on day-5+]"
    ),
    "paying": (
        "[v2 phase-1 day-4 Layer 4 user-segment 'paying' placeholder — "
        "real segment copy from product on day-5+]"
    ),
    "dormant": (
        "[v2 phase-1 day-4 Layer 4 user-segment 'dormant' placeholder — "
        "real segment copy from product on day-5+]"
    ),
}


def upgrade() -> None:
    """Create soul_file_layers table + indexes + seed Layers 1, 2, 4.

    WHAT: runs CREATE TABLE + CREATE INDEX + 7 INSERT statements (1+3+3).
    WHEN: invoked by `alembic upgrade head`.
    WHY:  bootstraps the per-service schema per F3 (schema-per-service)
          + seeds the layers needed for the composer's happy-path tests.
    """
    # Enable pgcrypto for `gen_random_uuid()` — Postgres 13+ ships it.
    # IF NOT EXISTS so re-running the migration in dev doesn't break.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    op.create_table(
        "soul_file_layers",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # SMALLINT 1-4 per E8 — single CHECK constraint enforces range.
        sa.Column("layer", sa.SmallInteger, nullable=False),
        # TEXT (not VARCHAR(n)) — content lengths vary widely; let Postgres
        # toast-compress when needed rather than guess a cap.
        sa.Column("scope_key", sa.Text, nullable=False),
        # On L3 rows only: the archetype the composer joins on to find
        # the matching L2 row. NULL on L1/L2/L4 rows.
        sa.Column("archetype", sa.Text, nullable=True),
        # Soul File body text — the actual content the composer concatenates.
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "version",
            sa.Integer,
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "is_current",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "created_at",
            sa.dialects.postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Future Prompt-Coach edit attribution (creator user_id). NULL on
        # seed rows + Day-4 placeholder content.
        sa.Column("created_by", sa.Text, nullable=True),
        sa.CheckConstraint(
            "layer IN (1, 2, 3, 4)",
            name="soul_file_layers_layer_in_1_to_4",
        ),
    )

    # Partial unique index — enforces "exactly ONE current row per
    # (layer, scope_key)" without rejecting historic non-current rows.
    op.execute(
        "CREATE UNIQUE INDEX soul_file_layers_one_current_per_slot "
        "ON soul_file_layers (layer, scope_key) "
        "WHERE is_current = TRUE;"
    )

    # Rollback / history lookup index — `version DESC` so the most recent
    # version sorts first when listing history for a (layer, scope_key).
    op.execute(
        "CREATE INDEX soul_file_layers_history "
        "ON soul_file_layers (layer, scope_key, version DESC);"
    )

    # Composer hot-path index — partial on `layer` filtered to current
    # rows. Speeds the per-turn lookup of (L1, L2, L3, L4) currents.
    op.execute(
        "CREATE INDEX soul_file_layers_current_by_layer "
        "ON soul_file_layers (layer) "
        "WHERE is_current = TRUE;"
    )

    # -----------------------------------------------------------------------
    # Seeds — every layer gets a placeholder body. Layer 3 (per-influencer)
    # NOT seeded — that's the F11 data port from chat-ai, deferred to the
    # Day-4.5 data-port PR per the Day-4 directive's "Out of scope" list.
    # -----------------------------------------------------------------------

    # Layer 1 — global. One row, scope_key='' (empty string per directive).
    op.execute(
        sa.text(
            "INSERT INTO soul_file_layers (layer, scope_key, body) "
            "VALUES (1, '', :body);"
        ).bindparams(body=LAYER_1_GLOBAL_BODY)
    )

    # Layer 2 — archetypes. 3 rows, one per archetype.
    for archetype_name, body in LAYER_2_ARCHETYPE_BODIES.items():
        op.execute(
            sa.text(
                "INSERT INTO soul_file_layers (layer, scope_key, body) "
                "VALUES (2, :scope_key, :body);"
            ).bindparams(scope_key=archetype_name, body=body)
        )

    # Layer 4 — user segments. 3 rows, one per segment.
    for segment_name, body in LAYER_4_SEGMENT_BODIES.items():
        op.execute(
            sa.text(
                "INSERT INTO soul_file_layers (layer, scope_key, body) "
                "VALUES (4, :scope_key, :body);"
            ).bindparams(scope_key=segment_name, body=body)
        )


def downgrade() -> None:
    """Drop the soul_file_layers table + its indexes.

    WHAT: reverses `upgrade()` cleanly; alembic-downgrade-base round-trip
          is a CI test (`test_schema_migrations.py`).
    WHEN: invoked by `alembic downgrade base` (or `downgrade -1` from head).
    WHY:  required by the Day-4 directive's "alembic upgrade + downgrade
          round-trip succeeds" test. Also required by CONSTRAINTS H11
          spirit: every migration must be reversible without manual SQL.
    """
    # Indexes get dropped automatically with the table; explicit DROP
    # INDEX statements would be redundant. The CHECK constraint also
    # goes with the table.
    op.drop_table("soul_file_layers")

    # Leave pgcrypto installed — other future migrations may need it.
    # CONSTRAINTS A1 spirit: don't delete what we don't have to.


# ===========================================================================
# RELATED FILES:
#   ../env.py                     — Alembic environment that invokes this
#   ../../models/soul_file.py     — Pydantic shapes that match this schema
#   ../../repository/soul_file_repository.py
#                                  — issues SELECT/INSERT against this table
#   ../../composer/four_layer_composer.py
#                                  — relies on the partial unique index for
#                                    correctness (exactly-one-current
#                                    guarantee on each L1/L2/L4 lookup)
#   ../../../tests/test_schema_migrations.py
#                                  — alembic up/down round-trip CI gate
#   ../../../PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md
#                                  — pre-spawn engineering contracts this
#                                    migration is the first concrete delivery of
# ===========================================================================
