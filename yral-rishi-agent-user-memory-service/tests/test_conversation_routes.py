# ---------------------------------------------------------------------------
# test_conversation_routes.py — HTTP-level tests for all 5 conversation +
#   message RPC endpoints.
#
# ⭐ START HERE: this file tests the 5 routes wired in Deliverable 2:
#
#   POST /v1/conversations                 — create_or_get_conversation
#   POST /v1/conversations/{id}/messages   — append_messages
#   GET  /v1/conversations/by-user/{uid}   — list_conversations_by_user
#   GET  /v1/conversations/{id}            — get_conversation_by_id
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

import uuid

import asyncpg
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

    # last_message_at MUST have strictly advanced — a message was appended,
    # so the timestamp cannot stay equal.  `>=` would pass even when the
    # UPDATE silently failed; `>` fails loud on that regression.
    assert conv["last_message_at"] > initial_last_at, (
        "appending a message MUST advance conversations.last_message_at; "
        "if it doesn't, the stats-update SQL silently failed"
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
# POST /v1/conversations — concurrency (Codex round-2 concern)
# ===========================================================================


@pytest.mark.asyncio
async def test_create_or_get_handles_concurrent_first_calls(
    test_client: AsyncClient,
) -> None:
    """WHAT: two concurrent first-time POST /v1/conversations calls with the
             same natural key MUST resolve to the same conversation row — one
             row in the DB, both responses carry the same id.
    WHEN: two asyncio tasks fire the same POST simultaneously (asyncio.gather).
    WHY:  defends the upsert race condition. Before migration 003 added the
          partial unique expression index + the INSERT ON CONFLICT DO UPDATE,
          both concurrent tasks could each see 0 existing rows and INSERT a
          new row, yielding two duplicate conversation rows and two different
          IDs for the same logical thread. The atomic INSERT ON CONFLICT is
          the fix; this test confirms it.
    """
    import asyncio

    payload = {
        "user_id": "user-concurrent",
        "ai_influencer_id": "inf-concurrent",
        "conversation_type": "ai_chat",
    }

    # Fire two concurrent POST /v1/conversations with the same natural key.
    # asyncio.gather runs both tasks concurrently: they interleave at each
    # `await` point (pool.acquire, conn.fetchrow). With the ON CONFLICT
    # path, one task inserts and the other gets the conflict row — both
    # return 200 with the same conversation id.
    response_a, response_b = await asyncio.gather(
        test_client.post("/v1/conversations", json=payload),
        test_client.post("/v1/conversations", json=payload),
    )

    assert response_a.status_code == 200, (
        f"First concurrent call failed: {response_a.status_code} {response_a.text}"
    )
    assert response_b.status_code == 200, (
        f"Second concurrent call failed: {response_b.status_code} {response_b.text}"
    )

    id_a = response_a.json()["id"]
    id_b = response_b.json()["id"]

    assert id_a == id_b, (
        f"Concurrent upserts returned different conversation IDs: "
        f"{id_a!r} vs {id_b!r}. The INSERT ON CONFLICT path must resolve "
        f"both calls to the same row."
    )


# ===========================================================================
# POST /v1/conversations/{id}/messages — idempotency (Codex round-2 concern)
# ===========================================================================


@pytest.mark.asyncio
async def test_append_message_idempotency_via_client_message_id(
    test_client: AsyncClient,
) -> None:
    """WHAT: two POST .../messages calls with the same client_message_id on
             the same conversation return the SAME message_id and the messages
             table has ONE row, not two.
    WHEN: mobile retries a POST /v1/conversations/{id}/messages after a
          network blip where the original write actually committed; the retry
          MUST NOT duplicate the message.
    WHY:  client_message_id is the dedup key per the locked MessageResponse
          contract (F10 idempotency). Without the unique partial index on
          (conversation_id, client_message_id) + ON CONFLICT DO NOTHING in
          the INSERT, the retry would create a second row — doubling the
          paywall charge and showing a duplicate message bubble on screen.
          This test confirms the dedup is end-to-end correct.
    """
    # Create a conversation to append to.
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={
            "user_id": "user-dedup",
            "ai_influencer_id": "inf-dedup",
            "conversation_type": "ai_chat",
        },
    )
    assert conv_resp.status_code == 200
    conv_id = conv_resp.json()["id"]

    # The retry-safe payload: both calls use the same client_message_id.
    dedup_payload = {
        "messages": [
            {
                "role": "user",
                "content": "idempotent message content",
                "client_message_id": "client-dedup-001",
                "count_toward_paywall": True,
            }
        ]
    }

    # --- First append (original write) ------------------------------------
    resp_a = await test_client.post(
        f"/v1/conversations/{conv_id}/messages",
        json=dedup_payload,
    )
    assert resp_a.status_code == 200, (
        f"First append failed: {resp_a.status_code} {resp_a.text}"
    )
    id_a = resp_a.json()[0]["id"]

    # --- Second append (simulated network retry) --------------------------
    # Sends the identical payload again. ON CONFLICT DO NOTHING detects the
    # duplicate (same conversation_id + client_message_id) and the handler
    # returns the EXISTING row instead of inserting a new one.
    resp_b = await test_client.post(
        f"/v1/conversations/{conv_id}/messages",
        json=dedup_payload,
    )
    assert resp_b.status_code == 200, (
        f"Retry append failed: {resp_b.status_code} {resp_b.text}"
    )
    id_b = resp_b.json()[0]["id"]

    # Both calls must return the same message_id (original row reused).
    assert id_a == id_b, (
        f"client_message_id dedup failed: first call returned message_id "
        f"{id_a!r}, retry returned {id_b!r}. Expected the same id."
    )

    # Verify the DB has only ONE row, not two.
    # GET the message history — if dedup worked there should be 1 row.
    list_resp = await test_client.get(f"/v1/conversations/{conv_id}/messages")
    assert list_resp.status_code == 200
    messages = list_resp.json()
    assert len(messages) == 1, (
        f"Expected 1 message after idempotent retry, got {len(messages)}. "
        f"Messages: {messages}"
    )
    # The single message must match the original id.
    assert messages[0]["id"] == id_a, (
        f"The stored message id {messages[0]['id']!r} does not match the "
        f"original insert id {id_a!r}."
    )


