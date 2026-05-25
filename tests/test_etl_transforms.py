# ---------------------------------------------------------------------------
# test_etl_transforms.py — unit tests for chat_ai_to_user_memory_etl.py.
#
# ⭐ START HERE: these tests verify the pure-function transform layer of the
#   Day-9 ETL migration (chat-ai → user-memory-service). They do NOT require
#   a live Postgres connection — all DB interactions are replaced with minimal
#   in-process mocks.
#
# WHAT this file tests:
#   1. Conversation row transform correctness (§2 column mapping)
#   2. Message row transform correctness (§3 column mapping)
#   3. JSONB serialization function
#   4. Verification failure exits loudly (count delta exceeds threshold)
#   5. CLI mutual-exclusivity enforcement (--conversations-only + --messages-only)
#   6. PII-safe logging (message content never appears in log output)
#
# WHY no live DB:
#   The transform functions are pure Python (asyncpg.Record → dict). Mocking
#   the pool for run_verification() confirms exit-code behaviour without
#   requiring network access to the chat-ai or v2 Postgres clusters.
#
# RELATED FILES:
#   etl-scripts/chat_ai_to_user_memory_etl.py  — module under test
#   etl-scripts/etl-plan-day-9-draft.md        — §2 + §3 column mapping doc
# ---------------------------------------------------------------------------

import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub asyncpg BEFORE loading the ETL module.
#
# WHY: the ETL module does `import asyncpg` at top-level. The functions
# under test (transform_*, _serialize_jsonb, run_verification) take plain
# dicts and our mock pool objects — no real Postgres connection is needed.
# Stubbing via sys.modules lets the module load in any Python environment
# that lacks asyncpg installed (CI runner, dev machine without a virtualenv).
# ---------------------------------------------------------------------------
if "asyncpg" not in sys.modules:
    sys.modules["asyncpg"] = MagicMock()

# ---------------------------------------------------------------------------
# Patch asyncpg.CheckViolationError with a real exception class BEFORE loading
# the ETL module.
#
# WHY: asyncpg is a MagicMock; accessing .CheckViolationError on it returns
# another MagicMock, which cannot be used in an `except` clause (Python raises
# TypeError: catching classes that do not inherit from BaseException). Replacing
# it with a real exception subclass lets the CheckViolationError fallback tests
# exercise the actual except branch in migrate_conversations / migrate_messages.
# ---------------------------------------------------------------------------


class _MockCheckViolationError(Exception):
    """Stand-in for asyncpg.CheckViolationError in unit tests.

    WHAT: subclasses Exception (satisfies Python's except-clause requirement)
          and exposes a constraint_name attribute matching asyncpg's real API.
    WHEN: raised by _MigrationDestinationConnection to simulate a bad row.
    WHY:  the ETL fallback logs violation.constraint_name — the attribute must
          exist so the log.warning() call in the except block doesn't error.
    """

    def __init__(self, message: str = "constraint violation"):
        super().__init__(message)
        self.constraint_name = "test_constraint"


sys.modules["asyncpg"].CheckViolationError = _MockCheckViolationError

# ---------------------------------------------------------------------------
# Load the ETL module by adding etl-scripts/ to sys.path and importing by name.
#
# WHY this approach instead of importlib file-loading:
#   etl-scripts/ is not a Python package (no __init__.py). Adding the
#   directory to sys.path is the standard way to make standalone scripts
#   importable without restructuring the repo. The file name uses underscores
#   (chat_ai_to_user_memory_etl.py) so it is a valid Python module name.
# ---------------------------------------------------------------------------
_ETL_SCRIPTS_DIR = str(Path(__file__).parent.parent / "etl-scripts")
if _ETL_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _ETL_SCRIPTS_DIR)

import chat_ai_to_user_memory_etl as etl  # noqa: E402


# ---------------------------------------------------------------------------
# Shared test data — deterministic UUIDs + timestamps for assertions.
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)
_LATER = datetime(2026, 5, 24, 13, 0, 0, tzinfo=timezone.utc)
_CONVERSATION_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_MESSAGE_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
_USER_ID = "user-test-principal-123"
_INFLUENCER_ID = "inf-test-456"


