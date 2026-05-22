# ---------------------------------------------------------------------------
# test_conversation_routes.py — HTTP-level tests for the 4 conversation +
#   message RPC endpoints.
#
# ⭐ START HERE: this file tests the 4 routes wired in Deliverable 2:
#
#   POST /v1/conversations                 — create_or_get_conversation
#   POST /v1/conversations/{id}/messages   — append_messages
#   GET  /v1/conversations/by-user/{uid}   — list_conversations_by_user
#   GET  /v1/conversations/{id}/messages   — list_messages
#
# TESTING APPROACH:
# Every test uses the `test_client` fixture (defined in conftest.py) which:
#   1. Creates a fresh asyncpg pool connected to testcontainers Postgres.
#   2. Injects that pool into app.database._pool before the FastAPI lifespan.
#   3. Truncates all tables for a clean per-test state.
#   4. Yields an httpx.AsyncClient backed by ASGITransport (in-process, no network).
#   5. Cleans up after yield (lifespan teardown closes the pool).
#
# WHY REAL SQL (not mocks)?
# Per J1 + the testing strategy: these are HOT-PATH endpoints. Mocking the
# DB would let us verify request parsing but not the SQL correctness — index
# usage, FK constraints, NULL handling, JSONB round-trips. We use a real
# ephemeral Postgres to catch SQL bugs before they hit the cluster.
#
# TEST ORDER: fixtures are independent; tests are ordered logically
# (create → append → list) but any ordering should work.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import pytest
from httpx import AsyncClient


# ===========================================================================
# POST /v1/conversations — create_or_get_conversation
# ===========================================================================


@pytest.mark.asyncio
async def test_create_conversation_returns_conversation_response_shape(
    test_client: AsyncClient,
) -> None:
    """WHAT: POST /v1/conversations returns a ConversationResponse-shaped payload.
    WHEN: a new conversation is requested.
    WHY:  the locked wire contract requires specific field names + types
          (per A8 + A16). A missing or renamed field breaks public-api and
          mobile without a visible error at deploy time.
    """
    response = await test_client.post(
        "/v1/conversations",
        json={
            "user_id": "user-abc",
            "ai_influencer_id": "influencer-xyz",
            "conversation_type": "ai_chat",
        },
    )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()

    # All required fields must be present
    required_fields = {
        "id", "user_id", "participant_b_id", "ai_influencer_id",
        "conversation_type", "last_message", "last_message_at", "unread_count",
    }
    for field in required_fields:
        assert field in data, f"Missing field in ConversationResponse: {field}"

    # Field value checks
    assert data["user_id"] == "user-abc"
    assert data["ai_influencer_id"] == "influencer-xyz"
    assert data["participant_b_id"] is None
    assert data["conversation_type"] == "ai_chat"
    assert data["last_message"] is None  # new conversation, no messages
    assert data["unread_count"] == 0
    # id must be a UUID-shaped string
    assert len(data["id"]) == 36


@pytest.mark.asyncio
async def test_create_conversation_upsert_returns_same_id_on_second_call(
    test_client: AsyncClient,
) -> None:
    """WHAT: POST /v1/conversations is idempotent for the same natural key.
    WHEN: the same (user_id, conversation_type, ai_influencer_id) is passed twice.
    WHY:  chat-ai's create-or-get contract (per A8) prevents duplicate
          conversation rows when mobile retries or re-opens the same chat.
          Breaking this doubles inbox rows for users.
    """
    payload = {
        "user_id": "user-upsert",
        "ai_influencer_id": "influencer-upsert",
        "conversation_type": "ai_chat",
    }

    # First call — creates the conversation
    response_a = await test_client.post("/v1/conversations", json=payload)
    assert response_a.status_code == 200
    id_a = response_a.json()["id"]

    # Second call — should return the SAME conversation
    response_b = await test_client.post("/v1/conversations", json=payload)
    assert response_b.status_code == 200
    id_b = response_b.json()["id"]

    assert id_a == id_b, (
        f"Expected same conversation_id on upsert, got different: {id_a} vs {id_b}"
    )


