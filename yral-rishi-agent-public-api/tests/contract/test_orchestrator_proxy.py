# ---------------------------------------------------------------------------
# test_orchestrator_proxy.py — Day-4C contract tests for the
# public-api → orchestrator proxy + F10 idempotency dedup.
#
# ⭐ START HERE: 7 J1-HOT tests covering the message-send forwarding +
# error-mapping + idempotency-dedup contract. The orchestrator is
# mocked (per directive: "Mock orchestrator in tests; do NOT take a
# hard dep on Session 4's running service for CI").
#
# THE 7 SCENARIOS:
#   1. happy turn: orchestrator 200 → public-api 200, envelope wrap, headers forwarded
#   2. idempotency hit: same X-Idempotency-Key twice → orchestrator called once
#   3. idempotency miss without client key: server mints key + logs source
#   4. orchestrator 503 → public-api 503 envelope + Sentry tag
#   5. orchestrator 422 → public-api 422 envelope
#   6. orchestrator timeout (ConnectError) → public-api 504 envelope + Sentry tag
#   7. different user_id + same idempotency key → 2 orchestrator calls (no cross-user collision)
#
# WHY MOCK orchestrator_client.run_turn DIRECTLY (vs httpx-level mock)?
# `run_turn` is the boundary the chat handler crosses. Mocking it
# bypasses the httpx layer entirely + lets each test stub the
# orchestrator's exact response shape (status + body). Mocking at httpx
# level would require building Request objects, choosing transport
# adapters, etc. — over-engineering per A2.1 for the test goal.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app import orchestrator_client, user_memory_client
from app.api.feature_flag import require_day_2_placeholder_flag_enabled
from app.main import app


# Path the chat handler proxies to.
SEND_MESSAGE_PATH = "/api/v1/chat/conversations/conv-id-day-4c/messages"


# A canonical MessageDto JSON the mocked orchestrator returns on the
# happy path. Shape matches interface-contracts/00-api-contract.md.
_HAPPY_MESSAGE_DTO = {
    "id": "msg-id-orchestrator-1",
    "conversation_id": "conv-id-day-4c",
    "role": "assistant",
    "content": "Hello from the (mocked) orchestrator",
    "media_urls": None,
    "client_message_id": "client-msg-1",
    "created_at": "2026-05-18T10:00:00+00:00",
    "count_toward_paywall": True,
}


# A canonical ConversationResponse the mocked user-memory-service
# returns. `ai_influencer_id` is the trust-derived value PR-B2
# forwards to the orchestrator; the unique sentinel string lets the
# trust-boundary test assert the orchestrator received THIS value
# rather than any client-controlled value.
_TRUSTED_INFLUENCER_ID = "trusted-influencer-id-from-conversation-row"
_HAPPY_CONVERSATION = {
    "id": "conv-id-day-4c",
    "user_id": "conftest-test-user-id",
    "conversation_type": "user_to_ai",
    "ai_influencer_id": _TRUSTED_INFLUENCER_ID,
    "participant_b_id": None,
    "created_at": "2026-05-23T10:00:00+00:00",
    "last_message_at": "2026-05-23T10:00:00+00:00",
    "message_count": 0,
    "last_message": None,
    "soft_deleted_at": None,
}


def _make_mock_response(status_code: int, json_body: dict) -> httpx.Response:
    """Build a synthetic httpx.Response for the mocked orchestrator.

    WHAT: returns an httpx.Response with the given status + JSON body.
    WHEN: called by per-test orchestrator_client.run_turn mocks.
    WHY:  httpx.Response is what the real run_turn returns; building
          one preserves the chat handler's actual code path.
    """
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(json_body).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


@pytest.fixture(autouse=True)
def mock_user_memory_happy(monkeypatch):
    """Autouse: patch user_memory_client.get_conversation → 200 with
    the happy ConversationResponse.

    WHAT: replaces user_memory_client.get_conversation with an
          AsyncMock that returns a 200 response carrying
          _HAPPY_CONVERSATION (whose ai_influencer_id is the
          _TRUSTED_INFLUENCER_ID sentinel). Returns the AsyncMock so
          tests can override + assert call_args / count.
    WHEN: autouse on every test in this file (send_message now does a
          user-memory lookup BEFORE the orchestrator call — without
          this mock every test would attempt a real HTTP call against
          an unreachable user-memory service).
    WHY:  PR-B2 trust boundary: public-api derives the per-request
          `influencer_id` from this lookup. The autouse fixture is
          the test-side equivalent of the production user-memory dep
          being reachable. Tests that want to exercise user-memory
          failure paths override this fixture with a 404 / 503 /
          timeout / bad-shape mock per-test.
    """
    happy_response = _make_mock_response(200, _HAPPY_CONVERSATION)
    mock = AsyncMock(return_value=happy_response)
    monkeypatch.setattr(user_memory_client, "get_conversation", mock)
    return mock