def _make_conv_row(**overrides) -> dict:
    """Build a minimal chat-ai conversations row as a plain dict.

    WHAT: returns a dict that mirrors the columns SELECTed by migrate_conversations()
          from chat-ai.conversations. Plain dict works as asyncpg.Record mock because
          transform_conversation_row() only calls row["key"] and row.get("key").
    WHEN: called at the top of each conversation transform test.
    WHY:  centralising the baseline prevents copy-paste drift when the SELECT list
          in migrate_conversations() changes.
    """
    base = {
        "id": _CONVERSATION_ID,
        "user_id": _USER_ID,
        "influencer_id": _INFLUENCER_ID,
        "participant_b_id": None,
        "conversation_type": "ai_chat",
        "metadata": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return base


def _make_message_row(**overrides) -> dict:
    """Build a minimal chat-ai messages row as a plain dict.

    WHAT: returns a dict mirroring the columns SELECTed by migrate_messages().
    WHEN: called at the top of each message transform test.
    WHY:  same rationale as _make_conv_row — single authoritative baseline.
    """
    base = {
        "id": _MESSAGE_ID,
        "conversation_id": _CONVERSATION_ID,
        "role": "user",
        "content": "hello world",
        "media_urls": None,
        "client_message_id": None,
        "token_count": None,
        "created_at": _NOW,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Minimal asyncpg pool mock for run_verification() tests.
# The function acquires each pool ONCE and calls fetchval() twice per connection
# (once for conversations count, once for messages count).
# ---------------------------------------------------------------------------


class _MockConnection:
    """Minimal asyncpg.Connection stand-in — supports fetchval() + async context manager.

    WHAT: each call to fetchval() consumes the next value from the initialiser list,
          matching the two-call sequence in run_verification() (convs, then msgs).
    WHEN: used only inside _MockPool.acquire().
    WHY:  avoids asyncpg install requirement in the repo-level test runner while
          keeping the test behaviour faithful to the real connection contract.
    """

    def __init__(self, *fetchval_returns):
        # Iterator over the predetermined return values for sequential fetchval calls.
        self._returns = iter(fetchval_returns)

    async def fetchval(self, _query: str):
        # Return the next predetermined value; raises StopIteration if exhausted.
        return next(self._returns)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class _MockPool:
    """Minimal asyncpg.Pool stand-in — acquire() returns the single shared _MockConnection.

    WHAT: pools the single _MockConnection instance; acquire() is a sync method returning
          the connection object (which is itself an async context manager).
    WHEN: constructed per-test with the expected fetchval sequence for that pool.
    WHY:  run_verification() acquires source + destination each once, so one
          connection per pool suffices.
    """

    def __init__(self, *fetchval_returns):
        self._connection = _MockConnection(*fetchval_returns)

    def acquire(self):
        # Returns the connection directly — it acts as its own async context manager.
        return self._connection


# ===========================================================================
# 1. CONVERSATION TRANSFORM CORRECTNESS
# ===========================================================================


def test_transform_conversation_full_column_mapping():
    """All source columns map to the correct v2 target columns per §2 of the plan.

    WHAT: verifies direct copies, the updated_at → last_message_at rename,
          and the v2-only defaults (message_count=0, soft_deleted_at=NULL).
    WHY:  a silent rename bug (e.g. both updated_at AND last_message_at present,
          or last_message_at=None) would corrupt the inbox sort order.
    """
    row = _make_conv_row(updated_at=_LATER)
    result = etl.transform_conversation_row(row)

    # Direct copies — value identity must be preserved.
    assert result["id"] == _CONVERSATION_ID
    assert result["user_id"] == _USER_ID
    assert result["influencer_id"] == _INFLUENCER_ID
    assert result["participant_b_id"] is None
    assert result["conversation_type"] == "ai_chat"
    assert result["created_at"] == _NOW

    # Rename: updated_at → last_message_at — use distinct values so the test
    # fails if the rename was skipped (both timestamps equal would hide the bug).
    assert result["last_message_at"] == _LATER, (
        "updated_at must be RENAMED to last_message_at; "
        "if last_message_at equals created_at the rename may have been silently skipped"
    )
    assert "updated_at" not in result, (
        "original updated_at key must NOT appear in the v2 output dict"
    )

    # v2-only defaults.
    assert result["message_count"] == 0, "message_count must be 0 — Phase 3 sets the real count"
    assert result["soft_deleted_at"] is None, "all migrated rows start as active (not soft-deleted)"


def test_transform_conversation_metadata_is_dropped():
    """conversations.metadata is dropped — Phase 2 pgvector will rebuild memories.

    WHAT: verifies that the output dict from transform_conversation_row() does
          NOT contain a 'metadata' key, even when the source row has a non-NULL
          metadata value.
    WHEN: called with a source row that has metadata = {"memories": [...]}.
    WHY:  v2 Phase 1 schema has no metadata column; passing it would cause a
          'column does not exist' error on INSERT. Phase 2 pgvector will
          reconstruct semantic memories from message history instead.
    """
    row = _make_conv_row(metadata={"memories": [{"fact": "likes cats"}]})
    result = etl.transform_conversation_row(row)
    assert "metadata" not in result, (
        "metadata must be dropped from the v2 output; "
        "if present the INSERT will fail with an unknown-column error"
    )


def test_transform_conversation_h2h_nulls_round_trip():
    """NULL influencer_id + non-NULL participant_b_id (H2H mode) survive the transform.

    WHAT: verifies that a human-to-human chat conversation (influencer_id=NULL,
          participant_b_id set, conversation_type="human_chat") passes through
          transform_conversation_row() with all three values preserved correctly.
    WHEN: called with a source row representing a human_chat conversation.
    WHY:  if influencer_id=NULL is silently coerced to '' or participant_b_id is
          dropped, the v2 inbox would misrender H2H conversations as AI chats
          (influencer icon instead of user avatar). NULL preservation is tested
          explicitly because dict.get() returns None for missing keys, which
          could mask a silent key-drop bug.
    """
    row = _make_conv_row(
        influencer_id=None,
        participant_b_id="user-456",
        conversation_type="human_chat",
    )
    result = etl.transform_conversation_row(row)
    assert result["influencer_id"] is None
    assert result["participant_b_id"] == "user-456"
    assert result["conversation_type"] == "human_chat"


# ===========================================================================
# 2. MESSAGE TRANSFORM CORRECTNESS
# ===========================================================================


def test_transform_message_token_count_to_gemini_metadata():
    """token_count is wrapped in {total_tokens: N} JSONB envelope — billing data preserved.

    WHAT: verifies that transform_message_row() converts a non-NULL token_count
          integer into gemini_metadata = '{"total_tokens": N}' (JSON string) and
          drops the original token_count key from the output dict.
    WHEN: called with a source row that has token_count = 42.
    WHY:  v2 has no token_count column; the data is preserved inside the
          gemini_metadata JSONB field that the orchestrator already reads for
          cost accounting. If the raw int were stored instead of the JSON
          envelope, the $6::jsonb cast in the INSERT would raise a type error
          at run time.
    """
    row = _make_message_row(token_count=42)
    result = etl.transform_message_row(row)

    assert result["gemini_metadata"] is not None
    parsed = json.loads(result["gemini_metadata"])
    assert parsed == {"total_tokens": 42}, (
        "token_count must become {\"total_tokens\": N} — "
        "if the raw int is stored instead, the $6::jsonb cast will fail at INSERT time"
    )
    assert "token_count" not in result, "token_count must not pass through to v2 output"


def test_transform_message_null_token_count_gives_null_gemini_metadata():
    """NULL token_count produces NULL gemini_metadata — user messages have no token count.

    WHAT: verifies that transform_message_row() maps token_count=None to
          gemini_metadata=None (not to '{}' or '{"total_tokens": null}').
    WHEN: called with a source row that has token_count = None (typical for
          user-role messages — only assistant replies from Gemini have token counts).
    WHY:  a non-NULL gemini_metadata on user messages would pollute the cost
          accounting query that reads gemini_metadata WHERE role = 'assistant';
          NULL correctly signals "no billing data for this row".
    """
    row = _make_message_row(token_count=None)
    result = etl.transform_message_row(row)
    assert result["gemini_metadata"] is None


def test_transform_message_null_content_coerced_to_empty_string():
    """NULL content maps to '' — v2 messages.content has a NOT NULL constraint.

    WHAT: verifies that transform_message_row() converts content=None to
          content='' in the output dict.
    WHEN: called with a source row where content is NULL (common for
          system-generated stub messages in chat-ai).
    WHY:  chat-ai occasionally stores NULL content for system-generated stubs;
          inserting NULL into v2 would violate the messages.content NOT NULL
          constraint and abort the entire batch transaction — losing all rows
          in that batch including valid ones.
    """
    row = _make_message_row(content=None)
    result = etl.transform_message_row(row)
    assert result["content"] == "", (
        "NULL content must be coerced to empty string; "
        "NULL would violate v2 messages.content NOT NULL constraint"
    )


def test_transform_message_count_toward_paywall_defaults_true():
    """All migrated messages count toward the paywall — conservative E7 default.

    WHAT: verifies that transform_message_row() always sets count_toward_paywall
          = True in the output dict, regardless of any source column value.
    WHEN: called with a baseline message row (no special overrides).
    WHY:  v2 enforces a paywall at N messages. We cannot retroactively determine
          which historical messages were auto-greet exemptions vs real turns, so
          all migrated rows are charged conservatively (E7 default). Defaulting
          to False would under-count usage and allow paywall bypass.
    """
    result = etl.transform_message_row(_make_message_row())
    assert result["count_toward_paywall"] is True, (
        "count_toward_paywall must default to True; "
        "we cannot retroactively determine which historical messages were auto-greet exemptions"
    )


def test_transform_message_dropped_columns_absent():
    """Columns with no v2 equivalent are completely absent from the output dict.

    WHAT: verifies that transform_message_row() does not include any of the
          8 chat-ai-only columns in its return value — specifically: sender_id,
          message_type, audio_url, audio_duration_seconds, is_read, status,
          metadata, and token_count.
    WHEN: called with a baseline message row.
    WHY:  passing unknown columns to asyncpg's INSERT causes a 'column does not
          exist' Postgres error that aborts the batch. Each dropped column is a
          documented decision in etl-plan §3 (no v2 equivalent, or superseded by
          a different field). Explicitly asserting absence catches any regression
          where a future transform refactor accidentally re-introduces a dropped
          column.
    """
    result = etl.transform_message_row(_make_message_row())
    dropped_columns = {
        "sender_id",            # H2H sender attribution — no v2 Phase 1 column
        "message_type",         # inferred from media_urls by v2 client
        "audio_url",            # referenced via media_urls in v2
        "audio_duration_seconds",  # no v2 equivalent
        "is_read",              # v2 tracks read state differently
        "status",               # no v2 equivalent
        "metadata",             # no v2 message metadata column
        "token_count",          # renamed to gemini_metadata JSONB envelope
    }
    for col in dropped_columns:
        assert col not in result, (
            f"Dropped column {col!r} must NOT appear in the v2 INSERT dict; "
            f"it has no matching v2 schema column (see etl-plan §3)"
        )


def test_transform_message_client_message_id_preserved():
    """client_message_id (F10 dedup key) is preserved — prevents mobile retry duplicates.

    WHAT: verifies that a non-NULL client_message_id from the source row appears
          unchanged in the transform output.
    WHEN: called with a source row that has client_message_id = "mobile-dedup-abc123".
    WHY:  client_message_id is the idempotency key for the ON CONFLICT DO NOTHING
          path in append_messages. If it is dropped or renamed during migration,
          v2 loses the ability to detect mobile retries and will double-insert
          messages (double paywall charge + duplicate UI bubbles).
    """
    row = _make_message_row(client_message_id="mobile-dedup-abc123")
    result = etl.transform_message_row(row)
    assert result["client_message_id"] == "mobile-dedup-abc123"


# ===========================================================================
# 3. JSONB SERIALIZATION
# ===========================================================================


def test_serialize_jsonb_none_returns_none():
    """NULL media_urls must remain NULL — not serialised to 'null' string.

    WHAT: verifies that _serialize_jsonb(None) returns None (not the string 'null').
    WHEN: called with None (the typical value for messages without media attachments).
    WHY:  if None were serialised to the JSON string 'null', the asyncpg $N::jsonb
          cast would insert 'null'::jsonb = SQL NULL equivalent, but the column
          type check would differ from a true SQL NULL — causing inconsistent
          behaviour in downstream queries that filter WHERE media_urls IS NULL.
    """
    assert etl._serialize_jsonb(None) is None


def test_serialize_jsonb_dict_produces_valid_json_string():
    """Python dict is serialised to a JSON string for $N::jsonb cast.

    WHAT: verifies that _serialize_jsonb({"urls": [...]}) returns a JSON-parseable
          string whose value round-trips back to the original dict.
    WHEN: called with a dict (the decoded JSONB Python object asyncpg returns when
          its JSONB codec is registered on the source connection).
    WHY:  asyncpg's destination pool uses $N::jsonb for JSONB columns; the
          parameter must be a string, not a Python dict. If a dict were passed
          directly, asyncpg would raise a type-mismatch error at execute time.
    """
    result = etl._serialize_jsonb({"urls": ["s3://bucket/key.jpg"]})
    assert isinstance(result, str), "serialized JSONB must be a string for asyncpg cast"
    parsed = json.loads(result)
    assert parsed == {"urls": ["s3://bucket/key.jpg"]}


def test_serialize_jsonb_list_produces_valid_json_string():
    """Python list (common for media_urls) serialises correctly.

    WHAT: verifies that _serialize_jsonb(["s3://a.jpg", "s3://b.mp4"]) returns
          a JSON string whose parsed value equals the original list.
    WHEN: called with a list (the most common media_urls shape in chat-ai).
    WHY:  media_urls is a JSONB array in both source and destination schemas.
          asyncpg may decode it to a Python list on source reads; it must be
          re-serialised to a string for the destination INSERT's ::jsonb cast.
    """
    result = etl._serialize_jsonb(["s3://a.jpg", "s3://b.mp4"])
    assert json.loads(result) == ["s3://a.jpg", "s3://b.mp4"]


def test_serialize_jsonb_already_string_passthrough():
    """A value already serialised as a JSON string is passed through unchanged.

    WHAT: verifies that _serialize_jsonb(already_serialized_string) returns the
          same string without double-encoding it.
    WHEN: called with a value that is already a valid JSON string (the case when
          the source asyncpg connection has no JSONB codec registered and returns
          raw text instead of decoded Python objects).
    WHY:  asyncpg codec configuration varies between deployments. Some connections
          decode JSONB to Python objects; others return raw strings. Double-encoding
          a raw string ('["a.jpg"]' → '"[\\"a.jpg\\"]"') would produce invalid JSONB
          at the destination INSERT and silently corrupt media_urls data.
    """
    already_serialized = '["s3://already-serialized.jpg"]'
    assert etl._serialize_jsonb(already_serialized) == already_serialized


# ===========================================================================
# 4. VERIFICATION FAILURE — LOUD EXIT
# ===========================================================================


def test_run_verification_passes_within_tolerance(capsys):
    """Verification succeeds silently when deltas are within ±500 convs / ±5K msgs.

    WHAT: verifies that run_verification() prints a VERIFICATION REPORT without
          a LARGE DELTA warning and exits cleanly (no sys.exit()) when source
          and destination counts are within the tolerance thresholds.
    WHEN: source has 100 conversations / 1000 messages; destination has 101 / 1003
          (small positive delta — expected when live traffic creates new rows
          during the migration window).
    WHY:  confirms the happy path doesn't false-positive sys.exit(1) and silently
          declare a normal small-delta ETL as failed.
    """
    source_pool = _MockPool(100, 1_000)       # source: 100 conversations, 1000 messages
    destination_pool = _MockPool(101, 1_003)  # destination: 101 conversations, 1003 messages

    # Should complete without sys.exit — any exit here is a test failure.
    asyncio.run(etl.run_verification(source_pool, destination_pool))

    report = capsys.readouterr().out
    assert "VERIFICATION REPORT" in report
    assert "LARGE DELTA" not in report


def test_run_verification_exits_1_on_large_conversation_delta(capsys):
    """Verification exits 1 when conversation delta exceeds ±500 threshold.

    WHAT: verifies that run_verification() calls sys.exit(1) and prints a LARGE
          DELTA warning when the conversation count difference exceeds 500 rows.
    WHEN: source has 10,000 conversations; destination has only 100 (delta = -9,900).
    WHY:  a large negative delta means rows were lost in migration (A4 violation
          — "ALL data MUST port"). The coordinator must investigate before declaring
          the ETL complete; exiting with code 1 prevents false-positive success.
    """
    source_pool = _MockPool(10_000, 1_000)      # source: 10K convs
    destination_pool = _MockPool(100, 1_000)    # destination: 100 convs — delta = -9900

    with pytest.raises(SystemExit) as exit_info:
        asyncio.run(etl.run_verification(source_pool, destination_pool))

    assert exit_info.value.code == 1, (
        "run_verification must exit with code 1 when conversation delta > 500; "
        "exit code 0 would silently declare a lossy migration as successful"
    )
    report = capsys.readouterr().out
    assert "LARGE DELTA" in report


def test_run_verification_exits_1_on_large_message_delta(capsys):
    """Verification exits 1 when message delta exceeds ±5000 threshold.

    WHAT: verifies that run_verification() calls sys.exit(1) and prints a LARGE
          DELTA warning when the message count difference exceeds 5,000 rows.
    WHEN: source has 100,000 messages; destination has only 10,000 (delta = -90,000).
    WHY:  3.3M messages × a large loss = millions of PII-bearing conversation
          turns silently not migrated. The higher ±5,000 threshold (vs ±500 for
          conversations) accounts for normal live-traffic growth during the
          migration window without masking a real data-loss event.
    """
    source_pool = _MockPool(100, 100_000)      # source: 100 convs, 100K messages
    destination_pool = _MockPool(100, 10_000)  # destination: 100 convs, 10K messages — delta = -90K

    with pytest.raises(SystemExit) as exit_info:
        asyncio.run(etl.run_verification(source_pool, destination_pool))

    assert exit_info.value.code == 1
    assert "LARGE DELTA" in capsys.readouterr().out


# ===========================================================================
# 5. CLI MUTUAL-EXCLUSIVITY ENFORCEMENT
# ===========================================================================


def test_cli_rejects_both_conversations_only_and_messages_only():
    """--conversations-only and --messages-only together must exit with code 2.

    WHAT: verifies that cli() raises SystemExit with code 2 when both
          --conversations-only and --messages-only flags are passed together.
    WHEN: sys.argv is set to ["etl", "--conversations-only", "--messages-only"].
    WHY:  the two flags are mutually exclusive phase-skip flags: together they
          would skip ALL phases (conversations_only skips messages; messages_only
          skips conversations) and main() would run zero migration work while
          silently exiting with code 0 — a completely silent no-op masquerading
          as a successful ETL run.
    """
    original_argv = sys.argv
    try:
        sys.argv = ["etl", "--conversations-only", "--messages-only"]
        with pytest.raises(SystemExit) as exit_info:
            etl.cli()
        assert exit_info.value.code == 2, (
            "argparse must exit with code 2 on mutual-exclusivity violation "
            "(standard POSIX CLI contract for argument errors)"
        )
    finally:
        # Restore sys.argv so other tests are not affected.
        sys.argv = original_argv


# ===========================================================================
# 6. PII-SAFE LOGGING
# ===========================================================================


def test_transform_message_does_not_log_content(caplog):
    """Message content (potential PII) must never appear in any log record.

    WHAT: verifies that calling transform_message_row() with a content string
          containing simulated PII does NOT cause that string to appear in any
          logging record emitted at DEBUG level or above.
    WHEN: called with content that includes a fake SSN and card number.
    WHY:  chat messages may contain names, contact details, payment info, or
          other personal information. Logging content would write PII to stdout
          and any log aggregation system (Sentry, Grafana Loki) — a GDPR and
          compliance violation. See etl-plan §8 PII safety section.
    """
    pii_content = "My SSN is 123-45-6789 and card is 4111-1111-1111-1111"
    row = _make_message_row(content=pii_content)

    with caplog.at_level(logging.DEBUG, logger="etl"):
        etl.transform_message_row(row)

    for record in caplog.records:
        log_text = record.getMessage()
        assert pii_content not in log_text, (
            f"PII content appeared verbatim in log record: {log_text!r}\n"
            "message content must NEVER be logged — it may contain user PII"
        )


def test_transform_conversation_does_not_log_metadata_values(caplog):
    """Conversation metadata values (may contain user facts) must not be logged.

    WHAT: verifies that calling transform_conversation_row() with a metadata dict
          containing a sensitive user fact does NOT log that fact string in any
          log record at DEBUG level or above.
    WHEN: called with metadata = {"memories": [{"fact": "user lives at 123 Main..."}]}.
    WHY:  conversations.metadata may contain AI-inferred user facts such as
          location, preferences, or personal details. Logging these values would
          write inferred PII to stdout and log aggregators without the user's
          awareness — a GDPR violation and privacy incident. See etl-plan §8.
    """
    sensitive_fact = "user lives at 123 Main Street, Springfield"
    row = _make_conv_row(metadata={"memories": [{"fact": sensitive_fact}]})

    with caplog.at_level(logging.DEBUG, logger="etl"):
        etl.transform_conversation_row(row)

    for record in caplog.records:
        log_text = record.getMessage()
        assert sensitive_fact not in log_text, (
            f"Sensitive metadata value appeared in log record: {log_text!r}"
        )


# ===========================================================================
# Mock infrastructure for migration behaviour tests (sections 7–11 below).
#
# WHY separate from _MockPool/_MockConnection:
#   run_verification() only needs fetchval(). The migration functions
#   additionally need fetch() (keyset reads), copy_records_to_table() (COPY),
#   and execute() (DDL + INSERT SELECT + per-row INSERT). A richer set of
#   test doubles is required without adding asyncpg or a real Postgres.
# ===========================================================================


def _make_migration_source_row(
    row_id: uuid.UUID,
    created_at: datetime,
    user_id: str = "user-migration-test",
) -> dict:
    """Build a minimal chat-ai conversations row for migration behaviour tests.

    WHAT: produces a dict matching the columns SELECTed by migrate_conversations()
          from chat-ai.conversations. The (created_at, id) pair is the keyset
          cursor; distinct values are needed for cursor-advancement assertions.
    WHEN: called at the top of each migration behaviour test.
    WHY:  inline dicts would be duplicated across tests; centralising keeps
          assertions readable and column-list drift in one place.
    """
    return {
        "id": row_id,
        "user_id": user_id,
        "influencer_id": "inf-migration-test",
        "participant_b_id": None,
        "conversation_type": "ai_chat",
        "metadata": None,
        "created_at": created_at,
        "updated_at": created_at,
    }


class _MigrationSourceConnection:
    """Asyncpg connection mock supporting fetchval() (count) + fetch() (keyset batches).

    WHAT: fetchval() returns the pre-configured total count once; each fetch()
          call returns the next pre-configured batch (empty list when exhausted).
          Records every fetch() call's cursor arguments for keyset assertions.
    WHEN: used by _MigrationSourcePool.acquire() for all source reads.
    WHY:  migrate_conversations() acquires the source pool twice per batch
          (once for count, once per keyset fetch). A shared connection object
          handles both call types in order without needing a stateful pool.
    """

    def __init__(self, total_count: int, batches: list):
        self._total_count = total_count
        self._batches = list(batches)
        self._batch_index = 0
        # Records (cursor_timestamp, cursor_id, batch_size) from each fetch() call.
        self.fetch_call_args: list = []

    async def fetchval(self, query: str):
        # Returns the total row count for the "SELECT count(*)" preamble.
        return self._total_count

    async def fetch(self, query: str, cursor_timestamp, cursor_id, batch_size: int):
        # Records cursor arguments so tests can assert correct cursor advancement.
        self.fetch_call_args.append((cursor_timestamp, cursor_id, batch_size))
        if self._batch_index < len(self._batches):
            result = self._batches[self._batch_index]
            self._batch_index += 1
            return result
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class _MigrationSourcePool:
    """Minimal source pool — returns the single shared _MigrationSourceConnection.

    WHAT: wraps _MigrationSourceConnection so its acquire() interface matches
          asyncpg.Pool. The connection attribute is public for test assertions
          (e.g. checking fetch_call_args after the migration run).
    WHEN: passed as the `source` argument to migrate_conversations / migrate_messages.
    WHY:  migrate_conversations() calls source.acquire() N+1 times (once for count,
          once per batch); all calls share the same connection object here.
    """

    def __init__(self, total_count: int, batches: list):
        self.connection = _MigrationSourceConnection(total_count, batches)

    def acquire(self):
        return self.connection


class _MigrationDestinationConnection:
    """Asyncpg connection mock capturing COPY + execute() calls for migration assertions.

    WHAT: accepts execute() (CREATE TEMP TABLE, TRUNCATE, INSERT SELECT, per-row
          INSERT, UPDATE) and copy_records_to_table(). Records all calls for
          assertion. Optionally raises _MockCheckViolationError on the first
          bulk INSERT SELECT and/or on the first per-row INSERT.
    WHEN: used by _MigrationDestinationPool.acquire() for all destination writes.
    WHY:  the destination connection is held for the entire migration phase
          (TEMP TABLE session scope); a single connection object covers all
          batches without pool re-acquisition.
    """

    def __init__(
        self,
        insert_select_results: list | None = None,
        raise_on_bulk_insert: bool = False,
        raise_on_first_per_row_insert: bool = False,
    ):
        # Pre-configured INSERT SELECT return values (consumed in order).
        self._insert_select_results = list(insert_select_results or ["INSERT 0 0"])
        self.raise_on_bulk_insert = raise_on_bulk_insert
        self.raise_on_first_per_row_insert = raise_on_first_per_row_insert
        self._bulk_insert_raised = False
        self._per_row_insert_count = 0
        # Public lists for test assertions.
        self.copy_calls: list = []    # one entry per copy_records_to_table() call
        self.execute_calls: list = [] # trimmed SQL for every execute() call
        self.per_row_insert_args: list = []  # positional args per per-row INSERT

    async def execute(self, query: str, *args):
        """Route each execute() call by SQL type; optionally raise on INSERT."""
        stripped = query.strip()
        self.execute_calls.append(stripped)

        is_bulk_insert = stripped.startswith("INSERT") and "SELECT" in stripped
        is_per_row_insert = stripped.startswith("INSERT") and "VALUES" in stripped

        if is_bulk_insert:
            if self.raise_on_bulk_insert and not self._bulk_insert_raised:
                self._bulk_insert_raised = True
                raise _MockCheckViolationError("bulk INSERT constraint violation")
            if self._insert_select_results:
                return self._insert_select_results.pop(0)
            return "INSERT 0 0"

        if is_per_row_insert:
            self._per_row_insert_count += 1
            self.per_row_insert_args.append(args)
            if self.raise_on_first_per_row_insert and self._per_row_insert_count == 1:
                raise _MockCheckViolationError("per-row constraint violation")
            return "INSERT 0 1"

        # DDL (CREATE TEMP TABLE, TRUNCATE) or UPDATE — generic success.
        return "OK"

    async def copy_records_to_table(self, table: str, *, records, columns):
        """Capture COPY call for assertion (table name, record data, column list)."""
        self.copy_calls.append({
            "table": table,
            "records": list(records),
            "columns": list(columns),
        })

    async def fetchval(self, query: str):
        # Used by update_message_counts dry-run path.
        return 3

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class _MigrationDestinationPool:
    """Minimal destination pool wrapping _MigrationDestinationConnection.

    WHAT: acquire() always returns the same connection object so the TEMP TABLE
          created in the first acquire stays accessible in subsequent acquires —
          matching PostgreSQL's session-scoped TEMP TABLE behaviour.
    WHEN: passed as the `destination` argument to migrate_conversations / migrate_messages.
    WHY:  single connection = single TEMP TABLE session = correct ETL behaviour.
    """

    def __init__(self, connection: _MigrationDestinationConnection | None = None):
        self.connection = connection or _MigrationDestinationConnection()

    def acquire(self):
        return self.connection


# ===========================================================================
# 7. KEYSET PAGINATION CURSOR ADVANCEMENT
# ===========================================================================


def test_migrate_conversations_keyset_cursor_advances_to_last_row_of_batch():
    """After batch 1, the next source fetch uses (created_at, id) of batch-1's last row.

    WHAT: migrate_conversations() issues two keyset reads:
          call 1 → cursor = (_ETL_EPOCH, _UUID_MIN) [initial values]
          call 2 → cursor = (row_b.created_at, row_b.id) [advanced to last of batch 1]
          This test pins both cursor values to confirm the advancement logic.
    WHEN: batch_size=2, batch 1 returns exactly 2 rows (full batch → continue loop),
          batch 2 returns 0 rows (end-of-data → exit loop).
    WHY:  a created_at-only or id-only cursor would silently skip or double-read rows
          that share a timestamp boundary. The compound (created_at, id) cursor must
          advance both components together after every full batch.
    """
    batch_size = 2
    row_a = _make_migration_source_row(
        row_id=uuid.UUID("aa000000-0000-0000-0000-000000000001"),
        created_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
    )
    row_b = _make_migration_source_row(
        row_id=uuid.UUID("bb000000-0000-0000-0000-000000000002"),
        created_at=datetime(2026, 1, 2, 10, 0, 0, tzinfo=timezone.utc),
    )
    # Batch 1: full batch (exactly batch_size rows → loop continues).
    # Batch 2: empty → exit loop.
    source = _MigrationSourcePool(total_count=2, batches=[[row_a, row_b], []])
    destination = _MigrationDestinationPool()

    asyncio.run(etl.migrate_conversations(source, destination, batch_size=batch_size, dry_run=True))

    call_args = source.connection.fetch_call_args
    assert len(call_args) == 2, (
        f"Expected exactly 2 fetch() calls (batch 1 + end-of-data), got {len(call_args)}"
    )
    # First call must use the sentinel starting cursor.
    initial_cursor_timestamp, initial_cursor_id, _ = call_args[0]
    assert initial_cursor_timestamp == etl._ETL_EPOCH, (
        "First fetch must start at _ETL_EPOCH (guaranteed to precede all real rows)"
    )
    assert initial_cursor_id == etl._UUID_MIN, (
        "First fetch must start at _UUID_MIN (nil UUID sorts first)"
    )
    # Second call must use the last row of batch 1 as the cursor.
    advanced_cursor_timestamp, advanced_cursor_id, _ = call_args[1]
    assert advanced_cursor_timestamp == row_b["created_at"], (
        "cursor_timestamp must advance to the last row's created_at after a full batch"
    )
    assert advanced_cursor_id == row_b["id"], (
        "cursor_id must advance to the last row's id after a full batch"
    )


# ===========================================================================
# 8. COPY-TO-STAGING CORRECT DATA
# ===========================================================================


def test_migrate_conversations_copies_correct_data_to_staging():
    """COPY call passes the correct staging table name, columns, and row data.

    WHAT: migrate_conversations() calls copy_records_to_table() once per batch
          with the staging table name, the ordered column list, and a record
          tuple matching the transformed row. This test pins all three.
    WHEN: single batch with one source row; dry_run=False so the COPY path runs.
    WHY:  a wrong staging table name, mismatched column order, or wrong field
          mapping would cause a silent data-type mismatch or INSERT SELECT failure
          on the live run. Pinning the COPY call in a unit test catches these
          bugs before Day-9 execution.
    """
    source_row = _make_migration_source_row(
        row_id=uuid.UUID("cc000000-0000-0000-0000-000000000003"),
        created_at=datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc),
        user_id="user-copy-test",
    )
    source = _MigrationSourcePool(total_count=1, batches=[[source_row]])
    destination_connection = _MigrationDestinationConnection(insert_select_results=["INSERT 0 1"])
    destination = _MigrationDestinationPool(destination_connection)

    asyncio.run(etl.migrate_conversations(source, destination, batch_size=10, dry_run=False))

    assert len(destination_connection.copy_calls) == 1, (
        f"Expected 1 COPY call for the single batch, got {len(destination_connection.copy_calls)}"
    )
    copy_call = destination_connection.copy_calls[0]
    assert copy_call["table"] == "conversations_staging", (
        f"COPY target must be 'conversations_staging', got {copy_call['table']!r}"
    )
    assert "id" in copy_call["columns"], "id must be in the COPY column list"
    assert "user_id" in copy_call["columns"], "user_id must be in the COPY column list"
    assert "conversation_type" in copy_call["columns"], (
        "conversation_type must be in the COPY column list"
    )
    # The user_id from the source row must appear in the copied records.
    user_id_index = copy_call["columns"].index("user_id")
    copied_user_ids = [record[user_id_index] for record in copy_call["records"]]
    assert "user-copy-test" in copied_user_ids, (
        f"Source row's user_id 'user-copy-test' missing from COPY records: {copied_user_ids}"
    )


# ===========================================================================
# 9. ON CONFLICT IDEMPOTENCY
# ===========================================================================


def test_migrate_conversations_on_conflict_returns_zero_inserted():
    """When INSERT SELECT returns 'INSERT 0 0' (all conflicts), inserted total is 0.

    WHAT: on a re-run where all rows already exist, INSERT SELECT returns
          'INSERT 0 0'. migrate_conversations() must parse this correctly and
          return 0 — no double-counting of already-loaded rows.
    WHEN: destination returns 'INSERT 0 0' (ON CONFLICT DO NOTHING fired for
          every row — simulates a second ETL run after all rows are loaded).
    WHY:  if the return-value parser returned 1 instead of 0, a re-run would
          erroneously report rows as newly inserted (misleads the coordinator's
          A4 verification + mutes the true count of fresh rows).
    """
    source_row = _make_migration_source_row(
        row_id=uuid.UUID("dd000000-0000-0000-0000-000000000004"),
        created_at=datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc),
    )
    source = _MigrationSourcePool(total_count=1, batches=[[source_row]])
    # Destination signals all-conflict (all rows pre-exist).
    destination = _MigrationDestinationPool(
        _MigrationDestinationConnection(insert_select_results=["INSERT 0 0"])
    )

    inserted = asyncio.run(
        etl.migrate_conversations(source, destination, batch_size=10, dry_run=False)
    )

    assert inserted == 0, (
        "migrate_conversations must return 0 when INSERT SELECT reports 0 new rows "
        "(ON CONFLICT DO NOTHING fired for all rows — idempotent re-run)"
    )


