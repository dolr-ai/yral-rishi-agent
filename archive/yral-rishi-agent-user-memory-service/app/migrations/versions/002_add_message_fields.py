# ---------------------------------------------------------------------------
# 002_add_message_fields.py — add client_message_id + count_toward_paywall
#   to the messages table.
#
# ⭐ START HERE: Deliverable 2 adds two columns needed to return the
# wire-canonical MessageResponse shape (per the locked contract in
# yral-rishi-agent-public-api/app/api/response_models.py):
#
#   client_message_id TEXT (nullable):
#     Mobile-side dedup ID. Sent by mobile on POST messages so the storage
#     layer can detect retries (F10 per-user idempotency). Null for
#     assistant + system messages (AI replies carry no client-side ID).
#
#   count_toward_paywall BOOLEAN NOT NULL DEFAULT TRUE:
#     E7 paywall counter. TRUE = counts toward the 50-message per-user
#     limit. The orchestrator sets this per-message at creation time;
#     FALSE for system-event messages (auto-greet etc.) that must NOT
#     burn the user's limit. Defaults TRUE — fail-safe: if the orchestrator
#     omits this field the message is conservatively counted rather than
#     silently exempted.
#
# WHY A SEPARATE MIGRATION (not merged into 001)?
# 001 was merged and applied on 2026-05-22. Alembic treats each migration
# as an atomic, append-only unit; editing 001 after any instance has
# applied it would break the revision chain. 002 is the correct pattern
# per H11 (backward-safe schema changes as separate migrations). The two
# new columns are also semantically part of the RPC endpoint layer
# (Deliverable 2), not the base schema (Deliverable 1) — the split in
# migrations matches the split in deliverables.
#
# NON-DESTRUCTIVE: both columns are safe to add to an existing table:
#   - client_message_id TEXT is nullable — existing rows get NULL,
#     which is the correct default (pre-D2 messages had no client IDs).
#   - count_toward_paywall has a NOT NULL DEFAULT TRUE — existing rows
#     get TRUE, the conservative fail-safe value described above.
#
# A1 JUSTIFICATION FOR THE downgrade() FUNCTION:
# The downgrade() function drops two columns from messages. See the full
# rationale in 001_initial_schema.py's A1 JUSTIFICATION block — the same
# logic applies here: test-only ephemeral DB (testcontainers Postgres);
# no real user data in the columns at the time downgrade runs in tests.
# Running downgrade on the Patroni cluster requires Rishi YES per
# A14 + SECURITY.md (these columns hold user session metadata once real
# messages are persisted).
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from alembic import op


# --- revision metadata -------------------------------------------------------

# revision: string identifier for this migration step. Must be unique.
revision = "002_add_message_fields"

# down_revision: the immediately preceding migration. Alembic uses this to
# build the linear chain and to determine what "downgrade -1" means.
down_revision = "001_initial_schema"

branch_labels = None
depends_on = None


# --- migration body ----------------------------------------------------------


def upgrade() -> None:
    """Add client_message_id and count_toward_paywall to the messages table.

    WHAT: two ALTER TABLE ADD COLUMN statements against the messages table.
          Non-destructive: adds nullable + defaulted columns so existing
          rows remain valid without any backfill.
    WHEN: run once per database instance that already has 001 applied.
          Applied automatically by `alembic upgrade head` on fresh envs
          and by the Deliverable-2 deploy operator-action on staging.
    WHY:  MessageResponse in the locked contract has both fields; they
          cannot be omitted from the wire shape per A8 + A16.
    """
    # client_message_id: nullable TEXT.
    # Role: mobile sends this on user messages so the storage layer can
    # detect and deduplicate POSTs from retries (F10 idempotency). Null
    # on assistant + system messages — AI replies have no client-side ID.
    op.execute(
        "ALTER TABLE messages ADD COLUMN client_message_id TEXT;"
    )

    # count_toward_paywall: not-nullable BOOLEAN defaults TRUE.
    # Role: E7 paywall counter — orchestrator sets FALSE for auto-greet
    # and system events that should not count against the user's 50-message
    # limit. Default TRUE is the fail-safe direction (see file header).
    op.execute(
        "ALTER TABLE messages "
        "ADD COLUMN count_toward_paywall BOOLEAN NOT NULL DEFAULT TRUE;"
    )


def downgrade() -> None:
    """Remove client_message_id and count_toward_paywall from messages.

    WHAT: reverses upgrade() — drops the two columns in reverse order
          of creation (count_toward_paywall first, then client_message_id).
    WHEN: test teardown (testcontainers) only. Running on the Patroni
          cluster requires Rishi YES per A14 + SECURITY.md because the
          columns hold user session metadata once real messages exist.
    WHY:  migration reversibility per H11 spirit. See A1 JUSTIFICATION in
          the file header for why this drop is safe in test environments.
    """
    # Drop in reverse order of addition. No CASCADE needed — no FK or
    # secondary index references either column directly.
    op.execute(
        "ALTER TABLE messages DROP COLUMN IF EXISTS count_toward_paywall;"
    )
    op.execute(
        "ALTER TABLE messages DROP COLUMN IF EXISTS client_message_id;"
    )


# ===========================================================================
# RELATED FILES:
#   001_initial_schema.py            — root migration this builds upon;
#                                      A1 JUSTIFICATION precedent lives there
#   ../../../app/api/models.py       — MessageResponse shape the columns fill
#   ../../../app/api/conversation_routes.py
#                                    — INSERT statements that write these cols
#   ../../../../yral-rishi-agent-public-api/app/api/response_models.py
#                                    — MessageResponse wire contract that drove
#                                      the column design (field-for-field match)
# ===========================================================================