@pytest.mark.asyncio
async def test_create_human_chat_conversation_uses_participant_b_id(
    test_client: AsyncClient,
) -> None:
    """WHAT: human_chat conversations use participant_b_id and null ai_influencer_id.
    WHEN: conversation_type is "human_chat".
    WHY:  public-api's inbox renders H2H differently from AI chat; the
          field values drive that branching. Wrong field mapping silently
          misrenders the inbox row.
    """
    response = await test_client.post(
        "/v1/conversations",
        json={
            "user_id": "user-human",
            "participant_b_id": "user-human-b",
            "conversation_type": "human_chat",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["conversation_type"] == "human_chat"
    assert data["participant_b_id"] == "user-human-b"
    assert data["ai_influencer_id"] is None


# ===========================================================================
# POST /v1/conversations/{id}/messages — append_messages
# ===========================================================================


@pytest.mark.asyncio
async def test_append_messages_returns_user_and_assistant_responses(
    test_client: AsyncClient,
) -> None:
    """WHAT: appending [user_message, assistant_reply] returns both as MessageResponse.
    WHEN: the orchestrator calls POST .../messages at the end of a turn.
    WHY:  the response list lets the orchestrator confirm both rows were
          persisted with the right UUIDs + timestamps before returning to
          public-api.
    """
    # Create a conversation first
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={"user_id": "user-turn", "ai_influencer_id": "influencer-turn", "conversation_type": "ai_chat"},
    )
    conv_id = conv_resp.json()["id"]

    # Append a turn (user + assistant)
    response = await test_client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Hello Tara",
                    "client_message_id": "client-001",
                    "count_toward_paywall": True,
                },
                {
                    "role": "assistant",
                    "content": "Hi! How are you?",
                    "gemini_metadata": {"prompt_tokens": 50, "completion_tokens": 10},
                    "count_toward_paywall": True,
                },
            ]
        },
    )

    assert response.status_code == 200
    messages = response.json()
    assert len(messages) == 2  # both non-system messages returned

    # Verify user message shape
    user_msg = messages[0]
    assert user_msg["role"] == "user"
    assert user_msg["content"] == "Hello Tara"
    assert user_msg["client_message_id"] == "client-001"
    assert user_msg["conversation_id"] == conv_id
    assert user_msg["count_toward_paywall"] is True
    assert "id" in user_msg and len(user_msg["id"]) == 36
    assert "created_at" in user_msg

    # Verify assistant message shape
    asst_msg = messages[1]
    assert asst_msg["role"] == "assistant"
    assert asst_msg["content"] == "Hi! How are you?"
    assert asst_msg["client_message_id"] is None  # AI replies carry no client ID
    assert asst_msg["count_toward_paywall"] is True


@pytest.mark.asyncio
async def test_append_messages_filters_system_role_from_response(
    test_client: AsyncClient,
) -> None:
    """WHAT: system-role messages are stored but not included in the response list.
    WHEN: the orchestrator includes a system context message in the batch.
    WHY:  mobile's MessageResponse.role is Literal["user", "assistant"]; a
          "system" role value on the wire would break mobile's type parser.
    """
    # Create a conversation
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={"user_id": "user-system-msg", "ai_influencer_id": "inf-sys", "conversation_type": "ai_chat"},
    )
    conv_id = conv_resp.json()["id"]

    response = await test_client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={
            "messages": [
                {"role": "system", "content": "[context frame]", "count_toward_paywall": False},
                {"role": "user", "content": "Hello", "count_toward_paywall": True},
                {"role": "assistant", "content": "Hi!", "count_toward_paywall": True},
            ]
        },
    )

    assert response.status_code == 200
    messages = response.json()

    # system message must NOT appear in response
    assert len(messages) == 2, (
        f"Expected 2 messages (system filtered out), got {len(messages)}: {messages}"
    )
    roles = [m["role"] for m in messages]
    assert "system" not in roles, f"system role should be filtered from response, got: {roles}"


