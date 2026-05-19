# ---------------------------------------------------------------------------
# test_chat_routes.py — contract tests for /api/v1/chat/* + /api/v2/chat/*.
#
# ⭐ START HERE: every test below asserts ONE of three properties:
#   1. The response wraps `data` in the ApiResponse envelope (the 4-field
#      shape {success, msg, error, data}).
#   2. The DTO inside `data` matches the contract field-for-field.
#   3. The Day-2 placeholder flag-off behavior returns 503 with an
#      envelope-shaped error body.
#
# WHY THE FIXTURES (per chat-ai actual responses) ARE NOT USED YET?
# Per the agent definition Day 6-7 plan + A14 "live yral-chat-ai DB
# pulls need typed Rishi YES every time," these tests check shapes
# against the LOCKED contract doc (interface-contracts/00-api-contract.md)
# at Day 2. Day 6-7 parity sprint pulls live chat-ai responses + commits
# them as JSON fixtures + asserts byte-level equality.
#
# WHY 3 TESTS PER ENDPOINT (NOT 5)?
# Per the agent definition's "3-5 contract-fixture tests per endpoint"
# range. 3 covers: envelope shape, DTO field presence, flag-gate path.
# Going to 5 here would mostly add field-type tests Pydantic already
# enforces at parse time (redundant per A2.1 — don't test what the
# library guarantees).
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# pytest — used by the WebSocket inbox stub test that needs the
# WebSocketDisconnect import path to assert the stub's close-code.
import pytest

# starlette.websockets.WebSocketDisconnect — raised by the test client
# when the server closes the WS connection. The BLOCKER-4 WS stub
# closes immediately after sending its error frame; this exception
# carries the close code + reason the test asserts on.
from starlette.websockets import WebSocketDisconnect

# (Conftest provides `client` (flag ON) + `client_flag_off` (flag OFF).
# No imports needed for fixtures — pytest finds them by name match.)


# ===========================================================================
# POST /api/v1/chat/conversations
# ===========================================================================


def test_create_conversation_returns_envelope_shape(client):
    """Envelope contract: 4 fields present + correct types."""
    response = client.post(
        "/api/v1/chat/conversations",
        json={"ai_influencer_id": "test-influencer-id", "conversation_type": "ai_chat"},
    )
    assert response.status_code == 200
    body = response.json()
    # All 4 envelope fields present per the contract.
    assert set(body.keys()) == {"success", "msg", "error", "data"}
    assert isinstance(body["success"], bool)
    assert body["success"] is True
    assert isinstance(body["msg"], str)
    assert body["error"] is None  # success path always None
    assert body["data"] is not None


def test_create_conversation_data_matches_conversation_response(client):
    """DTO contract: every ConversationResponse field present + right type."""
    response = client.post(
        "/api/v1/chat/conversations",
        json={"ai_influencer_id": "test-influencer-id", "conversation_type": "ai_chat"},
    )
    data = response.json()["data"]
    # Required fields per interface-contracts/00-api-contract.md ConversationResponse.
    assert isinstance(data["id"], str)
    assert isinstance(data["user_id"], str)
    assert data["conversation_type"] in ("ai_chat", "human_chat", "chat_as_human")
    assert isinstance(data["last_message_at"], str)
    assert isinstance(data["unread_count"], int)
    # `ai_influencer_id` echoes the request body so mobile gets the
    # right id back (Day-4 RPC integration must preserve this).
    assert data["ai_influencer_id"] == "test-influencer-id"


# ===========================================================================
# BLOCKER 3 — conversation_type Literal + per-mode validation tests
# ===========================================================================


