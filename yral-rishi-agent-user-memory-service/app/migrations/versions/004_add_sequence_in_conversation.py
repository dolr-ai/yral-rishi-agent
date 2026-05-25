# ---------------------------------------------------------------------------
# 004_add_sequence_in_conversation.py — add a monotonic sequence column to
#   messages for deterministic intra-conversation ordering.
#
# ⭐ START HERE: this migration closes the ordering ambiguity identified in
# Codex round-7 (PR #147). When two messages share the same created_at
# timestamp (the normal case for a batch POST — user + assistant in one
# transaction), PostgreSQL has no stable secondary sort key unless one is
# provided. Before this migration the tiebreaker was `id` (UUIDv4), which
# is random — giving a ~50% chance that the assistant reply sorts before the
# user message on any given query.
#
# FIX: add `sequence_in_conversation INTEGER NOT NULL DEFAULT 0`.
# `append_messages` assigns a per-conversation counter at INSERT time;
# the ETL Phase 4 backfill assigns the correct values for migrated messages.
# All ORDER BY clauses that previously used `id` as a tiebreaker now use
# `sequence_in_conversation`.
#
# SCHEMA CHANGE (non-destructive):
#   ALTER TABLE messages
#     ADD COLUMN sequence_in_conversation INTEGER NOT NULL DEFAULT 0;
#   — DEFAULT 0 means existing rows get 0; the ETL Phase 4 and any
#     pre-existing test data need the Phase 4 backfill to have non-zero values.
#
# BACKFILL (in upgrade()):
#   UPDATE messages
#   SET sequence_in_conversation = sub.rn
#   FROM (
#       SELECT id, ROW_NUMBER() OVER (
#           PARTITION BY conversation_id
#           ORDER BY created_at ASC, id ASC
#       ) AS rn FROM messages
#   ) sub
#   WHERE messages.id = sub.id;
#   — runs immediately after ALTER TABLE so the testcontainers environment
#     never sees rows with sequence=0 after upgrade().
#
# INDEX:
#   CREATE INDEX messages_by_conversation_sequence_idx
#     ON messages (conversation_id, created_at ASC, sequence_in_conversation ASC);
#   — supports ORDER BY (conversation_id, created_at, sequence_in_conversation)
#     used by list_messages.
#
# NON-DESTRUCTIVE:
#   Adding a column with a NOT NULL DEFAULT is a metadata-only operation in
#   Postgres 11+; it does not rewrite the table. The backfill UPDATE IS a full
#   table scan but runs once at migration time on a small staging table.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from alembic import op


# --- revision metadata -------------------------------------------------------

# revision: unique string identifier for this migration step.
revision = "004_add_sequence_in_conversation"

# down_revision: immediately preceding migration in the linear chain.
down_revision = "003_add_dedup_indexes"

branch_labels = None
depends_on = None


# --- migration body ----------------------------------------------------------


def upgrade() -> None:
    """Add sequence_in_conversation column, backfill, and add supporting index.

    WHAT: (1) adds the column with NOT NULL DEFAULT 0, (2) backfills all
          existing rows using ROW_NUMBER() OVER (PARTITION BY conversation_id
          ORDER BY created_at ASC, id ASC) so every row has a unique ordinal
          within its conversation, (3) creates a composite index to support
          ORDER BY (conversation_id, created_at, sequence_in_conversation).
    WHEN: run once per database instance that has 001 + 002 + 003 applied.
          Applied automatically by `alembic upgrade head` on fresh environments
          (testcontainers) and by the coordinator operator-action on staging
          and production.
    WHY:  without this column, messages sharing the same created_at timestamp
          (typical for orchestrator batch inserts of user + assistant in one
          transaction) have no stable secondary sort — PostgreSQL may return
          them in different physical orders on different scans, causing the
          assistant reply to appear before the user message in the LLM context
          window or on the mobile chat transcript.
    """
    # --- Step 1: add the column -------------------------------------------
    # DEFAULT 0 is used so existing rows get a sentinel value that the ETL
    # Phase 4 backfill then replaces with the correct ROW_NUMBER() value.
    # NOT NULL ensures every new row written by append_messages always has
    # a real sequence assigned (the route handler pre-queries MAX() + 1).
    op.execute(
        """
        ALTER TABLE messages
        ADD COLUMN sequence_in_conversation INTEGER NOT NULL DEFAULT 0
        """
    )

    # --- Step 2: backfill existing rows with ROW_NUMBER() -----------------
    # WHY ROW_NUMBER() and not just id: id is UUIDv4 (random), so sorting by
    # id alone gives an arbitrary ordering. ROW_NUMBER() OVER (ORDER BY
    # created_at ASC, id ASC) assigns 1-based ordinals in the same order the
    # original query uses — oldest message in each conversation gets 1, next 2,
    # etc. The id tiebreaker inside the window is stable (UUID is deterministic
    # given the same table scan) and matches the ORDER BY the list_messages
    # route used before this migration, preserving backwards compatibility.
    op.execute(
        """
        UPDATE messages
        SET sequence_in_conversation = sub.rn
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY conversation_id
                       ORDER BY created_at ASC, id ASC
                   ) AS rn
            FROM messages
        ) sub
        WHERE messages.id = sub.id
        """
    )

    # --- Step 3: add supporting index ------------------------------------
    # WHY composite (conversation_id, created_at, sequence_in_conversation):
    # list_messages ORDER BY (created_at DESC, sequence_in_conversation DESC)
    # is always scoped to a single conversation_id (WHERE conversation_id = $1).
    # The existing messages_by_conversation_time_idx covers (conversation_id,
    # created_at) — adding sequence_in_conversation as the third column lets
    # Postgres resolve the sort in the index without a table heap fetch for
    # the sequence value.
    op.execute(
        """
        CREATE INDEX messages_by_conversation_sequence_idx
        ON messages (conversation_id, created_at ASC, sequence_in_conversation ASC)
        """
    )


def downgrade() -> None:
    """Drop the sequence index and column, reverting to pre-004 state.

    WHAT: drops messages_by_conversation_sequence_idx first (dependent on the
          column), then drops the sequence_in_conversation column.
    WHEN: testcontainers teardown. Running on the Patroni cluster requires
          Rishi YES per A14 because downgrading restores UUID-sort ordering
          for same-timestamp batches (regression risk for live LLM context
          + mobile transcript ordering).
    WHY:  migration reversibility per H11 spirit.
    """
    # Drop the index first — it depends on the column.
    op.execute(
        "DROP INDEX IF EXISTS messages_by_conversation_sequence_idx;"
    )
    # Drop the column — CASCADE not needed since only the index referenced it.
    op.execute(
        "ALTER TABLE messages DROP COLUMN IF EXISTS sequence_in_conversation;"
    )


# ===========================================================================
# RELATED FILES:
#   003_add_dedup_indexes.py         — preceding migration in the chain
#   ../../../app/api/conversation_routes.py
#                                    — append_messages assigns sequence_in_conversation
#                                      per INSERT; list_messages ORDER BY uses it
#                                      as tiebreaker instead of id
#   ../../../../etl-scripts/chat_ai_to_user_memory_etl.py
#                                    — Phase 4 backfill_sequence_in_conversation()
#                                      corrects migrated rows (which land with
#                                      sequence_in_conversation = 0 from DEFAULT)
#   ../../../../tests/test_conversation_routes.py
#                                    — test_messages_ordering_with_same_created_at_timestamp
#                                      now asserts roles == ["user", "assistant"]
#                                      (ordered equality, not set equality)
#   ../../../../tests/test_schema_migrations.py
#                                    — should add a test for the new column + index
# ===========================================================================
