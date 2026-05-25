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
_CONV_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_MSG_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
_USER_ID = "user-test-principal-123"
_INF_ID = "inf-test-456"


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
        "id": _CONV_ID,
        "user_id": _USER_ID,
        "influencer_id": _INF_ID,
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
        "id": _MSG_ID,
        "conversation_id": _CONV_ID,
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
    assert result["id"] == _CONV_ID
    assert result["user_id"] == _USER_ID
    assert result["influencer_id"] == _INF_ID
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

    WHY: v2 Phase 1 schema has no metadata column; passing it would cause an
         'column does not exist' error on INSERT.
    """
    row = _make_conv_row(metadata={"memories": [{"fact": "likes cats"}]})
    result = etl.transform_conversation_row(row)
    assert "metadata" not in result, (
        "metadata must be dropped from the v2 output; "
        "if present the INSERT will fail with an unknown-column error"
    )


def test_transform_conversation_h2h_nulls_round_trip():
    """NULL influencer_id + non-NULL participant_b_id (H2H mode) survive the transform."""
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

    WHY: v2 has no token_count column; the data is preserved inside the
         gemini_metadata JSONB field that the orchestrator already reads
         for cost accounting.
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
    """NULL token_count produces NULL gemini_metadata — user messages have no token count."""
    row = _make_message_row(token_count=None)
    result = etl.transform_message_row(row)
    assert result["gemini_metadata"] is None


def test_transform_message_null_content_coerced_to_empty_string():
    """NULL content maps to '' — v2 messages.content has a NOT NULL constraint.

    WHY: chat-ai occasionally stores NULL content for system-generated stubs;
         inserting NULL into v2 would violate the NOT NULL constraint and abort
         the batch transaction.
    """
    row = _make_message_row(content=None)
    result = etl.transform_message_row(row)
    assert result["content"] == "", (
        "NULL content must be coerced to empty string; "
        "NULL would violate v2 messages.content NOT NULL constraint"
    )


def test_transform_message_count_toward_paywall_defaults_true():
    """All migrated messages count toward the paywall — conservative E7 default."""
    result = etl.transform_message_row(_make_message_row())
    assert result["count_toward_paywall"] is True, (
        "count_toward_paywall must default to True; "
        "we cannot retroactively determine which historical messages were auto-greet exemptions"
    )


def test_transform_message_dropped_columns_absent():
    """Columns with no v2 equivalent are completely absent from the output dict.

    WHY: passing unknown columns to the INSERT causes 'column does not exist' errors.
         Each dropped column is a documented data-loss decision in etl-plan §3.
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
    """client_message_id (F10 dedup key) is preserved — prevents mobile retry duplicates."""
    row = _make_message_row(client_message_id="mobile-dedup-abc123")
    result = etl.transform_message_row(row)
    assert result["client_message_id"] == "mobile-dedup-abc123"


# ===========================================================================
# 3. JSONB SERIALIZATION
# ===========================================================================


def test_serialize_jsonb_none_returns_none():
    """NULL media_urls must remain NULL — not serialised to 'null' string."""
    assert etl._serialize_jsonb(None) is None


def test_serialize_jsonb_dict_produces_valid_json_string():
    """Python dict is serialised to a JSON string for $N::jsonb cast."""
    result = etl._serialize_jsonb({"urls": ["s3://bucket/key.jpg"]})
    assert isinstance(result, str), "serialized JSONB must be a string for asyncpg cast"
    parsed = json.loads(result)
    assert parsed == {"urls": ["s3://bucket/key.jpg"]}


def test_serialize_jsonb_list_produces_valid_json_string():
    """Python list (common for media_urls) serialises correctly."""
    result = etl._serialize_jsonb(["s3://a.jpg", "s3://b.mp4"])
    assert json.loads(result) == ["s3://a.jpg", "s3://b.mp4"]


def test_serialize_jsonb_already_string_passthrough():
    """A value already serialised as a JSON string is passed through unchanged.

    WHY: asyncpg codec configuration varies — some pools decode JSONB to Python
         objects, others return raw strings. This passthrough handles the raw-string case.
    """
    already_serialized = '["s3://already-serialized.jpg"]'
    assert etl._serialize_jsonb(already_serialized) == already_serialized


# ===========================================================================
# 4. VERIFICATION FAILURE — LOUD EXIT
# ===========================================================================


def test_run_verification_passes_within_tolerance(capsys):
    """Verification succeeds silently when deltas are within ±500 convs / ±5K msgs.

    WHAT: source and destination counts are close — coordinator sees PASSED report.
    WHY:  confirms the happy path doesn't false-positive sys.exit(1).
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

    WHY: a large negative delta means rows were lost in migration (A4 violation);
         the coordinator must investigate before declaring the ETL complete.
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
    """Verification exits 1 when message delta exceeds ±5000 threshold."""
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

    WHY: if both flags were accepted, main() would skip Phase 1 (conversations_only=True
         skips messages) AND Phase 2 (messages_only=True skips conversations) — the ETL
         would run zero phases and silently succeed with no data migrated.
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

    WHY: chat messages may contain names, contact details, or other personal
         information. Logging content would write PII to stdout and any log
         aggregation system (Sentry, Grafana Loki). See etl-plan §8.
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

    WHY: conversations.metadata may contain inferred user facts from the AI
         (e.g. 'user lives at X'). These are sensitive and must not appear in logs.
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
# RELATED FILES:
#   etl-scripts/chat_ai_to_user_memory_etl.py  — module under test
#   etl-scripts/etl-plan-day-9-draft.md        — §2 + §3 column mapping documentation
#   yral-rishi-agent-user-memory-service/app/migrations/versions/
#                                              — schema the destination DB must satisfy
# ===========================================================================