@pytest.fixture
def mock_orchestrator_happy(monkeypatch):
    """Patch run_turn to return a happy 200 MessageDto on every call.

    WHAT: replaces orchestrator_client.run_turn with an AsyncMock that
          returns _HAPPY_MESSAGE_DTO inside a 200 response. Returns the
          AsyncMock so tests can assert call count + call args.
    WHEN: used by the happy-path + idempotency-hit tests.
    WHY:  isolates the test from Session 4's running service while
          still exercising the real chat handler code path.
    """
    happy_response = _make_mock_response(200, _HAPPY_MESSAGE_DTO)
    mock = AsyncMock(return_value=happy_response)
    monkeypatch.setattr(orchestrator_client, "run_turn", mock)
    return mock


@pytest.fixture
def mock_orchestrator_503(monkeypatch):
    """Patch run_turn to always return 503."""
    mock = AsyncMock(
        return_value=_make_mock_response(
            503, {"detail": "orchestrator stub-blocked"}
        )
    )
    monkeypatch.setattr(orchestrator_client, "run_turn", mock)
    return mock


@pytest.fixture
def mock_orchestrator_422(monkeypatch):
    """Patch run_turn to always return 422."""
    mock = AsyncMock(
        return_value=_make_mock_response(
            422, {"detail": "validation failed at orchestrator"}
        )
    )
    monkeypatch.setattr(orchestrator_client, "run_turn", mock)
    return mock


@pytest.fixture
def mock_orchestrator_timeout(monkeypatch):
    """Patch run_turn to always raise httpx.ConnectError."""
    mock = AsyncMock(side_effect=httpx.ConnectError("simulated connect error"))
    monkeypatch.setattr(orchestrator_client, "run_turn", mock)
    return mock


# ===========================================================================
# Scenario 1 — happy turn
# ===========================================================================