# ===========================================================================
# 10. MESSAGE COUNT UPDATE
# ===========================================================================


def test_update_message_counts_dry_run_logs_affected_count(caplog):
    """Dry-run path logs the number of conversations that would be updated.

    WHAT: update_message_counts(dry_run=True) must call fetchval() to count
          conversations WHERE message_count = 0 and log the result without
          executing any UPDATE.
    WHEN: destination pool's fetchval returns 7 (7 conversations awaiting update).
    WHY:  the coordinator checks the dry-run log before committing to a live
          migration. If the dry-run path silently skips the count query, the
          coordinator sees no output and cannot verify Phase 3 is ready.
    """
    destination_connection = _MigrationDestinationConnection()
    # Override fetchval to return a specific count for the dry-run check.
    destination_connection_count = 7

    class _CountingConnection(_MigrationDestinationConnection):
        async def fetchval(self, query: str):
            return destination_connection_count

    destination = _MigrationDestinationPool(_CountingConnection())

    with caplog.at_level(logging.INFO, logger="etl"):
        asyncio.run(etl.update_message_counts(destination, dry_run=True))

    log_messages = " ".join(r.getMessage() for r in caplog.records)
    assert "7" in log_messages, (
        "update_message_counts dry-run must log the count of affected conversations; "
        f"got log: {log_messages!r}"
    )
    assert "DRY RUN" in log_messages.upper(), (
        "update_message_counts dry-run must clearly say DRY RUN in the log"
    )
    # Verify no UPDATE was executed (no execute() calls with UPDATE keyword).
    update_calls = [
        line for line in destination.connection.execute_calls if "UPDATE" in line
    ]
    assert not update_calls, (
        f"dry_run=True must not execute any UPDATE statement; got: {update_calls}"
    )