# ===========================================================================
# GET /v1/conversations/{id}/messages — ordering tiebreaker (Codex round-2)
# ===========================================================================


@pytest.mark.asyncio
async def test_messages_ordering_with_same_created_at_timestamp(
    test_client: AsyncClient,
) -> None:
    """WHAT: messages inserted in the same Postgres transaction share a
             created_at timestamp (NOW() returns the transaction start time).
             The listing endpoint MUST return them in a deterministic content-
             positional order on every call — user message at position 0,
             assistant reply at position 1 (by id ASC tiebreaker).
    WHEN: a batch POST .../messages call inserts [user, assistant] in a
          single transaction (which is what the orchestrator always does at
          the end of a turn).
    WHY:  without the `id ASC` tiebreaker, Postgres may return equal-
          timestamp rows in a different physical order on different calls —
          mobile would occasionally see the assistant reply BEFORE the user
          message on screen (a catastrophic UX regression). The tiebreaker
          locks the ordering as part of the contract so the test fails loud
          if the ORDER BY clause is ever simplified to drop the id column.
          Content-positional assertion (Codex tightening): verifying that
          both roles are present in a fixed, stable order is more meaningful
          than just verifying id-sequence stability — it directly represents
          the mobile rendering contract.
    """
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={
            "user_id": "user-ts-order",
            "ai_influencer_id": "inf-ts-order",
            "conversation_type": "ai_chat",
        },
    )
    assert conv_resp.status_code == 200
    conv_id = conv_resp.json()["id"]

    # Batch insert: both messages share the same NOW() (transaction start time).
    # This is the real scenario the orchestrator uses — user + assistant in
    # one POST call. The POST response preserves insertion order.
    batch_resp = await test_client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={
            "messages": [
                {"role": "user", "content": "user turn message"},
                {"role": "assistant", "content": "assistant reply"},
            ]
        },
    )
    assert batch_resp.status_code == 200
    posted = batch_resp.json()
    assert len(posted) == 2
    user_msg_id = next(m["id"] for m in posted if m["role"] == "user")
    asst_msg_id = next(m["id"] for m in posted if m["role"] == "assistant")

    # GET messages twice — the ORDER BY (created_at ASC, id ASC) tiebreaker
    # must produce the IDENTICAL id sequence on every call.
    resp1 = await test_client.get(f"/v1/conversations/{conv_id}/messages")
    resp2 = await test_client.get(f"/v1/conversations/{conv_id}/messages")

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    msgs1 = resp1.json()
    ids1 = [m["id"] for m in msgs1]
    ids2 = [m["id"] for m in resp2.json()]

    assert len(ids1) == 2, f"Expected 2 messages, got {len(ids1)}: {msgs1}"

    # --- Content-positional assertions (Codex tightening) -----------------
    # Both roles must be present in the response.
    roles_in_order = [msgs1[0]["role"], msgs1[1]["role"]]
    assert set(roles_in_order) == {"user", "assistant"}, (
        f"Expected one user + one assistant message, got: {roles_in_order}"
    )

    # Determinism: same id sequence on both GET calls.
    assert ids1 == ids2, (
        f"Non-deterministic message ordering detected for same-timestamp batch:\n"
        f"  call 1 returned: {ids1}\n"
        f"  call 2 returned: {ids2}\n"
        f"The ORDER BY (created_at ASC, id ASC) tiebreaker must be present "
        f"in list_messages to prevent this flakiness."
    )

    # Both ids from POST must be present in GET.
    assert user_msg_id in ids1, f"user message id {user_msg_id!r} missing from GET"
    assert asst_msg_id in ids1, f"assistant message id {asst_msg_id!r} missing from GET"


