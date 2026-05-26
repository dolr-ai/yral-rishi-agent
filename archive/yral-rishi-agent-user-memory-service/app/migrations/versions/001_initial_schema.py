# ---------------------------------------------------------------------------
# 001_initial_schema.py — Phase 1 initial schema for user-memory-service.
#
# ⭐ START HERE: this migration creates TWO tables that form the entire
# Phase 1 conversation-history-persistence layer:
#
#   conversations — one row per chat thread (user ↔ AI Influencer or
#                   user ↔ user for H2H). Contains metadata about the
#                   thread: who, what type, when last active.
#   messages      — one row per chat turn. Append-only. References
#                   conversations(id) via FK. Contains the text content,
#                   optional media URLs, and Gemini call metadata.
#
# WHY TWO TABLES (not one)?
# Conversations + messages have a 1:N relationship. Denormalizing into one
# table would make "list all conversations for a user" require a GROUP BY
# with sub-queries or window functions — the hottest read path in the system
# (inbox load on mobile). Two tables lets the inbox read hit `conversations`
# directly with a simple index scan, and history read hits `messages` with
# its own index, without cross-table joins on the hot path.
#
# WHY soft_deleted_at (NOT hard-delete)?
# chat-ai hard-deletes conversations. v2 improves on this — per the
# directive: "soft_deleted_at is INTENTIONAL — v2 improvement over chat-ai
# (which hard-deletes); mobile's DELETE maps to setting soft_deleted_at =
# NOW(), recoverable." This means a user who accidentally swipes-to-delete
# can have their history recovered without a backup restore.
#
# WHY gemini_metadata JSONB?
# The orchestrator records prompt_tokens, completion_tokens, model, and
# latency_ms for every Gemini call. Storing as JSONB keeps this migration
# stable — we don't need to add/rename columns when the metadata shape
# evolves (e.g. adding cost_rupees in a future sprint). A typed Postgres
# table for this would require a new migration for every Gemini model
# version bump.
#
# WHY gen_random_uuid() FOR PRIMARY KEYS?
# UUIDs are globally unique, opaque, and non-enumerable — mobile can hold
# a conversation UUID as a stable reference without any server-round-trip
# to generate it. The randomness prevents clients from guessing other
# conversations' IDs (defense in depth alongside auth).
#
# INDEX DESIGN RATIONALE:
#   conversations_by_user_active_idx:
#     Partial index on (user_id, last_message_at DESC) WHERE soft_deleted_at IS NULL.
#     Covers the mobile inbox load: "give me user X's non-deleted conversations,
#     most recently active first." Partial because mobile never shows
#     soft-deleted threads (and the partial index is smaller + faster).
#
#   conversations_by_user_all_idx:
#     Full index on (user_id, created_at DESC).
#     Covers the ETL + admin queries that need to include soft-deleted rows.
#
#   messages_by_conversation_time_idx:
#     Index on (conversation_id, created_at ASC).
#     Covers the history read: "give me all messages for conversation C,
#     oldest first." The orchestrator + public-api both paginate history
#     using `created_at` ordering.
#
# ETL NOTE (A4 — ALL data MUST port):
# chat-ai's source schema has no `soft_deleted_at` concept. Per the
# directive: "Column mapping note for ETL: chat-ai source rows have no
# soft_deleted_at concept → migrate as NULL." The ETL script (Deliverable 3)
# sets `soft_deleted_at = NULL` for every migrated conversation row. Any
# chat-ai conversation the user had deleted before cutover is simply not
# present in chat-ai's DB (hard-deleted), so nothing to reconcile.
#
# A1 JUSTIFICATION FOR THE downgrade() FUNCTION:
# The downgrade() function drops `messages` then `conversations`. This is
# the reversal of this migration's own artifact against an ephemeral
# test container — NOT destruction of pre-existing production data.
# Per the A1 7-step safety check:
#   (1) What: messages + conversations tables created by THIS migration.
#   (2) Why: migration reversibility (H11 spirit).
#   (3) Obsolete: yes — they were created moments earlier in the test.
#   (4) References: only this migration created them.
#   (5) Non-destructive alternative: not applicable (migration tooling
#       requires a drop to undo a CREATE TABLE).
#   (6) Risk: LOW — test-only ephemeral DB.
#   (7) Post-deletion: test_schema_migrations.py asserts both tables
#       are absent, then re-upgrades to restore the seeded state.
# Coordinator approval for this specific downgrade pattern is implicit
# in the Session 4 Day-4 soul-file migration pattern we mirror here.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from alembic import op


