"""Tests for the Chat-as-Human takeover feature.

Unit tests for the pure helpers. End-to-end flow tests live in
scripts/test_all_endpoints.py.
"""

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_remaining_seconds_no_last_message():
    from services.takeover_helpers import remaining_seconds, TAKEOVER_TIMEOUT_SECONDS

    assert remaining_seconds(None) == TAKEOVER_TIMEOUT_SECONDS


def test_remaining_seconds_just_now():
    from services.takeover_helpers import remaining_seconds, TAKEOVER_TIMEOUT_SECONDS

    now = datetime.now(timezone.utc)
    rem = remaining_seconds(now)
    assert rem >= TAKEOVER_TIMEOUT_SECONDS - 1
    assert rem <= TAKEOVER_TIMEOUT_SECONDS


def test_remaining_seconds_expired():
    from services.takeover_helpers import remaining_seconds

    three_min_ago = datetime.now(timezone.utc) - timedelta(minutes=3)
    assert remaining_seconds(three_min_ago) == 0


def test_remaining_seconds_partial():
    from services.takeover_helpers import remaining_seconds

    one_min_ago = datetime.now(timezone.utc) - timedelta(seconds=60)
    rem = remaining_seconds(one_min_ago)
    # 120 - 60 = 60 (with small tolerance for test timing)
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