def test_send_message_happy_turn_returns_envelope_200(client, mock_orchestrator_happy):
    """Orchestrator returns MessageDto → public-api wraps in ApiResponse + 200."""
    response = client.post(
        SEND_MESSAGE_PATH,
        json={"content": "hi", "client_message_id": "client-msg-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["id"] == "msg-id-orchestrator-1"
    assert body["data"]["role"] == "assistant"
    # Orchestrator called exactly once.
    assert mock_orchestrator_happy.await_count == 1


def test_send_message_forwards_required_headers(client, mock_orchestrator_happy):
    """Run_turn called with user_id + request_id + idempotency_key kwargs."""
    client.post(
        SEND_MESSAGE_PATH,
        json={"content": "test", "client_message_id": "msg-key-test"},
        headers={"X-Idempotency-Key": "my-client-key-1"},
    )
    # Inspect the kwargs passed to the mocked run_turn.
    call_kwargs = mock_orchestrator_happy.await_args.kwargs
    # user_id flows from AuthenticatedUser.user_id (Day 4B).
    assert "user_id" in call_kwargs
    # The conversation_id from the URL path.
    assert call_kwargs["conversation_id"] == "conv-id-day-4c"
    # The client's idempotency key is passed through, not minted.
    assert call_kwargs["idempotency_key"] == "my-client-key-1"
    # request_id flows from the request middleware.
    assert "request_id" in call_kwargs


# ===========================================================================
# Scenario 2 — idempotency hit
# ===========================================================================


def test_idempotency_hit_second_call_does_not_reach_orchestrator(
    client, mock_orchestrator_happy,
):
    """Same X-Idempotency-Key twice → orchestrator called ONCE."""
    headers = {"X-Idempotency-Key": "idempotent-key-1"}
    r1 = client.post(
        SEND_MESSAGE_PATH,
        json={"content": "first call"},
        headers=headers,
    )
    r2 = client.post(
        SEND_MESSAGE_PATH,
        json={"content": "second call — should be cached"},
        headers=headers,
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Second response was served from cache — orchestrator called once total.
    assert mock_orchestrator_happy.await_count == 1
    # Both responses are byte-for-byte the same (replay fidelity per F10).
    assert r1.content == r2.content


# ===========================================================================
# Scenario 3 — idempotency miss without client key
# ===========================================================================


def test_idempotency_miss_without_client_key_server_mints_one(
    client, mock_orchestrator_happy,
):
    """Two calls WITHOUT X-Idempotency-Key header → orchestrator called twice
    (each call mints a different server key + misses cache)."""
    r1 = client.post(SEND_MESSAGE_PATH, json={"content": "first"})
    r2 = client.post(SEND_MESSAGE_PATH, json={"content": "second"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Each call gets its own server-minted key → both miss the cache →
    # orchestrator called twice.
    assert mock_orchestrator_happy.await_count == 2


# ===========================================================================
# Scenario 4 — orchestrator 503 → public-api 503 envelope
# ===========================================================================


def test_orchestrator_503_maps_to_public_api_503_envelope(client, mock_orchestrator_503):
    """Upstream 503 → downstream 503 with envelope-shaped error body."""
    response = client.post(SEND_MESSAGE_PATH, json={"content": "test"})
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "service_unavailable"
    # I6 push-back: directive specified `orchestrator_unavailable` but
    # the contract forbids new codes; using `service_unavailable` per
    # the contract's locked error-codes list. msg field carries the
    # human-readable distinction. Sentry tag carries the structured
    # signal (tested separately in a Sentry-integration suite).


# ===========================================================================
# Scenario 5 — orchestrator 422 → public-api 422 envelope
# ===========================================================================


def test_orchestrator_422_maps_to_public_api_422_envelope(client, mock_orchestrator_422):
    """Upstream 422 → downstream 422 with envelope-shaped error body."""
    response = client.post(SEND_MESSAGE_PATH, json={"content": "test"})
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "validation_failed"


# ===========================================================================
# Scenario 6 — orchestrator timeout → public-api 504 envelope
# ===========================================================================


def test_orchestrator_timeout_maps_to_public_api_504_envelope(
    client, mock_orchestrator_timeout,
):
    """httpx.ConnectError → public-api 504 with envelope error body."""
    response = client.post(SEND_MESSAGE_PATH, json={"content": "test"})
    assert response.status_code == 504
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "service_unavailable"
    # I6 push-back same as 503: directive specified `orchestrator_timeout`
    # but contract forbids new codes; using `service_unavailable`. Sentry
    # tag `orchestrator.call.failed=timeout` carries the structured
    # signal for the divergence dashboard.


# ===========================================================================
# Scenario 7 — same key, different users → no cross-user cache collision
# ===========================================================================


def test_same_idempotency_key_different_users_no_collision(
    client, mock_orchestrator_happy, monkeypatch, rsa_keypair,
):
    """Two users sending the same client-key → 2 orchestrator calls
    (no cache leak between users)."""
    # First call uses the conftest's default auth (user_id =
    # `conftest-test-user-id`).
    r1 = client.post(
        SEND_MESSAGE_PATH,
        json={"content": "user A first"},
        headers={"X-Idempotency-Key": "shared-client-key"},
    )
    assert r1.status_code == 200

    # Second call uses a DIFFERENT user. Mint a token with a different
    # sub claim + override the default header just for this request.
    from datetime import datetime, timedelta, timezone

    import jwt as jwt_lib

    from tests.contract.conftest import DEFAULT_ISSUER, TEST_KID

    private_pem, _ = rsa_keypair
    now = datetime.now(timezone.utc)
    other_user_token = jwt_lib.encode(
        {
            "iss": DEFAULT_ISSUER,
            "sub": "user-B-different-id",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=3600)).timestamp()),
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": TEST_KID},
    )

    r2 = client.post(
        SEND_MESSAGE_PATH,
        json={"content": "user B first"},
        headers={
            "X-Idempotency-Key": "shared-client-key",
            "Authorization": f"Bearer {other_user_token}",
        },
    )
    assert r2.status_code == 200

    # Both users called orchestrator — same key but different user_ids
    # → the cache keys differ (scoped by user_id per Day-4C directive)
    # → no cross-user replay.
    assert mock_orchestrator_happy.await_count == 2


# ===========================================================================
# PR-B2 tests — per-request influencer_id forwarding + trust boundary
# ===========================================================================
# These tests assert the load-bearing security property: public-api
# MUST derive `influencer_id` from the user-memory-service
# conversation lookup, NEVER from any client-controlled surface
# (request body / query string / header). The trust-boundary test
# forges all three surfaces simultaneously + asserts the orchestrator
# AsyncMock received the conversation-derived value, NOT any forged
# client value.
# ===========================================================================


def test_send_message_forwards_trusted_influencer_id_from_conversation(
    client, mock_orchestrator_happy,
):
    """WHAT: assert run_turn is called with influencer_id derived from
            the user-memory conversation lookup, NOT from any client-
            controlled surface.
    WHEN: every happy-path send_message turn (the autouse user-memory
          mock returns the canonical _HAPPY_CONVERSATION whose
          ai_influencer_id is the _TRUSTED_INFLUENCER_ID sentinel).
    WHY:  PR-B2 trust boundary is the gate for PR-B3 (orchestrator
          drops env fallback + requires influencer_id). This test
          proves the derivation path on a vanilla request.
    """
    response = client.post(SEND_MESSAGE_PATH, json={"content": "hi"})
    assert response.status_code == 200
    call_kwargs = mock_orchestrator_happy.await_args.kwargs
    assert call_kwargs["influencer_id"] == _TRUSTED_INFLUENCER_ID


def test_send_message_ignores_forged_influencer_id_on_every_surface(
    client, mock_orchestrator_happy,
):
    """WHAT: forge `influencer_id` simultaneously in (a) request body,
            (b) query string, AND (c) X-Influencer-Id header; assert
            orchestrator AsyncMock receives the conversation-derived
            _TRUSTED_INFLUENCER_ID, NOT the forged "client-forged-id".
    WHEN: a hostile mobile client tries to bypass the trust boundary
          by attaching `influencer_id` to any input surface.
    WHY:  THIS is the load-bearing security contract. PR-B3 will
          remove the orchestrator's env-var fallback, making
          influencer_id required — at that point a forged client
          value reaching the orchestrator would let an attacker
          chat with one influencer while billing/quota-ing against
          another's. The 3-surface forge proves no path exists from
          client → orchestrator's influencer_id field; the
          assertion will FAIL if any future refactor accidentally
          plumbs a client-supplied value through.
    """
    forged_id = "client-forged-id-attacker-controlled"
    response = client.post(
        SEND_MESSAGE_PATH + f"?influencer_id={forged_id}",
        json={
            "content": "hi",
            # Extra field — Pydantic's default extra="ignore" drops
            # it silently; SendMessageRequest never sees it. The
            # forge is here for defense-in-depth — if a future PR
            # ever flips Pydantic to extra="allow" this assertion
            # still holds because the handler never reads
            # `request.json()["influencer_id"]` directly.
            "influencer_id": forged_id,
        },
        headers={"X-Influencer-Id": forged_id},
    )
    assert response.status_code == 200

    # The load-bearing assertion: orchestrator received the
    # conversation-derived value, not the forged client value on any
    # of the 3 surfaces.
    call_kwargs = mock_orchestrator_happy.await_args.kwargs
    assert call_kwargs["influencer_id"] == _TRUSTED_INFLUENCER_ID, (
        f"TRUST BOUNDARY VIOLATION: orchestrator received "
        f"influencer_id={call_kwargs.get('influencer_id')!r} but expected "
        f"the conversation-derived {_TRUSTED_INFLUENCER_ID!r}. "
        f"A client-controlled surface (body / query / header) is leaking "
        f"into the orchestrator call — PR-B3 (orchestrator drops env "
        f"fallback) would be unsafe to ship in this state."
    )
    # Belt-and-suspenders: assert the orchestrator did NOT receive
    # the forged value on any keyword argument.
    for argument_name, argument_value in call_kwargs.items():
        assert argument_value != forged_id, (
            f"TRUST BOUNDARY VIOLATION: forged value {forged_id!r} reached "
            f"orchestrator argument {argument_name!r}."
        )


def test_send_message_passes_post_pr_131_body_shape_to_orchestrator(
    client, mock_orchestrator_happy,
):
    """WHAT: assert run_turn is called with the post-PR-#131 keyword
            arguments — `user_message` (renamed from message_content),
            `influencer_id` (new), and NO `user_id` in body (orchestrator
            reads X-User-Id header per Codex round-4 BLOCKER 2).
    WHEN: every send_message turn against the post-PR-#131 contract.
    WHY:  pre-PR-#131 the public-api side used `message_content` +
          included `user_id` in body. Post-PR-#131 the orchestrator's
          RunTurnRequest expects `user_message` + no user_id field.
          This test guards the public-api ↔ orchestrator wire shape
          alignment; a regression that drops the rename surfaces a
          422 from the orchestrator otherwise (silent in tests
          because they mock run_turn).
    """
    client.post(SEND_MESSAGE_PATH, json={"content": "hello"})
    call_kwargs = mock_orchestrator_happy.await_args.kwargs
    # New post-PR-#131 keyword arguments expected.
    assert call_kwargs["user_message"] == "hello"
    assert "influencer_id" in call_kwargs
    assert call_kwargs["conversation_id"] == "conv-id-day-4c"
    # X-User-Id forwarded as a keyword argument to run_turn (which
    # places it in the header dict, not the body — verified inside
    # orchestrator_client.run_turn).
    assert "user_id" in call_kwargs
    # Pre-PR-#131 names that should NOT appear (regression guard).
    assert "message_content" not in call_kwargs


def test_send_message_returns_404_envelope_when_user_memory_404(
    client, mock_orchestrator_happy, monkeypatch,
):
    """WHAT: when user-memory returns 404 (conversation not found,
            soft-deleted, OR belongs to a different user — Session 5's
            by-id endpoint doesn't differentiate), public-api returns
            envelope-shaped 404 with locked `not_found` error code
            AND skips the orchestrator call entirely.
    WHEN: client requests send_message for a conversation that
          doesn't exist OR belongs to a different user (tenant
          isolation).
    WHY:  the orchestrator call MUST NOT fire when the trust-boundary
          check fails. Skipping it (a) saves the LLM-token cost,
          (b) prevents any forged influencer_id from being forwarded
          via the env-fallback path on the orchestrator side. The
          assertion includes `mock_orchestrator_happy.await_count == 0`
          to prove the short-circuit.
    """
    not_found_response = _make_mock_response(404, {"detail": "conversation not found"})
    monkeypatch.setattr(
        user_memory_client,
        "get_conversation",
        AsyncMock(return_value=not_found_response),
    )
    response = client.post(SEND_MESSAGE_PATH, json={"content": "hi"})
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    # Load-bearing short-circuit assertion: orchestrator NEVER called
    # when the trust-boundary check fails.
    assert mock_orchestrator_happy.await_count == 0


def test_send_message_returns_503_envelope_when_user_memory_unreachable(
    client, mock_orchestrator_happy, monkeypatch,
):
    """WHAT: when user-memory raises httpx.ConnectError, public-api
            returns envelope-shaped 503 with the locked
            `service_unavailable` error code; orchestrator never
            called.
    WHEN: user-memory-service container down / DNS miss / rolling-
          update window on the user-memory side.
    WHY:  cannot derive trusted influencer_id without the lookup;
          failing closed (503 instead of attempting orchestrator
          with None influencer_id) prevents any env-fallback-driven
          response leaking to the user when the trust root is gone.
    """
    monkeypatch.setattr(
        user_memory_client,
        "get_conversation",
        AsyncMock(side_effect=httpx.ConnectError("user-memory unreachable")),
    )
    response = client.post(SEND_MESSAGE_PATH, json={"content": "hi"})
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"
    assert mock_orchestrator_happy.await_count == 0


def test_send_message_returns_503_envelope_when_user_memory_5xx(
    client, mock_orchestrator_happy, monkeypatch,
):
    """WHAT: when user-memory returns non-200 non-404 (e.g., 500),
            public-api returns envelope-shaped 503 + skips orchestrator.
    WHEN: user-memory-side internal error (DB query crash, unhandled
          exception in their handler).
    WHY:  same failure-closed principle as the connect-error path —
          any upstream issue with the trust root collapses to 503,
          never silently proceeds with None influencer_id.
    """
    monkeypatch.setattr(
        user_memory_client,
        "get_conversation",
        AsyncMock(return_value=_make_mock_response(500, {"error": "boom"})),
    )
    response = client.post(SEND_MESSAGE_PATH, json={"content": "hi"})
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"
    assert mock_orchestrator_happy.await_count == 0


def test_send_message_returns_503_envelope_when_user_memory_bad_shape(
    client, mock_orchestrator_happy, monkeypatch,
):
    """WHAT: when user-memory returns 200 but the body isn't a dict
            (schema drift), public-api returns envelope-shaped 503 +
            skips orchestrator.
    WHEN: user-memory-side schema drift (Session 5 changes the
          ConversationResponse shape without a coordinated contract
          update).
    WHY:  defense-in-depth on the contract boundary; bad shape →
          can't extract ai_influencer_id reliably → fail closed.
    """
    monkeypatch.setattr(
        user_memory_client,
        "get_conversation",
        # Return a list instead of a dict — schema drift simulation.
        AsyncMock(return_value=_make_mock_response(200, ["not", "a", "dict"])),
    )
    response = client.post(SEND_MESSAGE_PATH, json={"content": "hi"})
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"
    assert mock_orchestrator_happy.await_count == 0


def test_send_message_returns_503_when_ai_influencer_id_wrong_type(
    client, mock_orchestrator_happy, monkeypatch,
):
    """WHAT: when user-memory returns a ConversationResponse whose
            ai_influencer_id is neither str nor None (e.g., a list,
            dict, or int — schema drift on Session 5's side),
            public-api returns envelope-shaped 503 + skips the
            orchestrator call entirely.
    WHEN: Session 5 changes the ai_influencer_id column type
          unexpectedly (text → json / array / numeric).
    WHY:  defense-in-depth on the contract boundary. Without this
          check the malformed value would either (a) reach
          orchestrator's RunTurnRequest validation and surface as a
          422 (silent in tests because run_turn is mocked; mobile-
          confusing in cluster), or (b) — worse — land in a Sentry
          context line tagged with the user_id (potential H6 PII
          shape leak if the malformed value happens to carry nested
          user data). Failing closed at the public-api boundary
          keeps the trust check clean + surfaces the drift via the
          same envelope shape as other user-memory failure modes.
    """
    drifted_conv = dict(_HAPPY_CONVERSATION)
    # Simulate a schema drift where ai_influencer_id is now a list
    # (e.g., Session 5 migrated to a multi-influencer association).
    drifted_conv["ai_influencer_id"] = ["unexpected-list-value"]
    monkeypatch.setattr(
        user_memory_client,
        "get_conversation",
        AsyncMock(return_value=_make_mock_response(200, drifted_conv)),
    )
    response = client.post(SEND_MESSAGE_PATH, json={"content": "hi"})
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"
    # Load-bearing short-circuit: orchestrator NEVER fires when the
    # trust-boundary type check fails.
    assert mock_orchestrator_happy.await_count == 0


def test_send_message_returns_503_when_user_memory_id_does_not_match_url_conversation_id(
    client, mock_orchestrator_happy, monkeypatch,
):
    """WHAT: when user-memory returns a 200 response whose `id` field
            does not match the URL-path `conversation_id`, public-api
            returns envelope-shaped 503 + the orchestrator call NEVER
            fires.
    WHEN: user-memory implementation bug — returning the wrong row
          for the requested by-id lookup.
    WHY:  Codex PR #141 round-6 BLOCKER 2 — without this verification
          a wrong-row response would feed a foreign conversation's
          influencer_id into the orchestrator + leak conversation
          existence cross-row. Defense-in-depth on the trust-boundary
          contract.
    """
    wrong_row = dict(_HAPPY_CONVERSATION)
    wrong_row["id"] = "some-OTHER-conversation-id-from-a-different-row"
    monkeypatch.setattr(
        user_memory_client,
        "get_conversation",
        AsyncMock(return_value=_make_mock_response(200, wrong_row)),
    )
    response = client.post(SEND_MESSAGE_PATH, json={"content": "hi"})
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"
    # Load-bearing short-circuit: orchestrator NEVER fires on a wrong-
    # row response from user-memory.
    assert mock_orchestrator_happy.await_count == 0


def test_send_message_returns_503_when_user_memory_user_id_indicates_cross_tenant_leak(
    client, mock_orchestrator_happy, monkeypatch,
):
    """WHAT: when user-memory returns a 200 response whose `user_id`
            field does not match the JWT-authenticated caller's
            `user_id`, public-api returns envelope-shaped 503 + the
            orchestrator call NEVER fires.
    WHEN: cross-tenant leak signal on the user-memory side — a
          conversation row belonging to another user surfaced to
          this caller despite the by-id endpoint's tenant-isolation
          contract.
    WHY:  Codex PR #141 round-6 BLOCKER 2 — defense-in-depth at the
          public-api boundary. A user-memory regression CANNOT reach
          mobile when this check fires; on-call gets paged via
          Sentry `level="fatal"` immediately.
    """
    cross_tenant_row = dict(_HAPPY_CONVERSATION)
    cross_tenant_row["user_id"] = "some-OTHER-user-id-not-the-caller"
    monkeypatch.setattr(
        user_memory_client,
        "get_conversation",
        AsyncMock(return_value=_make_mock_response(200, cross_tenant_row)),
    )
    response = client.post(SEND_MESSAGE_PATH, json={"content": "hi"})
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"
    # Load-bearing short-circuit: orchestrator NEVER fires when a
    # cross-tenant signal is detected.
    assert mock_orchestrator_happy.await_count == 0


def test_send_message_forwards_none_influencer_id_for_legacy_conversation(
    client, mock_orchestrator_happy, monkeypatch,
):
    """WHAT: when the conversation row has no ai_influencer_id field
            (legacy data; the field is optional in the response),
            public-api forwards influencer_id=None to the orchestrator
            (which falls back to the env-var per PR-B1 backwards-compat).
    WHEN: legacy conversation rows that pre-date the ai_influencer_id
          column being populated.
    WHY:  PR-B2 doesn't FORCE non-None influencer_id (that's PR-B3's
          job) — the None pass-through preserves PR-B1's env-fallback
          semantics so legacy rows keep working during the migration
          window.
    """
    legacy_conv = dict(_HAPPY_CONVERSATION)
    legacy_conv.pop("ai_influencer_id")  # field absent
    monkeypatch.setattr(
        user_memory_client,
        "get_conversation",
        AsyncMock(return_value=_make_mock_response(200, legacy_conv)),
    )
    response = client.post(SEND_MESSAGE_PATH, json={"content": "hi"})
    assert response.status_code == 200
    call_kwargs = mock_orchestrator_happy.await_args.kwargs
    assert call_kwargs["influencer_id"] is None


# ===========================================================================
# PR-B2 round-5 — focused body-shape test on orchestrator_client.run_turn()
# ===========================================================================
# Codex PR #141 round-4 CONCERN: the body-shape regression test above
# (test_send_message_passes_post_pr_131_body_shape_to_orchestrator)
# only inspects the mocked run_turn keyword arguments — it doesn't
# verify the actual JSON body that run_turn() itself constructs and
# posts to Session 4. A future regression INSIDE run_turn() (e.g.,
# dropping `user_message` from the body dict, re-adding `user_id` to
# the body) would still 422 in cluster while the kwarg-boundary test
# above passes green.
#
# This test closes that gap by mocking ONE level deeper —
# get_orchestrator_client() returns a stub whose .post() captures
# the outgoing `json=` body + `headers=` argument verbatim, so the
# assertions speak to the actual HTTP shape the orchestrator will
# receive in production.
# ===========================================================================


async def test_run_turn_constructs_post_pr_131_body_shape(monkeypatch):
    """WHAT: directly invoke `orchestrator_client.run_turn(...)` against
            a stubbed `get_orchestrator_client()` that captures the
            outgoing `.post()` call's JSON body + headers; assert the
            body conforms to the post-PR-#131 RunTurnRequest contract
            (user_message present, influencer_id present, NO
            message_content, NO body-level user_id, NO ai_influencer_id
            placeholder) and assert X-User-Id reaches the headers dict.
    WHEN: every CI run — guards against a regression INSIDE
          run_turn()'s body-construction that the mock-the-boundary
          tests above can't catch (they assert on what the handler
          passes TO run_turn, not what run_turn passes to httpx).
    WHY:  Codex PR #141 round-4 CONCERN — without this test a future
          edit to run_turn() that drops user_message or re-adds
          user_id to the body would 422 in cluster while the existing
          test suite passes green (because every existing test mocks
          run_turn itself). Closing the gap end-to-end inside
          public-api is the load-bearing wire-shape guard for the
          cluster smoke.
    """
    # Capture the outgoing post() call arguments verbatim.
    captured: dict = {}

    async def fake_post(path, json=None, headers=None):
        captured["path"] = path
        captured["json"] = json
        captured["headers"] = headers
        return _make_mock_response(200, _HAPPY_MESSAGE_DTO)

    fake_client = AsyncMock()
    fake_client.post = fake_post

    # Stub get_orchestrator_client() so run_turn picks up the capture-
    # client instead of the lifespan-managed singleton. Mocking at this
    # boundary lets the test exercise the REAL run_turn body-construction
    # code path while the post() call is intercepted.
    monkeypatch.setattr(
        orchestrator_client,
        "get_orchestrator_client",
        lambda: fake_client,
    )

    # Invoke run_turn with all required arguments — values are
    # arbitrary but distinct so the assertions can pinpoint the
    # source of each field.
    await orchestrator_client.run_turn(
        user_id="test-user-id-header-only",
        conversation_id="test-conv-id",
        user_message="hello from the body-shape regression test",
        client_message_id=None,
        media_urls=None,
        influencer_id="test-influencer-from-conversation-lookup",
        request_id="test-req-id",
        idempotency_key="test-idempotency-key",
    )

    body = captured["json"]
    headers = captured["headers"]

    # Body MUST contain the post-PR-#131 fields.
    assert "user_message" in body, (
        f"Body missing required `user_message` field; got: {sorted(body.keys())!r}"
    )
    assert body["user_message"] == "hello from the body-shape regression test"
    assert "influencer_id" in body, (
        f"Body missing required `influencer_id` field; got: {sorted(body.keys())!r}"
    )
    assert body["influencer_id"] == "test-influencer-from-conversation-lookup"
    assert "conversation_id" in body
    assert body["conversation_id"] == "test-conv-id"

    # Body MUST NOT contain the pre-PR-#131 fields (these were
    # removed when run_turn synced to the post-PR-#131 contract).
    assert "message_content" not in body, (
        f"Body has stale pre-PR-#131 `message_content` field; "
        f"PR-B2 renamed it to `user_message`. Got: {sorted(body.keys())!r}"
    )
    assert "user_id" not in body, (
        f"Body has `user_id` — post-PR-#131 contract reads user_id from "
        f"X-User-Id header ONLY (Codex orchestrator round-4 BLOCKER 2). "
        f"Got: {sorted(body.keys())!r}"
    )
    assert "ai_influencer_id" not in body, (
        f"Body has stale pre-PR-#131 `ai_influencer_id` field; PR-B2 "
        f"removed the placeholder and renamed to `influencer_id`. Got: "
        f"{sorted(body.keys())!r}"
    )

    # X-User-Id MUST be in the headers — post-PR-#131 the orchestrator
    # reads user_id from this header (Codex round-4 BLOCKER 2 on the
    # orchestrator side).
    assert "X-User-Id" in headers, (
        f"Headers missing X-User-Id; got: {sorted(headers.keys())!r}"
    )
    assert headers["X-User-Id"] == "test-user-id-header-only"

    # The other 4 internal-call headers also forwarded per the contract.
    assert headers["X-Internal-Caller"] == orchestrator_client.INTERNAL_CALLER_NAME
    assert headers["X-Request-Id"] == "test-req-id"
    assert headers["X-Trace-Id"] == "test-req-id"
    assert headers["X-Idempotency-Key"] == "test-idempotency-key"


# ===========================================================================
# RELATED FILES:
#   conftest.py                         — provides `client` (auto-auth) + rsa_keypair
#   ../../app/api/chat_routes.py        — send_message handler under test
#   ../../app/orchestrator_client.py    — run_turn (mocked here)
#   ../../app/user_memory_client.py     — get_conversation (autouse-mocked here)
#   ../../app/api/idempotency.py        — resolve_idempotency_key / cache_lookup /
#                                         cache_store
#   ../../app/config.py                 — orchestrator_* + user_memory_* +
#                                         idempotency_dedup_ttl_seconds
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                                       — F10 (idempotency), J1 (HOT-tier coverage),
#                                         A8 (locked error codes)
# ===========================================================================
