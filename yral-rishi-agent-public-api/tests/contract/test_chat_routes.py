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

# Conftest provides `client` (flag ON) + `client_flag_off` (flag OFF).
# No imports needed for fixtures — pytest finds them by name match.


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


def test_create_conversation_data_matches_conversation_dto(client):
    """DTO contract: every ConversationDto field present + right type."""
    response = client.post(
        "/api/v1/chat/conversations",
        json={"ai_influencer_id": "test-influencer-id", "conversation_type": "ai_chat"},
    )
    data = response.json()["data"]
    # Required fields per interface-contracts/00-api-contract.md ConversationDto.
    assert isinstance(data["id"], str)
    assert isinstance(data["user_id"], str)
    assert data["conversation_type"] in ("ai_chat", "human_chat", "chat_as_human")
    assert isinstance(data["last_message_at"], str)
    assert isinstance(data["unread_count"], int)
    # `ai_influencer_id` echoes the request body so mobile gets the
    # right id back (Day-4 RPC integration must preserve this).
    assert data["ai_influencer_id"] == "test-influencer-id"


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
    """v1 inbox: envelope wraps a list[ConversationDto]."""
    response = client.get("/api/v1/chat/conversations")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert len(body["data"]) >= 1  # stub returns at least one


def test_list_conversations_v1_each_item_matches_dto(client):
    """v1 inbox: every item in the list has the ConversationDto shape."""
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


def test_send_message_data_matches_message_dto(client):
    """Send-message: every MessageDto field present."""
    response = client.post(
        "/api/v1/chat/conversations/conv-id-1/messages",
        json={"content": "Hello"},
    )
    data = response.json()["data"]
    # Required fields per interface-contracts/00-api-contract.md MessageDto.
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
    """Paginated history: envelope wraps a list[MessageDto]."""
    response = client.get("/api/v1/chat/conversations/conv-id-1/messages")
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


def test_list_messages_each_item_matches_message_dto(client):
    """Paginated history: each message has the MessageDto shape."""
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
    """Mark-read: Pydantic validation catches a missing required field."""
    response = client.post(
        "/api/v1/chat/conversations/conv-id-1/read",
        json={},
    )
    assert response.status_code == 422  # FastAPI's default validation error


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
# RELATED FILES:
#   conftest.py                      — provides `client` + `client_flag_off`
#   ../../app/api/chat_routes.py     — the handlers under test
#   ../../app/api/dtos.py            — MessageDto + ConversationDto shapes
#   ../../app/api/envelope.py        — ApiResponse[T] shape every test asserts
#   ../../app/api/feature_flag.py    — the dependency the flag-off tests trigger
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                                    — locked endpoint paths + DTO shapes
# ===========================================================================