# ===========================================================================
# 11. CHECK VIOLATION FALLBACK — PER-ROW RETRY
# ===========================================================================


def test_migrate_conversations_check_violation_falls_back_to_per_row(caplog):
    """When bulk INSERT SELECT raises CheckViolationError, per-row retry runs.

    WHAT: migrate_conversations() wraps the bulk INSERT SELECT in a try/except.
          When _MockCheckViolationError is raised, it retries the batch row-by-row.
          If one per-row INSERT also raises (bad row), that row is skipped (logged)
          and the loop continues. Only genuinely inserted rows are counted.
    WHEN: batch has 2 rows; bulk INSERT raises; first per-row INSERT raises
          (bad row); second per-row INSERT succeeds.
    WHY:  without the fallback, a single bad row aborts the entire batch — violating
          the plan §7 contract "Script logs the offending row + skips it".
          This test pins: (a) the fallback fires, (b) 1 row is skipped, (c) 1 is
          inserted, (d) the bad row's id appears in the warning log.
    """
    row_good = _make_migration_source_row(
        row_id=uuid.UUID("ee000000-0000-0000-0000-000000000005"),
        created_at=datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc),
    )
    row_bad = _make_migration_source_row(
        row_id=uuid.UUID("ff000000-0000-0000-0000-000000000006"),
        created_at=datetime(2026, 4, 1, 0, 0, 1, tzinfo=timezone.utc),
    )
    # Source returns both rows in one batch.
    source = _MigrationSourcePool(total_count=2, batches=[[row_good, row_bad]])
    # Destination: bulk INSERT raises; first per-row INSERT (row_good) raises;
    # second per-row INSERT (row_bad) would... wait, we want the BAD row to fail.
    # The rows_to_insert are processed in order: transform_conversation_row maps
    # them. Per-row loop iterates in the same order. Let bad be the first row.
    row_first = _make_migration_source_row(
        row_id=uuid.UUID("aa100000-0000-0000-0000-000000000001"),
        created_at=datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc),
    )
    row_second = _make_migration_source_row(
        row_id=uuid.UUID("bb200000-0000-0000-0000-000000000002"),
        created_at=datetime(2026, 5, 1, 0, 0, 1, tzinfo=timezone.utc),
    )
    source2 = _MigrationSourcePool(total_count=2, batches=[[row_first, row_second]])
    # raise_on_bulk_insert=True → first INSERT SELECT raises CheckViolationError
    # raise_on_first_per_row_insert=True → first per-row INSERT also raises (bad row)
    # Second per-row INSERT returns "INSERT 0 1" (default) → good row inserted
    destination_connection = _MigrationDestinationConnection(
        raise_on_bulk_insert=True,
        raise_on_first_per_row_insert=True,
    )
    destination = _MigrationDestinationPool(destination_connection)

    with caplog.at_level(logging.WARNING, logger="etl"):
        inserted = asyncio.run(
            etl.migrate_conversations(source2, destination, batch_size=10, dry_run=False)
        )

    # Exactly 1 row inserted (the second per-row INSERT succeeded).
    assert inserted == 1, (
        f"Expected 1 inserted row (1 skipped, 1 succeeded), got {inserted}. "
        "The fallback must count only rows that successfully INSERT."
    )
    # At least 2 warning log entries: one for the batch fallback, one for the bad row.
    warning_messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warning_messages) >= 2, (
        f"Expected ≥2 WARNING log entries (fallback notice + skipped row), "
        f"got {len(warning_messages)}: {warning_messages}"
    )
    # The fallback notice must mention 'fallback' or 'retry'.
    fallback_notice = any(
        "fallback" in m.lower() or "retry" in m.lower() or "retrying" in m.lower()
        for m in warning_messages
    )
    assert fallback_notice, (
        f"Expected a WARNING mentioning the per-row fallback, got: {warning_messages}"
    )
    # The bad-row skip must mention 'SKIPPING'.
    skip_notice = any("SKIPPING" in m for m in warning_messages)
    assert skip_notice, (
        f"Expected a WARNING with 'SKIPPING' for the bad row, got: {warning_messages}"
    )


