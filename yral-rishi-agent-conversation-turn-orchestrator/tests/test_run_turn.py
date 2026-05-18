# ---------------------------------------------------------------------------
# test_run_turn.py — Day-2 coverage for the `POST /v1/turn` RPC handler.
#
# ⭐ START HERE: this file exercises BOTH the happy path (stub returns
# a schema-valid MessageDto when the two gates open) AND the error
# paths (gates closed → 503; malformed body → 422). Per J1 the
# orchestrator is HOT-tier — these tests are the floor for Day-2's
# only new route.
#
# WHAT EACH TEST PROVES — at-a-glance index (priority order, happy
# paths first, then error paths, per B7):
#
#   HAPPY PATHS
#     test_run_turn_returns_schema_valid_message_dto_when_both_gates_open
#         200 + all 8 MessageDto fields present + correct types
#     test_run_turn_idempotency_key_header_is_accepted
#         200 when X-Idempotency-Key is set
#     test_run_turn_request_id_header_is_accepted
#         200 when X-Request-Id is set
#     test_run_turn_echoes_conversation_id_into_response
#         response.conversation_id == request.conversation_id
#     test_run_turn_stub_content_matches_documented_placeholder
#         content is the exact literal string the agent def specifies
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
# Combined with the auto-use `clean_settings_cache` fixture in
# conftest.py, this means each test starts from `enable_run_turn_stub=
# False, environment="local"` and explicitly sets what it needs.
#
# WHY NO MOCKED DOWNSTREAM (Soul-File, LLM, memory)?
# The Day-2 stub has no downstreams — that's its point. Day-5 PRs add
# the real downstreams + the mocked-downstream test patterns to match.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import pytest
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


def test_run_turn_returns_schema_valid_message_dto_when_both_gates_open(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: 200 + every MessageDto field present with the right type.
    WHEN: env != production AND enable_run_turn_stub=true.
    WHY:  proves the stub is byte-shape-compatible with chat-ai's
          MessageDto contract so Session 3 can wire its handler.
    """
    # Open both gates explicitly. Default settings keep them closed.
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post("/v1/turn", json=VALID_BODY)

    assert response.status_code == 200, response.text
    body = response.json()

    # Every chat-ai MessageDto field appears and has the right type.
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
    WHY:  Day-3 wires this header into the safety stack + Langfuse;
          Day-2 must accept it without erroring so Session 3 can send
          it from Day-2 onwards without breaking integration tests.
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
    WHY:  same rationale as idempotency-key — Session 3 sends a
          correlation ID with every internal RPC for Langfuse trace
          joining.
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
#   conftest.py            — `client` + `clean_settings_cache` fixtures
#   ../app/run_turn.py     — handler under test (the two gates + stub)
#   ../app/models/turn.py  — Pydantic models whose validation surface
#                            the 422 tests exercise
#   ../app/config.py       — `enable_run_turn_stub` + `environment` settings
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                          — chat-ai MessageDto parity contract that the
#                            schema-shape happy-path test validates against
# ===========================================================================
