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

from app import orchestrator_client
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
# RELATED FILES:
#   conftest.py                         — provides `client` (auto-auth) + rsa_keypair
#   ../../app/api/chat_routes.py        — send_message handler under test
#   ../../app/orchestrator_client.py    — run_turn (mocked here)
#   ../../app/api/idempotency.py        — resolve_idempotency_key / cache_lookup /
#                                         cache_store
#   ../../app/config.py                 — orchestrator_* + idempotency_dedup_ttl_seconds
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                                       — F10 (idempotency), J1 (HOT-tier coverage),
#                                         A8 (locked error codes)
# ===========================================================================
