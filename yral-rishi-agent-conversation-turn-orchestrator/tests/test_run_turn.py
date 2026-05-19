# ---------------------------------------------------------------------------
# test_run_turn.py — Day-2 coverage for the `POST /v1/turn` RPC handler.
#
# ⭐ START HERE: this file exercises BOTH the happy path (stub returns
# a schema-valid `MessageResponse` when the two gates open) AND the
# error paths (gates closed → 503; malformed body → 422), plus the
# F10 idempotency-replay path (same X-Idempotency-Key + same user
# within 24h returns the cached MessageResponse byte-for-byte).
# Per J1 the orchestrator is HOT-tier — these tests are the floor
# for Day-2's only new route.
#
# WHAT EACH TEST PROVES — at-a-glance index (priority order, happy
# paths first, then error paths, per B7):
#
#   HAPPY PATHS
#     test_run_turn_returns_schema_valid_message_response_when_both_gates_open
#         200 + all 8 MessageResponse fields present + correct types
#     test_run_turn_idempotency_key_header_is_accepted
#         200 when X-Idempotency-Key is set
#     test_run_turn_request_id_header_is_accepted
#         200 when X-Request-Id is set
#     test_run_turn_echoes_conversation_id_into_response
#         response.conversation_id == request.conversation_id
#     test_run_turn_stub_content_matches_documented_placeholder
#         content is the exact literal string the agent def specifies
#     test_run_turn_accepts_optional_media_urls_and_client_message_id
#         A8 multi-modal-parity fields in RunTurnRequest are accepted
#
#   ⭐ F10 IDEMPOTENCY (Codex PR-#96 BLOCKER 1)
#     test_run_turn_same_idempotency_key_replays_cached_response
#         Two POSTs with same key + same user → byte-identical replay
#         (same id, same content, same created_at).
#     test_run_turn_different_users_with_same_key_do_not_collide
#         Two POSTs with same key but DIFFERENT X-User-Id headers →
#         distinct responses (user scoping per directive).
#
#   ERROR PATHS
#     test_run_turn_returns_503_when_flag_unset_default
#         default settings → 503 (flag off everywhere by default)
#     test_run_turn_returns_503_when_environment_is_production
#         even with flag ON, prod gate still refuses
#     test_run_turn_returns_422_when_conversation_id_missing
#         Pydantic validation rejects missing required field
#     test_run_turn_returns_422_when_user_message_is_empty_string
#         min_length=1 on user_message
#
# WHY USE monkeypatch FOR ENV VARS?
# pytest's `monkeypatch.setenv` is scope-limited to the test — env
# changes auto-undo after the test exits, regardless of pass/fail.
# Combined with the auto-use `clean_settings_cache` + `fake_redis`
# fixtures in conftest.py, this means each test starts from
# `enable_run_turn_stub=False, environment="local"` + empty Redis +
# fresh Settings parse.
#
# WHY NO MOCKED DOWNSTREAM (Soul-File, LLM, memory)?
# The Day-2 stub has no downstreams — that's its point. Day-5 PRs add
# the real downstreams + the mocked-downstream test patterns to match.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# `pytest` — the test runner; we use `pytest.MonkeyPatch` as a typed
# parameter annotation for tests that mutate env vars.
import pytest

# `TestClient` — same FastAPI client the conftest's `client` fixture
# yields; importing here is just for the type annotation on test
# parameters (mypy / IDE friendliness, no runtime dependency).
from fastapi.testclient import TestClient


# ===========================================================================
# Shared test-input helper
# ===========================================================================

# A valid request body the happy-path tests use as their baseline. Tests
# that need to assert validation errors override one field at a time so
# the failure surface stays minimal.
VALID_BODY: dict[str, str] = {
    "conversation_id": "test-conversation-uuid-001",
    "user_message": "hello orchestrator",
}


# ===========================================================================
# HAPPY PATHS
# ===========================================================================