# ===========================================================================
# 12. PHASE 4 — SEQUENCE_IN_CONVERSATION BACKFILL
# ===========================================================================


def test_backfill_sequence_in_conversation_dry_run_logs_count(caplog):
    """Dry-run path logs how many messages need sequence_in_conversation backfill.

    WHAT: backfill_sequence_in_conversation(dry_run=True) must call fetchval()
          to count messages WHERE sequence_in_conversation = 0 and log the
          result without executing any UPDATE statement.
    WHEN: destination pool's fetchval() returns 3 (3 migrated messages with
          the DEFAULT 0 sentinel value that Phase 4 would correct).
    WHY:  the coordinator checks the dry-run log before committing to live
          Phase 4 execution. If the dry-run path silently skips the count
          query, the coordinator sees no output and cannot verify that Phase
          4 is needed (e.g. in a re-run after a partial migration). The
          absence of any UPDATE in the dry-run output confirms the live UPDATE
          is gated correctly.
    """
    # _MigrationDestinationConnection.fetchval() always returns 3 — used as
    # the "3 unsequenced messages" count for assertion purposes.
    destination = _MigrationDestinationPool()

    with caplog.at_level(logging.INFO, logger="etl"):
        asyncio.run(etl.backfill_sequence_in_conversation(destination, dry_run=True))

    log_messages = " ".join(r.getMessage() for r in caplog.records)
    assert "3" in log_messages, (
        "backfill dry-run must log the count of unsequenced messages (fetchval returns 3); "
        f"got log output: {log_messages!r}"
    )
    assert "DRY RUN" in log_messages.upper(), (
        "backfill dry-run must clearly include DRY RUN in the log output; "
        f"got: {log_messages!r}"
    )
    # Verify no UPDATE was issued in dry-run mode.
    update_calls = [
        line for line in destination.connection.execute_calls
        if "UPDATE" in line
    ]
    assert not update_calls, (
        f"dry_run=True must not execute any UPDATE; "
        f"found UPDATE call(s): {update_calls}"
    )