@pytest.mark.asyncio
async def test_append_messages_to_nonexistent_conversation_returns_404(
    test_client: AsyncClient,
) -> None:
    """WHAT: POST .../messages returns 404 when the conversation_id doesn't exist.
    WHEN: the orchestrator passes a conversation_id that was never created or
          was hard-deleted at the DB level.
    WHY:  a deterministic 404 lets the orchestrator distinguish "conversation
          missing" from "DB error" (which would be a 500). Without the 404,
          the FK violation from Postgres maps to an ambiguous 500.
    """
    import uuid
    fake_id = str(uuid.uuid4())

    response = await test_client.post(
        f"/v1/conversations/{fake_id}/messages",
        json={"messages": [{"role": "user", "content": "test"}]},
    )

    assert response.status_code == 404, (
        f"Expected 404 for non-existent conversation, got {response.status_code}"
    )


@pytest.mark.asyncio
async def test_append_messages_updates_conversation_stats(
    test_client: AsyncClient,
) -> None:
    """WHAT: appending messages updates conversations.last_message_at +
             message_count.
    WHEN: after POST .../messages succeeds.
    WHY:  last_message_at drives the inbox sort order; message_count is the
          ETL verification counter. Both must be correct for the inbox to
          show conversations in the right order after each turn.
    """
    # Create conversation
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={"user_id": "user-stats", "ai_influencer_id": "inf-stats", "conversation_type": "ai_chat"},
    )
    conv_id = conv_resp.json()["id"]
    initial_last_at = conv_resp.json()["last_message_at"]

    # Append two messages (user + assistant)
    await test_client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
            ]
        },
    )

    # Fetch the conversation via the by-user endpoint to check updated stats
    list_resp = await test_client.get("/v1/conversations/by-user/user-stats")
    assert list_resp.status_code == 200
    conversations = list_resp.json()
    assert len(conversations) == 1
    conv = conversations[0]

    # last_message_at must have advanced (it's a newer timestamp)
    assert conv["last_message_at"] >= initial_last_at, (
        f"last_message_at did not advance: {conv['last_message_at']!r} <= {initial_last_at!r}"
    )
    # last_message should now be populated
    assert conv["last_message"] is not None
    # The last non-system message is the assistant reply
    assert conv["last_message"]["role"] == "assistant"
    assert conv["last_message"]["content"] == "reply"


# ===========================================================================
# GET /v1/conversations/by-user/{user_id} — list_conversations_by_user
# ===========================================================================


@pytest.mark.asyncio
async def test_list_conversations_by_user_returns_empty_list_for_new_user(
    test_client: AsyncClient,
) -> None:
    """WHAT: GET .../by-user/{uid} returns [] when the user has no conversations.
    WHEN: a user_id with no rows in the conversations table is queried.
    WHY:  mobile inbox must show an empty list, not a 404 or 500, for a
          brand-new user who has never opened a chat.
    """
    response = await test_client.get("/v1/conversations/by-user/user-new-never-chatted")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_conversations_by_user_includes_last_message_inline(
    test_client: AsyncClient,
) -> None:
    """WHAT: GET .../by-user/{uid} returns conversations with last_message populated.
    WHEN: the user has a conversation with at least one message.
    WHY:  mobile uses last_message for the inbox row subtitle. Missing it
          = blank inbox subtitles for all conversations.
    """
    # Setup: create conversation, append a turn
    await test_client.post(
        "/v1/conversations",
        json={"user_id": "user-inbox", "ai_influencer_id": "inf-inbox", "conversation_type": "ai_chat"},
    )
    conv_resp2 = await test_client.post(
        "/v1/conversations",
        json={"user_id": "user-inbox", "ai_influencer_id": "inf-inbox", "conversation_type": "ai_chat"},
    )
    conv_id = conv_resp2.json()["id"]

    await test_client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={
            "messages": [
                {"role": "user", "content": "inbox test"},
                {"role": "assistant", "content": "inbox reply"},
            ]
        },
    )

    # Verify the inbox endpoint
    response = await test_client.get("/v1/conversations/by-user/user-inbox")
    assert response.status_code == 200
    conversations = response.json()
    assert len(conversations) == 1

    conv = conversations[0]
    assert conv["user_id"] == "user-inbox"
    assert conv["ai_influencer_id"] == "inf-inbox"

    # last_message must be populated and match the assistant reply
    assert conv["last_message"] is not None
    lm = conv["last_message"]
    assert lm["role"] == "assistant"
    assert lm["content"] == "inbox reply"
    # MessageResponse fields must all be present
    for field in ("id", "conversation_id", "role", "content", "created_at", "count_toward_paywall"):
        assert field in lm, f"last_message missing field: {field}"


