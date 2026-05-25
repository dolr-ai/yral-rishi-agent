# ---------------------------------------------------------------------------
# 005_add_sequence_unique_constraint.py — add a UNIQUE constraint on
#   (conversation_id, sequence_in_conversation) to eliminate the race
#   condition identified in Codex round-10 (PR #147).
#
# ⭐ START HERE: this migration closes the race condition left open by
# migration 004. Migration 004 added the `sequence_in_conversation` column
# and assigned values via SELECT MAX() + 1 in the application loop.
#
# THE RACE (pre-005 behaviour):
# Under READ COMMITTED isolation, two concurrent append_messages callers
# both read MAX(sequence_in_conversation) = N before either commits, then
# both INSERT with sequence = N + 1. Both inserts succeed (no constraint
# blocked them). Result: two messages with the same
# (conversation_id, sequence_in_conversation) — the ordering contract is
# broken silently and the LLM context window or mobile transcript can
# show duplicate bubbles or inverted turn order.
#
# THE FIX (two parts):
#   1. Application-level (conversation_routes.py): replace the
#      SELECT-MAX-then-INSERT loop with an inline-subquery INSERT that
#      computes the next sequence atomically within the INSERT statement.
#      The inline subquery still falls under READ COMMITTED semantics,
#      so a concurrent race is still possible — but the UNIQUE constraint
#      is the hard safety net.
#   2. DB-level (this migration): add a UNIQUE constraint on
#      (conversation_id, sequence_in_conversation). Any concurrent INSERT
#      that sneaks through the inline-subquery race is rejected by Postgres
#      with UniqueViolationError; append_messages catches that error on a
#      savepoint boundary and retries up to _SEQUENCE_RETRY_LIMIT times
#      with a fresh MAX read.
#
# SCHEMA CHANGE:
#   Step 1 — ROW_NUMBER() backfill (idempotent):
#     Deduplicates any rows that got the same sequence during the pre-005
#     bug window. Must run before ADD CONSTRAINT (which fails on duplicates).
#   Step 2 — DROP INDEX messages_by_conversation_sequence_idx (migration 004):
#     The 3-column composite index (conversation_id, created_at,
#     sequence_in_conversation) is superseded by the 2-column UNIQUE index
#     created in step 3. The existing messages_by_conversation_time_idx
#     (from migration 001) still covers the (conversation_id, created_at)
#     prefix for ORDER BY (created_at DESC, ...) scans.
#   Step 3 — CREATE UNIQUE INDEX messages_conversation_sequence_unique_idx
#     ON messages (conversation_id, sequence_in_conversation)
#   Step 4 — ALTER TABLE messages ADD CONSTRAINT
#     messages_conversation_sequence_unique
#     UNIQUE USING INDEX messages_conversation_sequence_unique_idx
#     → the constraint name is used by append_messages's retry check:
#       `if conflict.constraint_name != "messages_conversation_sequence_unique": raise`
#
# NON-DESTRUCTIVE:
#   Adding a UNIQUE constraint checks for existing duplicates and fails if
#   any are found. Step 1 deduplicates first, making step 4 safe.
#   Dropping the old composite index (step 2) does not affect data or
#   correctness — only query plan efficiency for ORDER BY scans, which
#   the time-index still handles.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from alembic import op


# --- revision metadata -------------------------------------------------------

# revision: unique string identifier for this migration step.
revision = "005_add_sequence_unique_constraint"

# down_revision: immediately preceding migration in the linear chain.
down_revision = "004_add_sequence_in_conversation"

branch_labels = None
depends_on = None


# --- migration body ----------------------------------------------------------