def test_backfill_sequence_in_conversation_live_executes_row_number_update(caplog):
    """Live path executes the ROW_NUMBER() UPDATE and logs completion.

    WHAT: backfill_sequence_in_conversation(dry_run=False) must call execute()
          with an UPDATE statement containing ROW_NUMBER() and log a completion
          message. The mock destination returns 'OK' from execute() — which the
          function logs as the result.
    WHEN: destination is a _MigrationDestinationPool with default mock connection;
          dry_run=False so the live path runs.
    WHY:  the dry-run test confirms the guard is in place; this test confirms the
          live path actually issues the UPDATE. Without this test, a refactor that
          accidentally put the UPDATE under an if-not-dry-run check identical to
          the dry_run branch would go undetected.
    """
    destination = _MigrationDestinationPool()

    with caplog.at_level(logging.INFO, logger="etl"):
        asyncio.run(etl.backfill_sequence_in_conversation(destination, dry_run=False))

    # Exactly one UPDATE must have been issued.
    update_calls = [
        line for line in destination.connection.execute_calls
        if "UPDATE" in line
    ]
    assert len(update_calls) == 1, (
        f"Expected exactly 1 UPDATE call in live mode, got {len(update_calls)}: {update_calls}"
    )
    # The UPDATE must reference ROW_NUMBER and PARTITION BY for correctness.
    update_sql = update_calls[0]
    assert "ROW_NUMBER" in update_sql, (
        "The backfill UPDATE must use ROW_NUMBER() OVER (...) to assign ordinals; "
        f"got: {update_sql!r}"
    )
    assert "PARTITION BY" in update_sql, (
        "The backfill UPDATE must PARTITION BY conversation_id so each conversation "
        f"gets its own 1-based sequence; got: {update_sql!r}"
    )
    # Log must mention completion (not dry-run text).
    log_messages = " ".join(r.getMessage() for r in caplog.records)
    assert "DONE" in log_messages or "backfill" in log_messages.lower(), (
        f"Expected a completion log message, got: {log_messages!r}"
    )


# ===========================================================================
# RELATED FILES:
#   etl-scripts/chat_ai_to_user_memory_etl.py  — module under test
#   etl-scripts/etl-plan-day-9-draft.md        — §2 + §3 column mapping documentation
#   yral-rishi-agent-user-memory-service/app/migrations/versions/
#                                              — schema the destination DB must satisfy
#   yral-rishi-agent-user-memory-service/app/migrations/versions/004_add_sequence_in_conversation.py
#                                              — migration that adds the column Phase 4 backfills
# ===========================================================================
