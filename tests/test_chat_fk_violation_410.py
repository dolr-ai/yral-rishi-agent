"""Regression test for the FK-violation race in chat send paths.

Sentry triage 2026-06-18 found YRAL-RISHI-AGENT-4H + 22 — small
event counts but real data-integrity failures: the user sent a
message after deleting the conversation on a different device, the
`_can_access_conversation` check passed (the conversation existed
when fetched), but the `messages.conversation_id` FK was violated
on INSERT because the conversation got deleted in the few hundred
ms between the fetch and the INSERT.

Fix: catch `asyncpg.ForeignKeyViolationError` around every
`message_repo.create` in the chat send paths; map to either:
  - HTTPException(410, "Conversation deleted") for non-stream + the
    pre-stream user_msg INSERT (clean HTTP status, mobile already
    handles 410)
  - SSE error event `{code: "CONVERSATION_DELETED", retryable:
    False}` for the mid-stream assistant_msg INSERT (can't raise
    mid-stream; use the same `{code, message, retryable}` shape the
    other stream errors use)
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

REPO = Path(__file__).resolve().parents[1]


# ─── source-pin ─────────────────────────────────────────────────────────


def test_asyncpg_imported():
    """The fix needs `asyncpg.ForeignKeyViolationError`. Pin the
    import so a future cleanup doesn't accidentally drop it."""
    src = (REPO / "app" / "routes" / "chat.py").read_text()
    assert "import asyncpg" in src


def test_non_stream_user_msg_caught_with_410():
    """Non-stream `send_message` writes user_msg first. The FK
    violation on that INSERT must map to 410 Gone."""
    src = (REPO / "app" / "routes" / "chat.py").read_text()
    # Find the send_message function body (between the @router.post
    # decorator and the next handler).
    fn_marker = '@router.post("/conversations/{conversation_id}/messages")'
    fn_start = src.index(fn_marker)
    # Next handler — image route or anything starting with @router.
    fn_end = src.index("@router.", fn_start + len(fn_marker))
    fn_body = src[fn_start:fn_end]
    assert "except asyncpg.ForeignKeyViolationError:" in fn_body
    assert 'HTTPException(status_code=410, detail="Conversation deleted")' in fn_body


def test_stream_pre_stream_user_msg_caught_with_410():
    """`send_message_stream` writes user_msg BEFORE the
    StreamingResponse is constructed. FK violation there → clean 410
    rather than an SSE error event mid-stream."""
    src = (REPO / "app" / "routes" / "chat.py").read_text()
    fn_marker = '@router.post("/conversations/{conversation_id}/messages/stream")'
    fn_start = src.index(fn_marker)
    fn_end = src.index("@router.", fn_start + len(fn_marker))
    fn_body = src[fn_start:fn_end]
    assert "except asyncpg.ForeignKeyViolationError:" in fn_body
    assert 'HTTPException(status_code=410, detail="Conversation deleted")' in fn_body


def test_stream_mid_stream_assistant_msg_yields_sse_error():
    """The assistant_msg INSERT inside event_stream runs mid-stream
    — we can't raise HTTPException after the SSE response is open.
    Yield the structured `{code, message, retryable}` error event
    the other stream-error paths use."""
    src = (REPO / "app" / "routes" / "chat.py").read_text()
    # The CONVERSATION_DELETED code is unique enough to scope-pin.
    assert '"code": "CONVERSATION_DELETED"' in src
    assert '"retryable": False' in src


def test_410_response_shape_consistent():
    """All `HTTPException(410, ...)` raises in this file use
    `detail="Conversation deleted"` so mobile's 410 handler doesn't
    have to special-case different messages."""
    src = (REPO / "app" / "routes" / "chat.py").read_text()
    # Count 410 raises that come with the canonical message
    canonical = 'HTTPException(status_code=410, detail="Conversation deleted")'
    assert src.count(canonical) >= 2  # at least non-stream + pre-stream paths