@pytest.mark.asyncio
async def test_list_conversations_by_user_limit_param_is_respected(
    test_client: AsyncClient,
) -> None:
    """WHAT: the `limit` query parameter caps the number of conversations returned.
    WHEN: the user has more conversations than the requested limit.
    WHY:  mobile's inbox pagination depends on limit being enforced; returning
          all rows on the first page would defeat the purpose.
    """
    # Create 3 conversations for the same user
    for i in range(3):
        await test_client.post(
            "/v1/conversations",
            json={
                "user_id": "user-limit",
                "ai_influencer_id": f"inf-limit-{i}",
                "conversation_type": "ai_chat",
            },
        )

    # Request only 2
    response = await test_client.get("/v1/conversations/by-user/user-limit?limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2, (
        f"Expected 2 conversations with limit=2, got {len(response.json())}"
    )


# ===========================================================================
# GET /v1/conversations/{id}/messages — list_messages
# ===========================================================================


@pytest.mark.asyncio
async def test_list_messages_returns_history_in_chronological_order(
    test_client: AsyncClient,
) -> None:
    """WHAT: GET .../messages returns messages sorted by created_at ASC.
    WHEN: the conversation has multiple messages.
    WHY:  the LLM requires context in chronological order (oldest first).
          Mobile renders the transcript in chronological order. Wrong order
          = incoherent LLM context + visually wrong chat transcript.
    """
    # Create conversation + append a multi-turn exchange
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={"user_id": "user-history", "ai_influencer_id": "inf-hist", "conversation_type": "ai_chat"},
    )
    conv_id = conv_resp.json()["id"]

    await test_client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={"messages": [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "first reply"},
        ]},
    )
    await test_client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={"messages": [
            {"role": "user", "content": "second message"},
            {"role": "assistant", "content": "second reply"},
        ]},
    )

    response = await test_client.get(f"/v1/conversations/{conv_id}/messages")
    assert response.status_code == 200
    messages = response.json()
    assert len(messages) == 4

    # Verify chronological order
    contents = [m["content"] for m in messages]
    assert contents == ["first message", "first reply", "second message", "second reply"], (
        f"Expected chronological order, got: {contents}"
    )

    # Verify timestamps are ascending
    timestamps = [m["created_at"] for m in messages]
    assert timestamps == sorted(timestamps), f"Messages not in ASC order: {timestamps}"


@pytest.mark.asyncio
async def test_list_messages_filters_system_role(
    test_client: AsyncClient,
) -> None:
    """WHAT: system-role messages stored in the DB do not appear in GET .../messages.
    WHEN: a batch contained system messages alongside user + assistant ones.
    WHY:  mobile's transcript renderer must never see role="system" — it
          would break the Literal["user","assistant"] type assumption
          and potentially render internal context frames in the chat UI.
    """
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={"user_id": "user-sys-filter", "ai_influencer_id": "inf-sf", "conversation_type": "ai_chat"},
    )
    conv_id = conv_resp.json()["id"]

    await test_client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={"messages": [
            {"role": "system", "content": "[system context]", "count_toward_paywall": False},
            {"role": "user", "content": "visible user msg"},
            {"role": "assistant", "content": "visible assistant msg"},
        ]},
    )

    response = await test_client.get(f"/v1/conversations/{conv_id}/messages")
    assert response.status_code == 200
    messages = response.json()

    roles = [m["role"] for m in messages]
    assert "system" not in roles, f"system role leaked into GET response: {roles}"
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_list_messages_limit_param_is_respected(
    test_client: AsyncClient,
) -> None:
    """WHAT: the `limit` param caps the number of messages returned (most recent N).
    WHEN: the conversation has more messages than the requested limit.
    WHY:  the orchestrator's context fetch uses limit=N to get the last N
          turns; returning all history when N is small would blow the LLM
          context window.
    """
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={"user_id": "user-msg-limit", "ai_influencer_id": "inf-ml", "conversation_type": "ai_chat"},
    )
    conv_id = conv_resp.json()["id"]

    # Insert 6 messages (3 turns)
    for i in range(3):
        await test_client.post(
            f"/v1/conversations/{conv_id}/messages",
            json={"messages": [
                {"role": "user", "content": f"msg {i}"},
                {"role": "assistant", "content": f"reply {i}"},
            ]},
        )

    # Request only the last 2 messages
    response = await test_client.get(f"/v1/conversations/{conv_id}/messages?limit=2")
    assert response.status_code == 200
    messages = response.json()
    assert len(messages) == 2, f"Expected 2 messages with limit=2, got {len(messages)}"

    # The 2 returned should be the MOST RECENT 2 (msg 2 + reply 2)
    contents = [m["content"] for m in messages]
    assert "msg 2" in contents and "reply 2" in contents, (
        f"Expected most-recent messages, got: {contents}"
    )


