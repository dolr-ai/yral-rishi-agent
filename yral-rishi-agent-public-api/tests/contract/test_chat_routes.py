# ---------------------------------------------------------------------------
# test_chat_routes.py — contract tests for /api/v1/chat/* + /api/v2/chat/*.
#
# ⭐ START HERE: every test below asserts ONE of three properties:
#   1. The response wraps `data` in the ApiResponse envelope (the 4-field
#      shape {success, msg, error, data}).
#   2. The response model inside `data` matches the contract field-for-field.
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
# range. 3 covers: envelope shape, response-model field presence,
# flag-gate path. Going to 5 here would mostly add field-type tests
# Pydantic already enforces at parse time (redundant per A2.1).
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
    """Envelope contract: 4 fields present + correct types.

    WHAT: POSTs a valid ai_chat create-conversation body + asserts the
          response body has exactly the 4 envelope keys (success, msg,
          error, data) with their contract-locked Python types.
    WHEN: happy-path with the placeholder flag ON; the create handler
          runs its stub body + emits a full envelope.
    WHY:  envelope shape is load-bearing per A8 — mobile's parser
          unwraps these 4 fields on EVERY endpoint, so any drift
          (e.g., a typo in "succes") breaks every screen on the app.
    """
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
    """ConversationResponse contract: every locked field present + typed.

    WHAT: asserts each ConversationResponse field declared in
          interface-contracts/00-api-contract.md is present + has the
          right Python type. Also asserts `ai_influencer_id` echoes
          the request body (not a hardcoded stub value).
    WHEN: happy-path with the placeholder flag ON.
    WHY:  guards against silent shape drift — if a future PR drops a
          field or changes a type, mobile's per-row inbox renderer
          would render blank cells or crash on a missing key.
    """
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
    """ai_chat mode → response echoes conversation_type + influencer.

    WHAT: POSTs an ai_chat request + asserts the response echoes both
          the mode AND the ai_influencer_id (NOT a hardcoded
          "ai_chat" / stub-influencer-id).
    WHEN: happy-path for the ai_chat branch of the per-mode validator
          (placeholder flag ON).
    WHY:  Codex PR #97 BLOCKER 3 — pre-fixup the stub always returned
          "ai_chat" regardless of input. This test guards against the
          regression returning by asserting echo-through.
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
    """human_chat mode → response echoes conversation_type + participant.

    WHAT: POSTs a human_chat request with `participant_b_id` set +
          asserts the response echoes the mode + participant.
    WHEN: happy-path for the human_chat branch of the per-mode validator.
    WHY:  E5 mandates H2H chat ships from day 1; this test proves the
          server correctly routes the H2H mode (a real user opening
          a thread with another real user, not an AI).
    """
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
    """chat_as_human mode → response echoes mode + ai_influencer_id.

    WHAT: POSTs a chat_as_human request + asserts the response echoes
          mode + influencer id (per the file docstring interpretation:
          chat_as_human is the AI-Influencer-adopts-human-persona
          mode, still anchored to an AI Influencer).
    WHEN: happy-path for the chat_as_human branch of the per-mode
          validator.
    WHY:  per B4 "Chat as Human" is exact-phrase product vocab;
          per E5 it ships from day 1 in one schema. This test
          ensures the third mode option doesn't silently fail to
          route distinctly from ai_chat.
    """
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

    WHAT: POSTs a conversation_type the Literal doesn't accept;
          asserts the response is HTTP 400 with envelope
          `error="validation_failed"`.
    WHEN: malformed input — client sends a typo / future-not-yet-
          locked mode.
    WHY:  pre-BLOCKER-3 the field accepted any string so unknown modes
          silently routed as ai_chat (the default). The Literal
          tightening + envelope handler ensure rejection is loud +
          parseable per the locked contract shape.
    """
    response = client.post(
        "/api/v1/chat/conversations",
        json={"ai_influencer_id": "infl-A", "conversation_type": "unknown_mode"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "validation_failed"


def test_create_conversation_rejects_ai_chat_without_influencer(client):
    """ai_chat WITHOUT ai_influencer_id → 400 envelope (BLOCKER 3).

    WHAT: POSTs an ai_chat body missing the required ai_influencer_id;
          asserts envelope-shaped 400 + validation_failed.
    WHEN: malformed input — client picks ai_chat but forgets to attach
          the influencer (would otherwise silently misroute at the
          orchestrator).
    WHY:  the per-mode validator in CreateConversationRequest catches
          this before any handler logic runs; this test guards against
          regression of that validation.
    """
    response = client.post(
        "/api/v1/chat/conversations",
        json={"conversation_type": "ai_chat"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "validation_failed"


def test_create_conversation_rejects_human_chat_without_participant(client):
    """human_chat WITHOUT participant_b_id → 400 envelope (BLOCKER 3).

    WHAT: POSTs a human_chat body missing the required participant_b_id;
          asserts envelope-shaped 400.
    WHEN: malformed input — client picks H2H but forgets to attach
          the other user.
    WHY:  symmetric to the ai_chat / influencer rule; the per-mode
          validator must equally enforce the H2H requirement.
    """
    response = client.post(
        "/api/v1/chat/conversations",
        json={"conversation_type": "human_chat"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "validation_failed"


def test_create_conversation_rejects_ai_chat_with_participant_b(client):
    """ai_chat with BOTH IDs → 400 envelope (BLOCKER 3 validator).

    WHAT: POSTs an ai_chat body that ALSO sets participant_b_id
          (illegal combo); asserts envelope-shaped 400.
    WHEN: malformed input — client conflates ai_chat with an H2H
          participant (would otherwise leak the participant to the
          orchestrator + cause confused routing).
    WHY:  the validator's "MUST be None for non-matching modes" rules
          need to fire on every off-mode field, not just on missing
          required ones. This test covers the over-supplied case.
    """
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
    """Production-safety contract: stub responses don't leak.

    WHAT: POSTs a valid create-conversation body with the placeholder
          flag OFF; asserts the response is HTTP 503 with envelope
          `error="service_unavailable"`.
    WHEN: production-default state (flag default-False) — the
          placeholder body must NOT be served.
    WHY:  guards the production-safety contract that motivated the
          flag in the first place: a half-built v2 cluster cannot
          accidentally serve fake responses to real mobile traffic.
    """
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
    """v1 inbox: envelope wraps a list[ConversationResponse].

    WHAT: GETs the v1 inbox; asserts envelope shape + the data is a
          non-empty list (stubs ship at least one).
    WHEN: happy-path with placeholder flag ON.
    WHY:  the inbox is the entry point for every chat flow; if its
          shape drifts, the mobile inbox screen breaks before any
          per-conversation drill-down even loads.
    """
    response = client.get("/api/v1/chat/conversations")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert len(body["data"]) >= 1  # stub returns at least one


def test_list_conversations_v1_each_item_matches_response(client):
    """v1 inbox: every item in the list has the ConversationResponse shape.

    WHAT: GETs the inbox + iterates the list, asserting each item
          carries the required fields.
    WHEN: happy-path with the flag ON.
    WHY:  even if the LIST returns OK, mobile renders each item by
          field; a missing field on one row = blank or crash. This
          asserts uniformity across all returned rows.
    """
    response = client.get("/api/v1/chat/conversations")
    for conv in response.json()["data"]:
        assert "id" in conv
        assert "user_id" in conv
        assert "conversation_type" in conv
        assert "last_message_at" in conv
        assert "unread_count" in conv


def test_list_conversations_v1_returns_503_when_flag_off(client_flag_off):
    """v1 inbox: flag-off path returns 503 service_unavailable.

    WHAT: GETs the v1 inbox with the placeholder flag OFF; asserts
          envelope-shaped 503.
    WHEN: production-default state.
    WHY:  same production-safety contract as the create endpoint —
          inbox stubs must not leak to mobile traffic.
    """
    response = client_flag_off.get("/api/v1/chat/conversations")
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"


# ===========================================================================
# POST /api/v1/chat/conversations/{id}/messages
#
# NOTE: the 4 Day-2 stub-behavior tests that used to live here
# (test_send_message_returns_envelope_with_assistant_reply,
# test_send_message_echoes_conversation_id_and_client_message_id,
# test_send_message_data_matches_message_response,
# test_send_message_returns_503_when_flag_off) were DELETED in Day-4C
# under the A1 relaxed 7-step. They asserted the Day-2 stub's behavior
# (assistant-role echo, client_message_id echo, MessageResponse shape
# from a hardcoded factory, 503 on flag-off). Day-4C rewrote
# send_message to call the orchestrator's /v1/turn RPC + the
# placeholder-flag dependency is no longer applied to this handler,
# so the assertions no longer hold. The 7 new tests in
# test_orchestrator_proxy.py cover the new contract end-to-end
# (happy turn forwarding, idempotency hit/miss, error mapping for
# 503/422/timeout, per-user-id cache scope) — strictly stronger than
# the deleted Day-2 stub tests. The OTHER chat-handler tests in this
# file (create_conversation, list_v1/v2, mark_read, delete, list_messages)
# still apply because those handlers still return Day-2 stubs.
# ===========================================================================


# ===========================================================================
# GET /api/v1/chat/conversations/{id}/messages
# ===========================================================================


def test_list_messages_returns_envelope_with_list(client):
    """Paginated history: envelope wraps a list[MessageResponse].

    WHAT: GETs the message history + asserts envelope wraps a list.
    WHEN: happy-path with placeholder flag ON.
    WHY:  mobile's transcript renderer iterates this list; envelope-
          wrap is non-negotiable per A8.
    """
    response = client.get("/api/v1/chat/conversations/conv-id-1/messages")
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


def test_list_messages_each_item_matches_message_response(client):
    """Paginated history: each item has the MessageResponse shape.

    WHAT: GETs message history + asserts each item carries the
          required fields.
    WHEN: happy-path with placeholder flag ON.
    WHY:  same uniformity argument as the conversation list test —
          mobile renders each message by field.
    """
    response = client.get("/api/v1/chat/conversations/conv-id-1/messages")
    for msg in response.json()["data"]:
        assert "id" in msg
        assert msg["role"] in ("user", "assistant")
        assert "content" in msg
        assert "created_at" in msg


def test_list_messages_accepts_pagination_query_params(client):
    """Paginated history: limit + before query params accepted (200).

    WHAT: GETs message history with `?limit=5&before=msg-id-1`;
          asserts HTTP 200 (params accepted, not 4xx-rejected).
    WHEN: happy-path with placeholder flag ON. Day-2 IGNORES the
          values; Day-4 wires them into the real query.
    WHY:  the route signature must accept the contract's pagination
          params from day 1 even though the stub doesn't honor them
          — mobile sends them on every page-up event.
    """
    response = client.get(
        "/api/v1/chat/conversations/conv-id-1/messages?limit=5&before=msg-id-1",
    )
    assert response.status_code == 200


# ===========================================================================
# POST /api/v1/chat/conversations/{id}/read
# ===========================================================================


def test_mark_read_returns_envelope_with_empty_data(client):
    """Mark-read: envelope success + empty {} data per the contract.

    WHAT: POSTs a mark-read request + asserts envelope success + the
          contract-locked empty-object data shape.
    WHEN: happy-path with placeholder flag ON.
    WHY:  mark-read is a fire-and-forget action; mobile reads the
          envelope to confirm 200 + ignores the data body, but the
          contract requires the empty-object shape so future fields
          can be added as a strict superset.
    """
    response = client.post(
        "/api/v1/chat/conversations/conv-id-1/read",
        json={"last_read_message_id": "msg-id-1"},
    )
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {}


def test_mark_read_rejects_missing_body_field(client):
    """Mark-read: Pydantic validation now returns envelope-shaped 400.

    WHAT: POSTs a mark-read body missing the required
          `last_read_message_id`; asserts HTTP 400 with envelope
          `error="validation_failed"` AND
          `data.errors` containing the per-field detail.
    WHEN: malformed input.
    WHY:  Codex PR #97 BLOCKER 2 — the locked contract requires
          every endpoint (incl. validation failures) to return the
          envelope. main.py's `envelope_validation_error_handler`
          catches RequestValidationError + emits HTTP 400 with
          `error="validation_failed"` instead of FastAPI's default
          422 + `{"detail": [...]}`.
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
    """Mark-read: flag-off path returns 503.

    WHAT: POSTs a valid mark-read body with the placeholder flag OFF;
          asserts HTTP 503.
    WHEN: production-default state.
    WHY:  even no-op endpoints get the production-safety gate so the
          server can't pretend to have marked reads when there's no
          real persistence behind the call.
    """
    response = client_flag_off.post(
        "/api/v1/chat/conversations/conv-id-1/read",
        json={"last_read_message_id": "msg-id-1"},
    )
    assert response.status_code == 503


# ===========================================================================
# DELETE /api/v1/chat/conversations/{id}
# ===========================================================================


def test_delete_conversation_returns_envelope_with_empty_data(client):
    """Delete: envelope success + empty {} data.

    WHAT: DELETEs a conversation + asserts envelope success + empty
          data shape.
    WHEN: happy-path with placeholder flag ON.
    WHY:  swipe-to-delete is a user-facing inbox interaction; mobile
          relies on success=true to remove the row from local state.
    """
    response = client.delete("/api/v1/chat/conversations/conv-id-1")
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {}


def test_delete_conversation_returns_503_when_flag_off(client_flag_off):
    """Delete: flag-off path returns 503.

    WHAT: DELETEs a conversation with the placeholder flag OFF;
          asserts 503.
    WHEN: production-default state.
    WHY:  same production-safety contract — server must not falsely
          confirm a delete when no real persistence layer is wired.
    """
    response = client_flag_off.delete("/api/v1/chat/conversations/conv-id-1")
    assert response.status_code == 503


# ===========================================================================
# GET /api/v2/chat/conversations
# ===========================================================================


def test_list_conversations_v2_returns_envelope_with_list(client):
    """v2 inbox (current mobile build): envelope wraps a list.

    WHAT: GETs the v2 inbox + asserts envelope wraps a list of at
          least one item.
    WHEN: happy-path with placeholder flag ON.
    WHY:  v2 inbox is what the current mobile build hits; the v1
          path stays for backward-compat but mobile prefers v2's
          bot-aware row shape.
    """
    response = client.get("/api/v2/chat/conversations")
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert len(body["data"]) >= 1


def test_list_conversations_v2_returns_503_when_flag_off(client_flag_off):
    """v2 inbox: flag-off path returns 503.

    WHAT: GETs the v2 inbox with the placeholder flag OFF; asserts 503.
    WHEN: production-default state.
    WHY:  parity with the v1 inbox flag-off test — both must obey the
          production-safety gate.
    """
    response = client_flag_off.get("/api/v2/chat/conversations")
    assert response.status_code == 503


# ===========================================================================
# BLOCKER 4 — WebSocket inbox stub
# ===========================================================================


def test_ws_inbox_stub_closes_with_service_unavailable(client):
    """WS /api/v1/chat/ws/inbox/{user_id}: stub frame + 1011 close.

    WHAT: connects to the WebSocket inbox; asserts the stub sends one
          envelope-shaped error frame (`error="service_unavailable"`)
          then closes with WebSocket close code 1011.
    WHEN: any connect attempt before the real inbox implementation
          (Days 14-18 per agent definition).
    WHY:  Codex PR #97 BLOCKER 4 — locked path; previously 404'd on
          upgrade. Now registered as a stub so mobile sees "feature
          not yet available" instead of "routing bug." Mobile reads
          the close-reason + payload to surface the right user-facing
          state.
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
#   ../../app/api/response_models.py — MessageResponse + ConversationResponse shapes
#   ../../app/api/envelope.py        — ApiResponse[T] shape every test asserts
#   ../../app/api/feature_flag.py    — the dependency the flag-off tests trigger
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                                    — locked endpoint paths + response-model shapes
# ===========================================================================
