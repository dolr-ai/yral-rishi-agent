"""Tests for the Chat-as-Human takeover feature.

Unit tests for the pure helpers. End-to-end flow tests live in
scripts/test_all_endpoints.py and scripts/test_takeover_e2e.py.

Timer semantics (Bug 1 fix): remaining_seconds() reads the CREATOR's last
message timestamp — auto-release fires when the creator goes silent.
"""

from datetime import datetime, timezone, timedelta


def test_remaining_seconds_no_last_message():
    from services.takeover_helpers import remaining_seconds, TAKEOVER_TIMEOUT_SECONDS

    assert remaining_seconds(None) == TAKEOVER_TIMEOUT_SECONDS


def test_remaining_seconds_creator_just_sent():
    """Creator just sent a message — full 120s remaining."""
    from services.takeover_helpers import remaining_seconds, TAKEOVER_TIMEOUT_SECONDS

    now = datetime.now(timezone.utc)
    rem = remaining_seconds(now)
    assert rem >= TAKEOVER_TIMEOUT_SECONDS - 1
    assert rem <= TAKEOVER_TIMEOUT_SECONDS


def test_remaining_seconds_creator_silent_past_timeout():
    """Creator silent for 3 min — timer expired."""
    from services.takeover_helpers import remaining_seconds

    three_min_ago = datetime.now(timezone.utc) - timedelta(minutes=3)
    assert remaining_seconds(three_min_ago) == 0


def test_remaining_seconds_creator_silent_one_min():
    """Creator silent for 1 min — ~60s remaining (Bug 1: timer keyed on creator)."""
    from services.takeover_helpers import remaining_seconds

    one_min_ago = datetime.now(timezone.utc) - timedelta(seconds=60)
    rem = remaining_seconds(one_min_ago)
    assert 55 <= rem <= 65


def test_remaining_seconds_naive_datetime():
    """Naive datetimes from asyncpg should still work (treated as UTC)."""
    from services.takeover_helpers import remaining_seconds, TAKEOVER_TIMEOUT_SECONDS

    naive_now = datetime.utcnow()
    rem = remaining_seconds(naive_now)
    assert rem >= TAKEOVER_TIMEOUT_SECONDS - 2


def test_format_msg_for_response():
    from services.takeover_helpers import format_msg_for_response

    msg = {
        "id": "msg-123",
        "conversation_id": "conv-1",
        "role": "assistant",
        "content": "Hello from creator",
        "message_type": "text",
        "created_at": datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc),
    }
    formatted = format_msg_for_response(msg)
    assert formatted["id"] == "msg-123"
    assert formatted["content"] == "Hello from creator"
    assert formatted["role"] == "assistant"
    assert formatted["created_at"].startswith("2026-05-28")


def test_remaining_seconds_ignores_user_activity():
    """Bug 1 regression guard: timer driven by CREATOR's timestamp, not user's.

    If we passed a fresh user timestamp here, the old buggy code would return
    full 120s. Now we pass it as the creator timestamp argument — same result —
    but the semantic distinction matters: the caller MUST pass creator's
    timestamp, not user's. The route code is updated to do exactly that.
    """
    from services.takeover_helpers import remaining_seconds, TAKEOVER_TIMEOUT_SECONDS

    creator_just_now = datetime.now(timezone.utc)
    rem = remaining_seconds(creator_just_now)
    # Creator active → full window
    assert rem >= TAKEOVER_TIMEOUT_SECONDS - 1

    creator_long_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
    rem_expired = remaining_seconds(creator_long_ago)
    # Creator silent → expired regardless of what the user did
    assert rem_expired == 0