@pytest.mark.asyncio
async def test_list_messages_before_cursor_returns_older_messages(
    test_client: AsyncClient,
) -> None:
    """WHAT: the `before` cursor returns messages older than the cursor message.
    WHEN: mobile scrolls up in the chat transcript to load history.
    WHY:  cursor-based pagination is the standard mobile load-more pattern.
          Wrong cursor semantics = user sees duplicate messages or skips
          messages when scrolling up.
    """
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={"user_id": "user-cursor", "ai_influencer_id": "inf-cursor", "conversation_type": "ai_chat"},
    )
    conv_id = conv_resp.json()["id"]

    # Insert 3 sequential messages (user only, for simplicity)
    # We use 3 separate POST calls so created_at timestamps are distinct.
    msg_ids = []
    for content in ("oldest", "middle", "newest"):
        resp = await test_client.post(
            f"/v1/conversations/{conv_id}/messages",
            json={"messages": [{"role": "user", "content": content}]},
        )
        msg_ids.append(resp.json()[0]["id"])

    oldest_id, middle_id, newest_id = msg_ids

    # Using `before=newest_id` should return the 2 older messages
    response = await test_client.get(
        f"/v1/conversations/{conv_id}/messages?before={newest_id}&limit=10"
    )
    assert response.status_code == 200
    messages = response.json()
    contents = [m["content"] for m in messages]

    assert "newest" not in contents, f"'newest' must not appear with before=newest_id: {contents}"
    assert "oldest" in contents and "middle" in contents, (
        f"Expected oldest + middle with before=newest_id, got: {contents}"
    )


@pytest.mark.asyncio
async def test_list_messages_for_nonexistent_conversation_returns_404(
    test_client: AsyncClient,
) -> None:
    """WHAT: GET .../messages returns 404 when the conversation_id doesn't exist.
    WHEN: the orchestrator or public-api passes a stale or wrong conversation_id.
    WHY:  a 404 is actionable (conversation missing) vs a 500 (server bug).
          Without this check, the route returns an empty list which the
          orchestrator would silently accept (wrong LLM context = bad replies).
    """
    import uuid
    fake_id = str(uuid.uuid4())

    response = await test_client.get(f"/v1/conversations/{fake_id}/messages")
    assert response.status_code == 404, (
        f"Expected 404 for non-existent conversation, got {response.status_code}"
    )


# ===========================================================================
# Health endpoint — /health/ready upgraded in D2 to ping Postgres
# ===========================================================================


@pytest.mark.asyncio
async def test_health_ready_returns_ok_with_connected_pool(
    test_client: AsyncClient,
) -> None:
    """WHAT: GET /health/ready returns 200 {"status":"ok"} when the DB pool is live.
    WHEN: the app is running with an initialised asyncpg pool (testcontainers).
    WHY:  Swarm + Caddy route traffic based on this endpoint. A broken
          /health/ready that always returns 200 would hide DB outages from
          the load balancer.
    """
    response = await test_client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "yral-rishi-agent-user-memory-service"


# ===========================================================================
# RELATED FILES:
#   conftest.py                  — test_client fixture (pool injection + httpx)
#   ../app/api/conversation_routes.py
#                                — the 4 route handlers under test
#   ../app/api/models.py         — request + response shapes asserted above
#   ../app/migrations/versions/001_initial_schema.py
#                                — schema under test (conversations + messages)
#   ../app/migrations/versions/002_add_message_fields.py
#                                — adds client_message_id + count_toward_paywall
# ===========================================================================
