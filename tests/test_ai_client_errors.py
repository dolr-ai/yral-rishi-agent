"""Tests for Phase 3.8 graceful Gemini error UX.

Covers the error_code path through ai_client: classification of Gemini blocks
vs transient failures, and the response shape mobile receives.
"""


import pytest


def test_error_messages_exhaustive():
    """Every retryable code is in ERROR_MESSAGES; every error code is classified."""
    from services.ai_client import ERROR_MESSAGES, RETRYABLE_CODES

    for code in RETRYABLE_CODES:
        assert code in ERROR_MESSAGES, f"{code} retryable but no message defined"
    assert "BLOCKED_CONTENT" in ERROR_MESSAGES
    assert "TRANSIENT" in ERROR_MESSAGES
    assert "NO_PROVIDER" in ERROR_MESSAGES
    # BLOCKED_CONTENT is NOT retryable — same prompt won't get past the filter.
    assert "BLOCKED_CONTENT" not in RETRYABLE_CODES
    # NO_PROVIDER is NOT retryable — config issue, retry won't help.
    assert "NO_PROVIDER" not in RETRYABLE_CODES


def test_llm_blocked_error_carries_reason():
    from services.ai_client import LlmBlockedError

    err = LlmBlockedError("blockReason=PROHIBITED_CONTENT")
    assert err.reason == "blockReason=PROHIBITED_CONTENT"
    assert "PROHIBITED_CONTENT" in str(err)


def test_assistant_error_model_shape():
    """Mobile contract: error has code, message, retryable. Code is Literal."""
    from models import AssistantError

    err = AssistantError(
        code="BLOCKED_CONTENT",
        message="I can't reply to that — try asking me something else.",
        retryable=False,
    )
    assert err.code == "BLOCKED_CONTENT"
    assert err.retryable is False

    with pytest.raises(Exception):
        AssistantError(code="INVALID_CODE", message="x", retryable=False)


def test_send_message_response_allows_null_assistant_with_error():
    """On AI failure, assistant_message=None + error set. Mobile renders inline."""
    from models import SendMessageResponse, ChatMessage, AssistantError

    user_msg = ChatMessage(
        id="m-1",
        conversation_id="c-1",
        role="user",
        content="hi",
        message_type="text",
        created_at="2026-05-28T00:00:00Z",
    )
    resp = SendMessageResponse(
        user_message=user_msg,
        assistant_message=None,
        error=AssistantError(
            code="TRANSIENT",
            message="I'm having trouble connecting right now. Try again in a moment.",
            retryable=True,
        ),
    )
    assert resp.assistant_message is None
    assert resp.error.code == "TRANSIENT"
    assert resp.error.retryable is True