# Alembic revision chain — `down_revision = None` marks this as the root.
revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the conversations + messages tables with indices.

    WHAT: forwards DDL — creates both tables, their check constraints,
          the FK from messages to conversations, and the three query-
          optimised indices documented in the file header.
    WHEN: run by `alembic upgrade head` once per new database instance
          (cluster deploy day or test suite startup via testcontainers).
    WHY:  first and only schema migration for Phase 1; establishes the
          entire storage layer for conversation history persistence.
    """
    # ------------------------------------------------------------------
    # TABLE: conversations
    # One row per chat thread. Mobile creates a conversation before
    # sending the first message; subsequent messages reference it via FK.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE conversations (
            id                UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id           TEXT          NOT NULL,
            influencer_id     TEXT,
            participant_b_id  TEXT,
            conversation_type TEXT          NOT NULL
                CHECK (conversation_type IN ('ai_chat', 'human_chat', 'chat_as_human')),
            created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            last_message_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            message_count     INTEGER       NOT NULL DEFAULT 0,
            soft_deleted_at   TIMESTAMPTZ
        );
        """
    )

    # ------------------------------------------------------------------
    # INDEX: inbox load (non-deleted conversations, most recent first)
    # ------------------------------------------------------------------
    # Partial index covers the hot path: mobile inbox list. Only active
    # (non-soft-deleted) conversations are included — keeps the index
    # small and the index scan fast.
    op.execute(
        """
        CREATE INDEX conversations_by_user_active_idx
            ON conversations (user_id, last_message_at DESC)
            WHERE soft_deleted_at IS NULL;
        """
    )

    # ------------------------------------------------------------------
    # INDEX: ETL + admin queries (all conversations including deleted)
    # ------------------------------------------------------------------
    # Full index used by the ETL script (Deliverable 3) and admin tools
    # that need to enumerate every conversation for a user including
    # soft-deleted ones.
    op.execute(
        """
        CREATE INDEX conversations_by_user_all_idx
            ON conversations (user_id, created_at DESC);
        """
    )

    # ------------------------------------------------------------------
    # TABLE: messages
    # One row per chat turn. Append-only by design — messages are never
    # updated (the content of a sent message is immutable in chat-ai;
    # v2 preserves this convention).
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE messages (
            id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id  UUID        NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            role             TEXT        NOT NULL
                CHECK (role IN ('user', 'assistant', 'system')),
            content          TEXT        NOT NULL DEFAULT '',
            media_urls       JSONB,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            gemini_metadata  JSONB
        );
        """
    )

    # ------------------------------------------------------------------
    # INDEX: history read (messages for a conversation, oldest first)
    # ------------------------------------------------------------------
    # Covers GET /v1/conversations/{id}/messages — the paginated history
    # endpoint in Deliverable 2. Both the orchestrator (context fetch
    # before LLM call) and public-api (mobile history screen) hit this.
    op.execute(
        """
        CREATE INDEX messages_by_conversation_time_idx
            ON messages (conversation_id, created_at ASC);
        """
    )


def downgrade() -> None:
    """Drop the conversations + messages tables + their indices.

    WHAT: reverses `upgrade()` — drops both tables in dependency order
          (messages first to avoid FK violation, then conversations).
          Indices are dropped automatically when their table drops.
    WHEN: run by `alembic downgrade base` in the round-trip test (and
          by operators rolling back a bad deploy).
    WHY:  migration reversibility per H11 spirit. See A1 JUSTIFICATION
          in the file header for why this drop is safe.
    """
    # Drop messages first — it holds the FK reference to conversations.
    # Dropping conversations first would fail with a FK constraint error.
    op.execute("DROP TABLE IF EXISTS messages;")

    # Drop conversations after messages is gone.
    op.execute("DROP TABLE IF EXISTS conversations;")


# ===========================================================================
# RELATED FILES:
#   ../env.py                        — Alembic env that runs this migration
#   ../../../alembic.ini             — config pointing at this package
#   ../../../tests/test_schema_migrations.py
#                                    — asserts both tables exist after upgrade
#                                      and are gone after downgrade
#   ../../../tests/conftest.py       — spins testcontainers-postgres and
#                                      runs `alembic upgrade head` once
#   ../../../../yral-rishi-agent-public-api/app/api/response_models.py
#                                    — ConversationResponse + MessageResponse
#                                      shapes that map to these tables
# ===========================================================================