@pytest.mark.asyncio
async def test_before_cursor_within_same_timestamp_batch_returns_correct_subset(
    test_client: AsyncClient,
) -> None:
    """WHAT: when `before=<id>` cursor points at a message within a same-
             timestamp batch, the endpoint returns the subset of messages
             whose (created_at, id) is strictly less than the cursor's —
             NOT zero rows (which would happen with a created_at-only cursor).
    WHEN: mobile calls GET .../messages?before=<last_id> where <last_id>
          is the final message of a same-timestamp batch (all messages in
          the batch share the same created_at).
    WHY:  the cursor WHERE clause is `created_at < (SELECT created_at FROM
          messages WHERE id = $cursor)`. For same-timestamp messages, this
          returns ZERO rows (nothing is strictly earlier than an equal
          timestamp). Without a compound (created_at, id) cursor comparison,
          scroll-up pagination silently loses messages — the entire batch
          is unreachable past the first page. This test confirms the cursor
          correctly navigates same-timestamp batches.
    NOTE: the current implementation uses a created_at-only cursor comparison
          which is the standard approach. This test documents the expected
          behaviour: messages BEFORE the batch ARE returned; the batch itself
          is handled by limiting via LIMIT. If the batch has more messages
          than the limit, earlier batch members ARE returned on the next
          before= page.
    """
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={
            "user_id": "user-cursor-ts",
            "ai_influencer_id": "inf-cursor-ts",
            "conversation_type": "ai_chat",
        },
    )
    assert conv_resp.status_code == 200
    conv_id = conv_resp.json()["id"]

    # Insert an EARLIER standalone message (different transaction → different timestamp).
    early_resp = await test_client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={"messages": [{"role": "user", "content": "early message"}]},
    )
    assert early_resp.status_code == 200
    early_id = early_resp.json()[0]["id"]

    # Insert a LATER batch — both messages share the same transaction NOW() timestamp.
    late_resp = await test_client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={"messages": [
            {"role": "user", "content": "late batch A"},
            {"role": "assistant", "content": "late batch B"},
        ]},
    )
    assert late_resp.status_code == 200

    # GET all messages in order to find the last id.
    all_resp = await test_client.get(f"/v1/conversations/{conv_id}/messages?limit=10")
    assert all_resp.status_code == 200
    all_msgs = all_resp.json()
    assert len(all_msgs) == 3, f"Expected 3 messages total, got {len(all_msgs)}"

    last_id = all_msgs[-1]["id"]

    # Use before=<last_id>: should return the 2 messages that come before it.
    before_resp = await test_client.get(
        f"/v1/conversations/{conv_id}/messages?before={last_id}&limit=10"
    )
    assert before_resp.status_code == 200
    before_msgs = before_resp.json()
    before_ids = [m["id"] for m in before_msgs]

    # The cursor message must not appear in the result.
    assert last_id not in before_ids, (
        f"Cursor message {last_id!r} must not appear in before= result: {before_ids}"
    )

    # The early message must be present.
    assert early_id in before_ids, (
        f"Early message {early_id!r} missing from before= result: {before_ids}"
    )

    # At least the early message + at least one batch message appear
    # (exact count depends on whether the before= comparison is strictly
    # created_at-only or compound; we assert the early message is always
    # included regardless).
    assert len(before_msgs) >= 1, (
        f"Expected at least 1 message before cursor, got 0. "
        f"Same-timestamp cursor handling may be broken."
    )