def test_run_turn_returns_schema_valid_message_response_when_both_gates_open(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: 200 + every MessageResponse field present with the right type.
    WHEN: env != production AND enable_run_turn_stub=true.
    WHY:  proves the stub is byte-shape-compatible with chat-ai's
          MessageResponse contract so Session 3 can wire its handler.
    """
    # Open both gates explicitly. Default settings keep them closed.
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post("/v1/turn", json=VALID_BODY)

    assert response.status_code == 200, response.text
    body = response.json()

    # Every chat-ai MessageResponse field appears and has the right type.
    # `id` is a UUID-shaped string; we don't assert the exact value
    # (the stub generates a fresh one per call) but we do assert it's
    # a non-empty string.
    assert isinstance(body["id"], str) and len(body["id"]) > 0
    assert body["conversation_id"] == VALID_BODY["conversation_id"]
    assert body["role"] == "assistant"
    assert isinstance(body["content"], str) and len(body["content"]) > 0
    assert body["media_urls"] is None
    assert body["client_message_id"] is None
    assert isinstance(body["created_at"], str)
    assert body["created_at"].endswith("Z")  # ISO8601 UTC trailing Z
    assert body["count_toward_paywall"] is True


def test_run_turn_idempotency_key_header_is_accepted(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: X-Idempotency-Key header is accepted without 4xx.
    WHEN: both gates open + header set.
    WHY:  F10 fixup wires this header into Redis dedup; the bare
          shape-accepted gate stays so a regression in F10 wiring
          doesn't silently break this contract.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post(
        "/v1/turn",
        json=VALID_BODY,
        headers={"X-Idempotency-Key": "test-idempotency-key-001"},
    )

    assert response.status_code == 200, response.text


def test_run_turn_request_id_header_is_accepted(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: X-Request-Id header is accepted without 4xx.
    WHEN: both gates open + header set.
    WHY:  Session 3 sends a correlation ID with every internal RPC
          for Langfuse trace joining; the handler must accept it
          without erroring.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post(
        "/v1/turn",
        json=VALID_BODY,
        headers={"X-Request-Id": "test-request-id-001"},
    )

    assert response.status_code == 200, response.text


def test_run_turn_echoes_conversation_id_into_response(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: response.conversation_id mirrors the request's value.
    WHEN: both gates open + valid body.
    WHY:  Session 3 + downstream callers rely on this echo to
          correlate the reply with the conversation they kicked off.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    distinct_id = "echo-test-conversation-001"
    response = client.post(
        "/v1/turn",
        json={"conversation_id": distinct_id, "user_message": "echo me"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["conversation_id"] == distinct_id


def test_run_turn_stub_content_matches_documented_placeholder(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: response.content is exactly the agent-def-specified string.
    WHEN: both gates open.
    WHY:  the bracketed placeholder is the searchable marker future
          readers + Sentry log greppers use to identify "this turn
          came from the Day-2 stub". Any drift makes that grep fail
          silently and a future PR could ship a stub reply to prod.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post("/v1/turn", json=VALID_BODY)

    assert response.status_code == 200, response.text
    assert response.json()["content"] == (
        "[v2 phase-1 day-2 orchestrator stub — real LLM response from day-5]"
    )


def test_run_turn_accepts_optional_media_urls_and_client_message_id(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: RunTurnRequest accepts the two new optional fields without 422.
    WHEN: body includes media_urls + client_message_id per the updated
          contract (coordinator PR #98 / commit f708a49).
    WHY:  A8 multi-modal parity — public-api forwards media_urls inline
          so the orchestrator doesn't pay a second DB read per turn.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    body_with_extras = {
        "conversation_id": "test-conversation-uuid-002",
        "user_message": "hello with attachments",
        "media_urls": [
            "https://example.invalid/image-001.png",
            "https://example.invalid/image-002.png",
        ],
        "client_message_id": "client-msg-id-001",
    }
    response = client.post("/v1/turn", json=body_with_extras)

    assert response.status_code == 200, response.text
    # The assistant reply doesn't echo the request's media_urls /
    # client_message_id — those are user-msg fields (per contract).
    body = response.json()
    assert body["media_urls"] is None
    assert body["client_message_id"] is None


# ===========================================================================
# ⭐ F10 IDEMPOTENCY (Codex PR-#96 BLOCKER 1 — the load-bearing test)
# ===========================================================================


def test_run_turn_same_idempotency_key_replays_cached_response(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: two POSTs with same X-Idempotency-Key + same X-User-Id +
          same body → byte-identical response (id, content, created_at).
    WHEN: both gates open + headers set on both calls.
    WHY:  F10 verbatim: "default-on on all non-GET endpoints; dedupes
          via Redis 24hr TTL". Codex PR-#96 review caught the original
          handler accepting X-Idempotency-Key but never reading or
          writing Redis around it. This test is the regression gate.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    headers = {
        "X-Idempotency-Key": "replay-test-key-001",
        "X-User-Id": "user-001",
    }

    first = client.post("/v1/turn", json=VALID_BODY, headers=headers)
    second = client.post("/v1/turn", json=VALID_BODY, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    first_body = first.json()
    second_body = second.json()

    # Byte-identical replay — every field on the second response
    # matches the first. Without F10 dedup the `id` would differ
    # (fresh UUID per call) and `created_at` would differ (fresh
    # timestamp per call), so these two asserts ARE the dedup proof.
    assert first_body["id"] == second_body["id"], (
        "F10 idempotency replay regression: `id` drifted between calls "
        "with the same X-Idempotency-Key"
    )
    assert first_body["created_at"] == second_body["created_at"], (
        "F10 idempotency replay regression: `created_at` drifted between "
        "calls with the same X-Idempotency-Key"
    )
    assert first_body["content"] == second_body["content"]
    assert first_body == second_body, (
        "F10 idempotency replay regression: full response payload "
        "drifted between calls with the same X-Idempotency-Key"
    )


def test_run_turn_different_users_with_same_key_do_not_collide(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: same X-Idempotency-Key but different X-User-Id → distinct responses.
    WHEN: two users send chats with the same client-generated key
          (e.g. same content-hash key from popular phrases).
    WHY:  user-scoping per the Day-2-fixup directive — without it, a
          popular phrase would dedupe across users + user B would see
          user A's response. Distinct `id` proves the user scoping
          actually splits the cache key.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    user_a = client.post(
        "/v1/turn",
        json=VALID_BODY,
        headers={
            "X-Idempotency-Key": "collision-test-key",
            "X-User-Id": "user-A",
        },
    )
    user_b = client.post(
        "/v1/turn",
        json=VALID_BODY,
        headers={
            "X-Idempotency-Key": "collision-test-key",
            "X-User-Id": "user-B",
        },
    )

    assert user_a.status_code == 200
    assert user_b.status_code == 200
    # Distinct UUIDs prove the cache key is user-scoped.
    assert user_a.json()["id"] != user_b.json()["id"], (
        "user-scoping regression: same idempotency key collided across "
        "users — F10 cache key needs `{user_id}` in the prefix"
    )


# ===========================================================================
# ERROR PATHS
# ===========================================================================


def test_run_turn_returns_503_when_flag_unset_default(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: 503 when only the env var defaults (flag OFF) are set.
    WHEN: ENABLE_RUN_TURN_STUB unset → defaults False.
    WHY:  default-off is the explicit safety posture — a freshly
          spawned dev environment must NOT accidentally serve the stub.
    """
    # No flag set; environment defaults to "local" per config.py.
    monkeypatch.setenv("ENVIRONMENT", "local")

    response = client.post("/v1/turn", json=VALID_BODY)

    assert response.status_code == 503, response.text
    assert "stub disabled" in response.json()["detail"].lower()


def test_run_turn_returns_503_when_environment_is_production(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: 503 in production EVEN IF the feature flag is on.
    WHEN: environment=production + enable_run_turn_stub=true.
    WHY:  defence-in-depth. The agent definition + Rishi's Day-2
          directive require that the stub MUST NOT leak to mobile
          parity-test traffic regardless of how the flag is set.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post("/v1/turn", json=VALID_BODY)

    assert response.status_code == 503, response.text
    # Production-specific message text so operators reading Sentry can
    # tell which gate fired.
    assert "real llm enablement is day-5" in response.json()["detail"].lower()


def test_run_turn_returns_422_when_conversation_id_missing(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: 422 from FastAPI/Pydantic when required field absent.
    WHEN: body lacks conversation_id.
    WHY:  the RPC contract requires conversation_id; an empty/missing
          value cannot identify which conversation the turn belongs to.
          Pydantic's automatic rejection is the right + simplest gate.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post(
        "/v1/turn", json={"user_message": "no conversation id"}
    )

    assert response.status_code == 422, response.text


def test_run_turn_returns_422_when_user_message_is_empty_string(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: 422 when user_message is an empty string.
    WHEN: body has user_message="".
    WHY:  matches chat-ai behaviour (empty messages are rejected at
          the public-api layer) + matches the `min_length=1` on the
          RunTurnRequest Pydantic model.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post(
        "/v1/turn",
        json={"conversation_id": "abc-123", "user_message": ""},
    )

    assert response.status_code == 422, response.text


# ===========================================================================
# RELATED FILES:
#   conftest.py              — `client` + `clean_settings_cache` +
#                               `fake_redis` fixtures
#   ../app/run_turn.py       — handler under test (the two gates +
#                               F10 dedup + stub)
#   ../app/models/turn.py    — Pydantic models whose validation surface
#                               the 422 tests exercise
#   ../app/config.py         — `enable_run_turn_stub` + `environment` +
#                               `redis_url` settings
#   ../app/idempotency.py    — F10 Redis dedup layer; tests above prove
#                               the dedup-replay path
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — chat-ai MessageResponse parity contract
#                               that the schema-shape happy-path test
#                               validates against
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                            — internal RPC contract (PR #98 commit
#                               f708a49 added idempotency-required-
#                               day-1 + media_urls / client_message_id)
# ===========================================================================
