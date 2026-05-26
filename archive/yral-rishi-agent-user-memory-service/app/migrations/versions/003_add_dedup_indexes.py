# ---------------------------------------------------------------------------
# 003_add_dedup_indexes.py — unique indexes for race-condition-free upsert
#   and client_message_id deduplication.
#
# ⭐ START HERE: this migration adds two partial unique indexes that close
# two data-integrity gaps identified in Codex round-2 review (PR #132):
#
#   1. conversations_natural_key_active_unique_idx
#      Enforces uniqueness of the conversation "natural key"
#      (user_id, conversation_type, influencer_id, participant_b_id)
#      among ACTIVE (non-soft-deleted) rows. COALESCE converts NULL
#      influencer_id and participant_b_id to '' so two NULL values match
#      each other (standard unique constraints treat two NULLs as unequal
#      in PostgreSQL; this expression index corrects that).
#
#      WHY: without this index, two concurrent POST /v1/conversations calls
#      with the same natural key can both do a SELECT (both return 0 rows),
#      both run INSERT, and create duplicate conversation rows. Once this
#      index exists, the INSERT in conversation_routes.py uses
#      "ON CONFLICT ... DO UPDATE" which is atomic — one winner inserts,
#      the other hits the conflict and gets the existing row back.
#
#   2. messages_client_message_id_dedup_idx
#      Enforces uniqueness of (conversation_id, client_message_id) among
#      rows WHERE client_message_id IS NOT NULL. Partial index: rows with
#      NULL client_message_id (system + assistant messages) are excluded
#      — they cannot carry client-side dedup IDs, so duplicates there are
#      handled by the normal PK uniqueness.
#
#      WHY: mobile retries a POST /v1/conversations/{id}/messages after a
#      network blip. If the original write committed, the retry must NOT
#      create a second message row (that would double-charge the paywall
#      and show a duplicate bubble on screen). The route handler uses
#      "ON CONFLICT DO NOTHING" — if the row already exists, it returns
#      the existing row; no duplicate inserted.
#
# NON-DESTRUCTIVE: both statements create indexes only; no column or
# row data is modified. Safe to apply to an existing Patroni cluster
# while the service is running (Postgres builds the index without locking
# reads/writes on the table). `CREATE INDEX` without CONCURRENTLY does
# lock, but these are migration-time ops on a small table (pre-ETL).
#
# A1 JUSTIFICATION FOR downgrade():
# The downgrade() drops both indexes. Per the precedent in
# 001_initial_schema.py: in testcontainers environments, no real user
# data is affected. In staging / production, running downgrade requires
# Rishi YES per A14 because the concurrent-safe upsert and dedup logic
# in conversation_routes.py depend on these indexes being present.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from alembic import op


# --- revision metadata -------------------------------------------------------

# revision: unique string identifier for this migration step.
revision = "003_add_dedup_indexes"

# down_revision: the immediately preceding migration in the linear chain.
down_revision = "002_add_message_fields"

branch_labels = None
depends_on = None


# --- migration body ----------------------------------------------------------


def upgrade() -> None:
    """Add two partial unique indexes for upsert correctness and dedup.

    WHAT: creates `conversations_natural_key_active_unique_idx` using
          COALESCE to handle NULLable key columns, and
          `messages_client_message_id_dedup_idx` for client-side
          message deduplication.
    WHEN: run once per database instance that has 001 + 002 applied.
          Applied automatically by `alembic upgrade head` on fresh envs
          and by the D2 cluster operator-action on staging/production.
    WHY:  without these indexes, concurrent upsert + retry scenarios can
          produce duplicate rows — see file header for the full race
          condition and retry analyses.
    """
    # --- Index 1: conversation natural-key uniqueness (active rows) ---------
    # WHY COALESCE: PostgreSQL unique indexes treat each NULL as distinct
    # from every other NULL (NULL != NULL in unique constraint checks). Two
    # NULL influencer_ids would not conflict under a plain unique index.
    # COALESCE(influencer_id, '') maps NULL → '' so two "no influencer"
    # conversations for the same user+type DO conflict — which is the
    # desired behaviour.
    # WHY PARTIAL (WHERE soft_deleted_at IS NULL): soft-deleted rows are
    # logically removed from the active namespace. A user can re-create a
    # fresh conversation of the same type after soft-deleting the old one.
    op.execute(
        """
        CREATE UNIQUE INDEX conversations_natural_key_active_unique_idx
        ON conversations (
            user_id,
            conversation_type,
            COALESCE(influencer_id, ''),
            COALESCE(participant_b_id, '')
        )
        WHERE soft_deleted_at IS NULL
        """
    )

    # --- Index 2: message deduplication by client_message_id ----------------
    # WHY PARTIAL (WHERE client_message_id IS NOT NULL): only user messages
    # carry a client_message_id (mobile sends it; AI replies don't). The
    # partial predicate excludes the NULL rows so each assistant/system
    # message can be inserted without constraint interference.
    # WHY (conversation_id, client_message_id) together: the ID is scoped
    # per conversation — the same mobile client could reuse the same ID in
    # two different conversations without collision.
    op.execute(
        """
        CREATE UNIQUE INDEX messages_client_message_id_dedup_idx
        ON messages (conversation_id, client_message_id)
        WHERE client_message_id IS NOT NULL
        """
    )


def downgrade() -> None:
    """Drop both unique indexes, reverting to pre-003 state.

    WHAT: drops `messages_client_message_id_dedup_idx` first (no FK
          dependencies), then `conversations_natural_key_active_unique_idx`.
    WHEN: test teardown (testcontainers round-trip) only. Running on the
          Patroni cluster requires Rishi YES per A14 + SECURITY.md because
          downgrading removes the safety net for the concurrent upsert +
          retry paths used by live traffic.
    WHY:  migration reversibility per H11 spirit.
    """
    # Drop message dedup index first — no dependencies.
    op.execute(
        "DROP INDEX IF EXISTS messages_client_message_id_dedup_idx;"
    )
    # Drop conversation uniqueness index.
    op.execute(
        "DROP INDEX IF EXISTS conversations_natural_key_active_unique_idx;"
    )


# ===========================================================================
# RELATED FILES:
#   002_add_message_fields.py        — preceding migration in the chain;
#                                      adds client_message_id column that
#                                      messages_client_message_id_dedup_idx
#                                      indexes
#   ../../../app/api/conversation_routes.py
#                                    — INSERT ... ON CONFLICT DO UPDATE uses
#                                      conversations_natural_key_active_unique_idx;
#                                      INSERT ... ON CONFLICT DO NOTHING uses
#                                      messages_client_message_id_dedup_idx
#   ../../../../tests/test_conversation_routes.py
#                                    — concurrent upsert + idempotency tests
#                                      that require these indexes to pass
#   ../../../../tests/test_schema_migrations.py
#                                    — tests_migration_003_unique_indexes_exist
#                                      verifies these indexes are present
# ===========================================================================