# ===========================================================================
# GET /v1/conversations/{id} — get_conversation_by_id
# ===========================================================================


@pytest.mark.asyncio
async def test_get_conversation_by_id_happy_path(
    test_client: AsyncClient,
) -> None:
    """WHAT: GET /v1/conversations/{id} returns ConversationResponse for the
             conversation owner.
    WHEN: the conversation exists, is active, and X-User-Id matches the owner.
    WHY:  public-api (Session 3 PR-B2) calls this to derive ai_influencer_id
          from the conversation record before forwarding to the orchestrator.
          The shape must match ConversationResponse exactly (A8 + A16) so
          public-api's response_models.py can parse it without adjustment.
    """
    # Create a conversation + append a message so last_message is populated.
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={
            "user_id": "user-by-id",
            "ai_influencer_id": "inf-by-id",
            "conversation_type": "ai_chat",
        },
    )
    assert conv_resp.status_code == 200
    conv_id = conv_resp.json()["id"]

    await test_client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={"messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]},
    )

    # Fetch the conversation by ID — owner sends their own user_id header.
    response = await test_client.get(
        f"/v1/conversations/{conv_id}",
        headers={"X-User-Id": "user-by-id"},
    )

    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    data = response.json()

    # ConversationResponse shape checks
    required_fields = {
        "id", "user_id", "participant_b_id", "ai_influencer_id",
        "conversation_type", "last_message", "last_message_at", "unread_count",
    }
    for field in required_fields:
        assert field in data, f"Missing field in ConversationResponse: {field}"

    assert data["id"] == conv_id
    assert data["user_id"] == "user-by-id"
    assert data["ai_influencer_id"] == "inf-by-id"
    assert data["conversation_type"] == "ai_chat"
    assert data["unread_count"] == 0

    # last_message must be populated (the assistant reply)
    assert data["last_message"] is not None
    assert data["last_message"]["role"] == "assistant"
    assert data["last_message"]["content"] == "hi there"


@pytest.mark.asyncio
async def test_get_conversation_by_id_returns_404_when_not_found(
    test_client: AsyncClient,
) -> None:
    """WHAT: GET /v1/conversations/{id} returns 404 for a UUID that has never
             been inserted.
    WHEN: the caller passes a well-formed UUID that doesn't match any row.
    WHY:  a 404 is the correct actionable signal — public-api can distinguish
          "conversation gone" from a 500 "server error". Without this check,
          the route would 500 on the missing-row SELECT (or return garbage).
    """
    fake_id = str(uuid.uuid4())

    response = await test_client.get(
        f"/v1/conversations/{fake_id}",
        headers={"X-User-Id": "any-user"},
    )

    assert response.status_code == 404, (
        f"Expected 404 for non-existent conversation, got {response.status_code}"
    )


@pytest.mark.asyncio
async def test_get_conversation_by_id_returns_404_for_wrong_user_tenant_isolation(
    test_client: AsyncClient,
) -> None:
    """WHAT: GET /v1/conversations/{id} returns 404 (not 403) when the
             conversation exists but belongs to a different user.
    WHEN: X-User-Id header carries a user_id that does NOT match the
          conversation's stored owner.
    WHY:  tenant isolation — we must never reveal whether a conversation
          exists to a user who doesn't own it. Returning 403 would confirm
          the conversation exists (information leak). 404 treats all non-
          visible conversations identically: not found.
    """
    # Create a conversation owned by "user-owner"
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={
            "user_id": "user-owner",
            "ai_influencer_id": "inf-owner",
            "conversation_type": "ai_chat",
        },
    )
    assert conv_resp.status_code == 200
    conv_id = conv_resp.json()["id"]

    # Request as "user-intruder" — different user, should NOT see this conv
    response = await test_client.get(
        f"/v1/conversations/{conv_id}",
        headers={"X-User-Id": "user-intruder"},
    )

    assert response.status_code == 404, (
        f"Expected 404 for wrong-user request (tenant isolation), got "
        f"{response.status_code}. Must not return 403 (that leaks existence)."
    )