def upgrade() -> None:
    """Deduplicate sequences, drop old index, add UNIQUE constraint.

    WHAT: (1) re-runs the ROW_NUMBER() backfill to fix any duplicate
          sequence values produced by the pre-005 race condition;
          (2) drops the non-unique composite index from migration 004;
          (3) creates a UNIQUE INDEX on (conversation_id, sequence_in_conversation);
          (4) promotes the index to a named UNIQUE constraint so asyncpg's
          UniqueViolationError carries the constraint name for retry logic.
    WHEN: run once per database instance after migration 004.
    WHY:  the SELECT-MAX + INSERT pattern in pre-005 append_messages is
          non-atomic under READ COMMITTED isolation. Without a uniqueness
          constraint, concurrent callers silently write duplicate sequence
          ordinals. With this constraint, the second writer gets a
          UniqueViolationError that the application can catch and retry.
    """
    # --- Step 1: deduplicate existing sequences ----------------------------
    # Re-run migration 004's ROW_NUMBER() backfill to assign unique ordinals
    # to any rows that collided during the pre-005 race window.
    # Must run BEFORE ADD CONSTRAINT — Postgres rejects ADD UNIQUE if any
    # duplicate (conversation_id, sequence_in_conversation) pairs exist.
    # Idempotent: ROW_NUMBER() assigns the same 1-based ordinals every run
    # (ordered by created_at ASC, id ASC within each conversation).
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

    # --- Step 2: drop migration 004's composite index ----------------------
    # The composite index (conversation_id, created_at ASC, sequence_in_conversation ASC)
    # from migration 004 served ORDER BY (created_at, sequence_in_conversation)
    # queries. It is superseded by two surviving indexes:
    #   - messages_by_conversation_time_idx (from migration 001):
    #     covers (conversation_id, created_at) for ORDER BY created_at scans.
    #   - messages_conversation_sequence_unique_idx (step 3):
    #     covers (conversation_id, sequence_in_conversation) for MAX() lookups.
    op.execute(
        "DROP INDEX IF EXISTS messages_by_conversation_sequence_idx;"
    )

    # --- Step 3: create the UNIQUE index -----------------------------------
    # UNIQUE enforces that no two messages in the same conversation share a
    # sequence number. Postgres uses this B-tree index to enforce the constraint
    # added in step 4 and to satisfy MAX(sequence_in_conversation) lookups
    # efficiently (single-page descent to the rightmost leaf).
    op.execute(
        """
        CREATE UNIQUE INDEX messages_conversation_sequence_unique_idx
        ON messages (conversation_id, sequence_in_conversation)
        """
    )

    # --- Step 4: promote the index to a named constraint ------------------
    # ADD CONSTRAINT ... UNIQUE USING INDEX transfers ownership of the index
    # to the constraint. The constraint name "messages_conversation_sequence_unique"
    # is the string append_messages checks in its UniqueViolationError retry:
    #   if conflict.constraint_name != "messages_conversation_sequence_unique": raise
    # Owning the index means DROP CONSTRAINT also drops the backing index —
    # clean rollback in downgrade().
    op.execute(
        """
        ALTER TABLE messages
        ADD CONSTRAINT messages_conversation_sequence_unique
        UNIQUE USING INDEX messages_conversation_sequence_unique_idx
        """
    )


def downgrade() -> None:
    """Drop the unique constraint and restore migration 004's composite index.

    WHAT: (1) drops the named unique constraint, which also drops the backing
          UNIQUE INDEX (since the index was promoted to the constraint in
          upgrade step 4); (2) recreates migration 004's composite index.
    WHEN: testcontainers teardown. Running on the Patroni cluster requires
          Rishi YES (A14) — restoring the pre-005 code path alongside this
          downgrade reintroduces the sequence race condition.
    WHY:  migration reversibility per H11 spirit.
    """
    # Drop the constraint — this also drops messages_conversation_sequence_unique_idx
    # (the backing index is owned by the constraint after upgrade step 4).
    op.execute(
        "ALTER TABLE messages DROP CONSTRAINT messages_conversation_sequence_unique;"
    )
    # Restore migration 004's composite index so that ORDER BY
    # (created_at, sequence_in_conversation) queries are index-supported again.
    op.execute(
        """
        CREATE INDEX messages_by_conversation_sequence_idx
        ON messages (conversation_id, created_at ASC, sequence_in_conversation ASC)
        """
    )


# ===========================================================================
# RELATED FILES:
#   004_add_sequence_in_conversation.py
#                                    — preceding migration; added the column
#                                      and the original composite index
#                                      that this migration drops
#   ../../../app/api/conversation_routes.py
#                                    — append_messages now uses an inline-
#                                      subquery INSERT + savepoint retry on
#                                      messages_conversation_sequence_unique
#                                      (UniqueViolationError)
#   ../../../../tests/test_schema_migrations.py
#                                    — test_migration_005_sequence_unique_index_exists
#                                      verifies messages_conversation_sequence_unique_idx
# ===========================================================================