def test_create_conversation_ai_chat_echoes_mode_and_influencer(client):
    """ai_chat mode → response echoes conversation_type='ai_chat' + influencer.

    Codex PR #97 BLOCKER 3: previously the stub always returned
    "ai_chat" regardless of input; now it echoes whichever mode the
    client requested.
    """
    response = client.post(
        "/api/v1/chat/conversations",
        json={"ai_influencer_id": "infl-A", "conversation_type": "ai_chat"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["conversation_type"] == "ai_chat"
    assert data["ai_influencer_id"] == "infl-A"
    assert data["participant_b_id"] is None


def test_create_conversation_human_chat_echoes_mode_and_participant(client):
    """human_chat mode → response echoes conversation_type + participant_b_id."""
    response = client.post(
        "/api/v1/chat/conversations",
        json={"participant_b_id": "user-B", "conversation_type": "human_chat"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["conversation_type"] == "human_chat"
    assert data["participant_b_id"] == "user-B"
    assert data["ai_influencer_id"] is None


def test_create_conversation_chat_as_human_echoes_mode_and_influencer(client):
    """chat_as_human mode → echoes mode + ai_influencer_id (per the file
    docstring's interpretation: chat_as_human is the AI-Influencer-
    adopts-human-persona mode, still anchored to an AI Influencer)."""
    response = client.post(
        "/api/v1/chat/conversations",
        json={"ai_influencer_id": "infl-X", "conversation_type": "chat_as_human"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["conversation_type"] == "chat_as_human"
    assert data["ai_influencer_id"] == "infl-X"


def test_create_conversation_rejects_unknown_mode(client):
    """Unknown conversation_type → envelope-shaped 400 (BLOCKER 2 + 3).

    Pre-BLOCKER-3 the field accepted any string; the Literal tightening
    means unknown modes fail Pydantic validation → main.py's envelope
    handler returns HTTP 400 with error="validation_failed".
    """
    response = client.post(
        "/api/v1/chat/conversations",
        json={"ai_influencer_id": "infl-A", "conversation_type": "unknown_mode"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "validation_failed"


def test_create_conversation_rejects_ai_chat_without_influencer(client):
    """ai_chat WITHOUT ai_influencer_id → 400 envelope (BLOCKER 3 validator)."""
    response = client.post(
        "/api/v1/chat/conversations",
        json={"conversation_type": "ai_chat"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "validation_failed"


def test_create_conversation_rejects_human_chat_without_participant(client):
    """human_chat WITHOUT participant_b_id → 400 envelope (BLOCKER 3 validator)."""
    response = client.post(
        "/api/v1/chat/conversations",
        json={"conversation_type": "human_chat"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "validation_failed"


def test_create_conversation_rejects_ai_chat_with_participant_b(client):
    """ai_chat with BOTH participant_b_id AND ai_influencer_id → 400 envelope."""
    response = client.post(
        "/api/v1/chat/conversations",
        json={
            "ai_influencer_id": "infl-A",
            "participant_b_id": "user-B",
            "conversation_type": "ai_chat",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "validation_failed"


def test_create_conversation_returns_503_when_flag_off(client_flag_off):
    """Production-safety contract: stub responses don't leak."""
    response = client_flag_off.post(
        "/api/v1/chat/conversations",
        json={"ai_influencer_id": "test-influencer-id", "conversation_type": "ai_chat"},
    )
    assert response.status_code == 503
    body = response.json()
    # 503 body is still envelope-shaped per the envelope-aware handler.
    assert body["success"] is False
    assert body["error"] == "service_unavailable"


# ===========================================================================
# GET /api/v1/chat/conversations
# ===========================================================================


def test_list_conversations_v1_returns_envelope_with_list(client):
    """v1 inbox: envelope wraps a list[ConversationResponse]."""
    response = client.get("/api/v1/chat/conversations")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert len(body["data"]) >= 1  # stub returns at least one


def test_list_conversations_v1_each_item_matches_response(client):
    """v1 inbox: every item in the list has the ConversationResponse shape."""
    response = client.get("/api/v1/chat/conversations")
    for conv in response.json()["data"]:
        assert "id" in conv
        assert "user_id" in conv
        assert "conversation_type" in conv
        assert "last_message_at" in conv
        assert "unread_count" in conv


def test_list_conversations_v1_returns_503_when_flag_off(client_flag_off):
    """v1 inbox: flag-off path returns 503 service_unavailable."""
    response = client_flag_off.get("/api/v1/chat/conversations")
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"


# ===========================================================================
# POST /api/v1/chat/conversations/{id}/messages
# ===========================================================================


def test_send_message_returns_envelope_with_assistant_reply(client):
    """Send-message: envelope success + assistant reply in data."""
    response = client.post(
        "/api/v1/chat/conversations/conv-id-1/messages",
        json={"content": "Hello", "client_message_id": "client-msg-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["role"] == "assistant"


def test_send_message_echoes_conversation_id_and_client_message_id(client):
    """Send-message: stub echoes conversation_id + client_message_id
    so mobile's local dedup matches the response to the outgoing request."""
    response = client.post(
        "/api/v1/chat/conversations/conv-id-XYZ/messages",
        json={"content": "test", "client_message_id": "client-msg-XYZ"},
    )
    data = response.json()["data"]
    assert data["conversation_id"] == "conv-id-XYZ"
    assert data["client_message_id"] == "client-msg-XYZ"


def test_send_message_data_matches_message_response(client):
    """Send-message: every MessageResponse field present."""
    response = client.post(
        "/api/v1/chat/conversations/conv-id-1/messages",
        json={"content": "Hello"},
    )
    data = response.json()["data"]
    # Required fields per interface-contracts/00-api-contract.md MessageResponse.
    assert isinstance(data["id"], str)
    assert isinstance(data["conversation_id"], str)
    assert data["role"] in ("user", "assistant")
    assert isinstance(data["content"], str)
    assert isinstance(data["created_at"], str)
    assert isinstance(data["count_toward_paywall"], bool)


def test_send_message_returns_503_when_flag_off(client_flag_off):
    """Send-message: flag-off path returns 503."""
    response = client_flag_off.post(
        "/api/v1/chat/conversations/conv-id-1/messages",
        json={"content": "test"},
    )
    assert response.status_code == 503


# ===========================================================================
# GET /api/v1/chat/conversations/{id}/messages
# ===========================================================================


def test_list_messages_returns_envelope_with_list(client):
    """Paginated history: envelope wraps a list[MessageResponse]."""
    response = client.get("/api/v1/chat/conversations/conv-id-1/messages")
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


def test_list_messages_each_item_matches_message_response(client):
    """Paginated history: each message has the MessageResponse shape."""
    response = client.get("/api/v1/chat/conversations/conv-id-1/messages")
    for msg in response.json()["data"]:
        assert "id" in msg
        assert msg["role"] in ("user", "assistant")
        assert "content" in msg
        assert "created_at" in msg


def test_list_messages_accepts_pagination_query_params(client):
    """Paginated history: limit + before query params are accepted (even
    though Day-2 ignores them — Day-4 wires them into the real query)."""
    response = client.get(
        "/api/v1/chat/conversations/conv-id-1/messages?limit=5&before=msg-id-1",
    )
    assert response.status_code == 200


# ===========================================================================
# POST /api/v1/chat/conversations/{id}/read
# ===========================================================================


def test_mark_read_returns_envelope_with_empty_data(client):
    """Mark-read: envelope success + empty {} data per the contract."""
    response = client.post(
        "/api/v1/chat/conversations/conv-id-1/read",
        json={"last_read_message_id": "msg-id-1"},
    )
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {}


def test_mark_read_rejects_missing_body_field(client):
    """Mark-read: Pydantic validation now returns envelope-shaped 400.

    Codex PR #97 BLOCKER 2: the locked contract requires every endpoint
    (including validation failures) to return the ApiResponse envelope.
    main.py's `envelope_validation_error_handler` catches the
    RequestValidationError + emits HTTP 400 with
    `error="validation_failed"` (NOT FastAPI's default 422 +
    {"detail": [...]}).
    """
    response = client.post(
        "/api/v1/chat/conversations/conv-id-1/read",
        json={},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "validation_failed"
    # Pydantic's per-field detail still flows through inside data.errors
    # so a debug build can surface the per-field cause without breaking
    # the envelope contract.
    assert "errors" in body["data"]


def test_mark_read_returns_503_when_flag_off(client_flag_off):
    """Mark-read: flag-off path returns 503."""
    response = client_flag_off.post(
        "/api/v1/chat/conversations/conv-id-1/read",
        json={"last_read_message_id": "msg-id-1"},
    )
    assert response.status_code == 503


# ===========================================================================
# DELETE /api/v1/chat/conversations/{id}
# ===========================================================================


def test_delete_conversation_returns_envelope_with_empty_data(client):
    """Delete: envelope success + empty {} data."""
    response = client.delete("/api/v1/chat/conversations/conv-id-1")
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {}


def test_delete_conversation_returns_503_when_flag_off(client_flag_off):
    """Delete: flag-off path returns 503."""
    response = client_flag_off.delete("/api/v1/chat/conversations/conv-id-1")
    assert response.status_code == 503


# ===========================================================================
# GET /api/v2/chat/conversations
# ===========================================================================


def test_list_conversations_v2_returns_envelope_with_list(client):
    """v2 inbox (current mobile build): envelope wraps a list."""
    response = client.get("/api/v2/chat/conversations")
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert len(body["data"]) >= 1


def test_list_conversations_v2_returns_503_when_flag_off(client_flag_off):
    """v2 inbox: flag-off path returns 503."""
    response = client_flag_off.get("/api/v2/chat/conversations")
    assert response.status_code == 503


# ===========================================================================
# BLOCKER 4 — WebSocket inbox stub
# ===========================================================================


def test_ws_inbox_stub_closes_with_service_unavailable(client):
    """WS /api/v1/chat/ws/inbox/{user_id}: stub accepts, sends envelope
    error frame, closes 1011 with reason "service_unavailable_stub_days_14_18".

    Codex PR #97 BLOCKER 4: locked path; previously 404'd on upgrade.
    Now registered as a stub so mobile sees "feature not yet available"
    instead of "routing bug."
    """
    with client.websocket_connect("/api/v1/chat/ws/inbox/test-user-id") as websocket:
        # The stub sends ONE envelope-shaped error frame...
        frame = websocket.receive_json()
        assert frame["success"] is False
        assert frame["error"] == "service_unavailable"
        # ...then closes with code 1011 (server error per RFC 6455).
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()
        assert exc_info.value.code == 1011


# ===========================================================================
# RELATED FILES:
#   conftest.py                      — provides `client` + `client_flag_off`
#   ../../app/api/chat_routes.py     — the handlers under test
#   ../../app/api/response_models.py            — MessageResponse + ConversationResponse shapes
#   ../../app/api/envelope.py        — ApiResponse[T] shape every test asserts
#   ../../app/api/feature_flag.py    — the dependency the flag-off tests trigger
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                                    — locked endpoint paths + DTO shapes
# ===========================================================================