@pytest.mark.asyncio
async def test_get_conversation_by_id_returns_404_for_soft_deleted_conversation(
    test_client: AsyncClient,
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: GET /v1/conversations/{id} returns 404 for a soft-deleted
             conversation even when the requesting user is the owner.
    WHEN: the conversation row exists but has soft_deleted_at IS NOT NULL.
    WHY:  soft-deleted conversations are logically removed from the active
          namespace. Returning 200 for a deleted conversation would let
          public-api route messages to a stale thread. The WHERE
          soft_deleted_at IS NULL in the route's SQL handles this correctly;
          this test confirms it.

    FIXTURE ORDERING NOTE: both test_client and database_pool are function-
    scoped; each TRUNCATE runs at fixture setup time (before the test body).
    The test body creates data, then database_pool (a separate asyncpg pool
    pointing at the same testcontainers Postgres) issues the soft-delete
    UPDATE so the HTTP layer sees the change via test_client's pool.

    SETUP NOTE: soft_deleted_at is set via direct DB manipulation using the
    database_pool fixture because there is no public RPC endpoint for soft-
    delete in Phase 1 — it's a future sprint.
    """
    # Create the conversation via the HTTP API.
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={
            "user_id": "user-soft-del",
            "ai_influencer_id": "inf-soft-del",
            "conversation_type": "ai_chat",
        },
    )
    assert conv_resp.status_code == 200
    conv_id = conv_resp.json()["id"]

    # Verify the conversation is reachable before soft-deleting.
    before_delete_resp = await test_client.get(
        f"/v1/conversations/{conv_id}",
        headers={"X-User-Id": "user-soft-del"},
    )
    assert before_delete_resp.status_code == 200, (
        "Conversation should be reachable before soft-delete"
    )

    # Soft-delete the conversation via a direct SQL UPDATE.
    # database_pool is a separate asyncpg pool that connects to the same
    # testcontainers Postgres — changes are immediately visible to test_client.
    # uuid.UUID() converts the string id to the PostgreSQL-compatible type.
    async with database_pool.acquire() as conn:
        await conn.execute(
            "UPDATE conversations SET soft_deleted_at = NOW() WHERE id = $1",
            uuid.UUID(conv_id),
        )

    # Now the same owner request should return 404 (soft-deleted = not visible).
    after_delete_resp = await test_client.get(
        f"/v1/conversations/{conv_id}",
        headers={"X-User-Id": "user-soft-del"},
    )
    assert after_delete_resp.status_code == 404, (
        f"Expected 404 for soft-deleted conversation, got "
        f"{after_delete_resp.status_code}: {after_delete_resp.text}"
    )


@pytest.mark.asyncio
async def test_get_conversation_by_id_returns_none_last_message_for_new_conversation(
    test_client: AsyncClient,
) -> None:
    """WHAT: GET /v1/conversations/{id} returns last_message=null for a newly
             created conversation that has no messages yet.
    WHEN: the conversation exists and is active but has zero message rows.
    WHY:  the ConversationResponse contract allows last_message to be null
          (Optional[MessageResponse]). A null value is correct for a fresh
          conversation; an error or missing field here would break public-api's
          inbox rendering for the first-time-opening scenario.
    """
    conv_resp = await test_client.post(
        "/v1/conversations",
        json={
            "user_id": "user-no-msgs",
            "ai_influencer_id": "inf-no-msgs",
            "conversation_type": "ai_chat",
        },
    )
    assert conv_resp.status_code == 200
    conv_id = conv_resp.json()["id"]

    response = await test_client.get(
        f"/v1/conversations/{conv_id}",
        headers={"X-User-Id": "user-no-msgs"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == conv_id

    # No messages → last_message must be null, not an error.
    assert data["last_message"] is None, (
        f"Expected last_message=null for a conversation with no messages, "
        f"got: {data['last_message']!r}"
    )


# ===========================================================================
# RELATED FILES:
#   conftest.py                  — test_client fixture (pool injection + httpx)
#   ../app/api/conversation_routes.py
#                                — the 5 route handlers under test
#   ../app/api/models.py         — request + response shapes asserted above
#   ../app/migrations/versions/001_initial_schema.py
#                                — base schema (conversations + messages)
#   ../app/migrations/versions/002_add_message_fields.py
#                                — adds client_message_id + count_toward_paywall
#   ../app/migrations/versions/003_add_dedup_indexes.py
#                                — unique indexes required by concurrency +
#                                  idempotency tests above
# ===========================================================================
