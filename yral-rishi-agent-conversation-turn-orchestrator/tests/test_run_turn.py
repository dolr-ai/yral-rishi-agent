# ---------------------------------------------------------------------------
# test_run_turn.py — Day-2 coverage for the `POST /v1/turn` RPC handler.
#
# ⭐ START HERE: this file exercises the happy path + the four error
# paths + the F10 atomic-dedup gates. Per J1 the orchestrator is
# HOT-tier — these tests are the floor for Day-2's only new route.
#
# WHAT EACH TEST PROVES — at-a-glance index (priority order, happy
# paths first, then error paths, per B7):
#
#   HAPPY PATHS
#     test_run_turn_returns_schema_valid_message_response_when_both_gates_open
#         200 + all 8 MessageResponse fields present + correct types
#     test_run_turn_request_id_header_is_accepted
#         200 when X-Request-Id is set (correlation id passthrough)
#     test_run_turn_echoes_conversation_id_into_response
#         response.conversation_id == request.conversation_id
#     test_run_turn_stub_content_matches_documented_placeholder
#         content is the exact literal string the agent def specifies
#     test_run_turn_accepts_optional_media_urls_and_client_message_id
#         A8 multi-modal-parity fields in RunTurnRequest are accepted
#
#   ⭐ F10 ATOMIC DEDUP (Codex PR-#96 round-3 BLOCKER 1)
#     test_run_turn_same_idempotency_key_replays_cached_response
#         Two sequential POSTs with same key + same user + same body →
#         byte-identical replay (same id, content, created_at).
#     test_run_turn_concurrent_same_key_same_body_executes_handler_once
#         Two CONCURRENT POSTs (asyncio.gather) with same key + same body →
#         only ONE `mark_complete` invocation + both responses byte-equal.
#     test_run_turn_same_key_different_body_returns_409_envelope
#         Same key + DIFFERENT body → 409 + ApiResponse envelope shape
#         {success:false, msg:..., error:"idempotency_key_reused_with_
#         different_body", data:null}.
#     test_run_turn_different_users_with_same_key_do_not_collide
#         Same key, different X-User-Id → distinct responses (user scoping).
#
#   ERROR PATHS
#     test_run_turn_returns_400_envelope_when_idempotency_key_missing
#         No X-Idempotency-Key header → 400 + ApiResponse envelope shape.
#     test_run_turn_returns_503_when_flag_unset_default
#         default settings → 503 (flag off; gate fires before idempotency)
#     test_run_turn_returns_503_when_environment_is_production
#         even with flag ON, prod gate still refuses (gate first)
#     test_run_turn_returns_422_when_conversation_id_missing
#         Pydantic validation rejects missing required field
#     test_run_turn_returns_422_when_user_message_is_empty_string
#         min_length=1 on user_message
#
# WHY USE monkeypatch FOR ENV VARS?
# pytest's `monkeypatch.setenv` is scope-limited to the test — env
# changes auto-undo after the test exits. Combined with the auto-use
# `clean_settings_cache` + `fake_redis` fixtures in conftest.py, each
# test starts from `enable_run_turn_stub=False, environment="local"`
# + empty Redis + fresh Settings parse.
#
# WHY THE HAPPY-PATH TESTS NOW PASS HEADERS
# Codex PR-#96 round-3 BLOCKER 1a made X-Idempotency-Key REQUIRED.
# Tests that used to send the body alone now include the
# `_required_headers(...)` helper output so the gate doesn't 400 them.
# Tests that exercise the missing-header path explicitly omit it.
#
# WHY NO MOCKED DOWNSTREAM (Soul-File, LLM, memory)?
# The Day-2 stub has no downstreams — that's its point. Day-5 PRs add
# the real downstreams + the mocked-downstream test patterns to match.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib asyncio — used by the concurrent-POST regression test to fire
# two `client.post(...)` calls via `asyncio.gather(...)` so they truly
# race on the `SET NX` critical section in `app.idempotency.acquire_or_check`.
import asyncio

# `pytest` — the test runner; we use `pytest.MonkeyPatch` as a typed
# parameter annotation for tests that mutate env vars OR patch the
# `mark_complete` function for the concurrent-handler-count assertion.
import pytest

# `httpx.AsyncClient` — the type the `async_client` fixture yields.
# Imported for the parameter annotation only; the fixture body in
# conftest.py builds the actual instance.
import httpx

# `TestClient` — same FastAPI client the conftest's `client` fixture
# yields; importing here is just for the type annotation on test
# parameters (mypy / IDE friendliness).
from fastapi.testclient import TestClient

# `app.idempotency` — module the fakeredis fixture (in conftest)
# patches `_redis` on, and where the original `mark_complete` lives.
# Imported here so the concurrent-handler-count test can grab the
# original to wrap. Also used by the round-4 BLOCKER 1 production-
# fail-closed test, which imports the un-stubbed `init_redis` via
# `conftest._REAL_INIT_REDIS_FOR_TESTS`.
import app.idempotency as app_idempotency

# Import the conftest's un-stubbed init_redis reference for the
# round-4 BLOCKER 1 production-fail-closed regression test.
from tests.conftest import _REAL_INIT_REDIS_FOR_TESTS

# `app.run_turn` — module the concurrent test patches the
# `mark_complete` reference on. Why patch the run_turn module's
# reference, not idempotency's? Python `from foo import bar` binds
# `bar` into the importing module's namespace as a separate
# reference; replacing `foo.bar` later doesn't affect callers that
# already imported the original. The handler in run_turn.py imports
# `mark_complete` directly (`from app.idempotency import
# mark_complete`), so the spy MUST live on `app.run_turn.mark_complete`
# to intercept the actual handler call.
import app.run_turn as app_run_turn


# stdlib `uuid` — used to generate valid UUID-shaped X-Idempotency-Key
# values for every test that expects 200. Round-4 BLOCKER 3 added
# UUID-format validation at the route boundary; tests that used to
# pass plain strings like "test-idempotency-key-001" would now 400.
import uuid


# ===========================================================================
# Shared test helpers
# ===========================================================================

# A valid request body the happy-path tests use as their baseline. Tests
# that need to assert validation errors override one field at a time so
# the failure surface stays minimal.
VALID_BODY: dict[str, str] = {
    "conversation_id": "test-conversation-uuid-001",
    "user_message": "hello orchestrator",
}


# A fixed valid UUID used as the default X-Idempotency-Key in every
# 200-expecting test. Round-4 BLOCKER 3 made UUID-format validation
# mandatory; using a stable constant keeps the test output diffable
# (a fresh uuid4 per call would change the key suffix every run).
_DEFAULT_TEST_IDEMPOTENCY_KEY: str = "550e8400-e29b-41d4-a716-446655440001"


def _required_headers(
    idempotency_key: str = _DEFAULT_TEST_IDEMPOTENCY_KEY,
    user_id: str = "test-user-default",
) -> dict[str, str]:
    """Return the headers every successful POST /v1/turn must include.

    WHAT: returns `{"X-Idempotency-Key": <uuid>, "X-User-Id": <user>}`.
    WHEN: called by every test that expects 200 (not the missing-header
          or production-gate tests which intentionally omit one).
    WHY:  X-Idempotency-Key is REQUIRED per F10 + must parse as a UUID
          per round-4 BLOCKER 3. X-User-Id is REQUIRED per round-4
          BLOCKER 2. Centralising the test headers in a helper keeps
          each test body focused on the ONE thing it's asserting; a
          header-shape bump only edits this helper.
    """
    return {
        "X-Idempotency-Key": idempotency_key,
        "X-User-Id": user_id,
    }


def _fresh_uuid_key() -> str:
    """Return a freshly-generated UUID-shaped X-Idempotency-Key value.

    WHAT: `str(uuid.uuid4())`.
    WHEN: called by tests that need a key DIFFERENT from the default
          (e.g. tests that fire multiple POSTs and don't want
          accidental cross-test cache pollution despite the fakeredis
          fixture's per-test reset).
    WHY:  per-test isolation safety net + reads as English at the
          callsite.
    """
    return str(uuid.uuid4())


# ===========================================================================
# HAPPY PATHS
# ===========================================================================


def test_run_turn_returns_schema_valid_message_response_when_both_gates_open(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: 200 + every MessageResponse field present with the right type.
    WHEN: env != production AND enable_run_turn_stub=true AND
          X-Idempotency-Key + X-User-Id headers set.
    WHY:  proves the stub is byte-shape-compatible with chat-ai's
          MessageResponse contract so Session 3 can wire its handler.
    """
    # Open both gates explicitly. Default settings keep them closed.
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post(
        "/v1/turn", json=VALID_BODY, headers=_required_headers(),
    )

    assert response.status_code == 200, response.text
    body = response.json()

    # Every chat-ai MessageResponse field appears and has the right type.
    assert isinstance(body["id"], str) and len(body["id"]) > 0
    assert body["conversation_id"] == VALID_BODY["conversation_id"]
    assert body["role"] == "assistant"
    assert isinstance(body["content"], str) and len(body["content"]) > 0
    assert body["media_urls"] is None
    assert body["client_message_id"] is None
    assert isinstance(body["created_at"], str)
    assert body["created_at"].endswith("Z")  # ISO8601 UTC trailing Z
    assert body["count_toward_paywall"] is True


def test_run_turn_request_id_header_is_accepted(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: X-Request-Id header is accepted without 4xx.
    WHEN: both gates open + required headers + X-Request-Id set.
    WHY:  Session 3 sends a correlation ID with every internal RPC
          for Langfuse trace joining; the handler must accept it
          without erroring.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    headers = _required_headers() | {"X-Request-Id": "test-request-id-001"}
    response = client.post("/v1/turn", json=VALID_BODY, headers=headers)

    assert response.status_code == 200, response.text


def test_run_turn_echoes_conversation_id_into_response(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: response.conversation_id mirrors the request's value.
    WHEN: both gates open + required headers + valid body.
    WHY:  Session 3 + downstream callers rely on this echo to
          correlate the reply with the conversation they kicked off.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    distinct_id = "echo-test-conversation-001"
    response = client.post(
        "/v1/turn",
        json={"conversation_id": distinct_id, "user_message": "echo me"},
        headers=_required_headers(idempotency_key="550e8400-e29b-41d4-a716-446655440010"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["conversation_id"] == distinct_id


def test_run_turn_stub_content_matches_documented_placeholder(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: response.content is exactly the agent-def-specified string.
    WHEN: both gates open + required headers.
    WHY:  the bracketed placeholder is the searchable marker future
          readers + Sentry log greppers use to identify "this turn
          came from the Day-2 stub". Any drift makes that grep fail
          silently and a future PR could ship a stub reply to prod.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post(
        "/v1/turn", json=VALID_BODY,
        headers=_required_headers(idempotency_key="550e8400-e29b-41d4-a716-446655440011"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["content"] == (
        "[v2 phase-1 day-2 orchestrator stub — real LLM response from day-5]"
    )


def test_run_turn_accepts_optional_media_urls_and_client_message_id(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: RunTurnRequest accepts the two new optional fields without 422.
    WHEN: body includes media_urls + client_message_id per the updated
          contract; required headers present.
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
    response = client.post(
        "/v1/turn", json=body_with_extras,
        headers=_required_headers(idempotency_key="550e8400-e29b-41d4-a716-446655440012"),
    )

    assert response.status_code == 200, response.text
    # The assistant reply doesn't echo the request's media_urls /
    # client_message_id — those are user-msg fields (per contract).
    body = response.json()
    assert body["media_urls"] is None
    assert body["client_message_id"] is None


# ===========================================================================
# ⭐ F10 ATOMIC DEDUP (Codex round-3 BLOCKER 1)
# ===========================================================================


def test_run_turn_same_idempotency_key_replays_cached_response(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: two sequential POSTs with same key + same user + same body
          → byte-identical response (id, content, created_at).
    WHEN: both gates open + headers set on both calls.
    WHY:  F10 verbatim: "default-on on all non-GET endpoints; dedupes
          via Redis 24hr TTL". The atomic-dedup fixup (round-3
          BLOCKER 1b) replaced GET-then-SET with SET-NX-then-overwrite;
          this test is the regression gate for the happy-path replay.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    headers = _required_headers(
        idempotency_key="550e8400-e29b-41d4-a716-446655440013", user_id="user-001",
    )

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


async def test_run_turn_concurrent_same_key_same_body_executes_handler_once(
    monkeypatch: pytest.MonkeyPatch, async_client: httpx.AsyncClient
) -> None:
    """WHAT: two CONCURRENT POSTs (asyncio.gather) with same key + same
          body → only ONE `mark_complete` invocation + both responses
          byte-equal.
    WHEN: both gates open + headers set on both calls.
    WHY:  Codex round-3 BLOCKER 1b — the previous GET-then-SET flow
          let two concurrent requests both execute the handler. The
          atomic SET-NX critical section serialises the race so only
          ONE acquires the lock. The other request polls until it
          sees the cached response. Asserting `mark_complete` fires
          exactly once is the direct regression gate for the race fix.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    # Spy on mark_complete to count invocations. The fake_redis fixture
    # already set up the in-memory Redis; we wrap mark_complete to
    # observe how many times it's called without breaking its logic.
    mark_complete_invocations: list[str] = []
    original_mark_complete = app_idempotency.mark_complete

    async def counting_mark_complete(
        redis_key: str, fingerprint: str, response_payload: dict,
    ) -> None:
        mark_complete_invocations.append(redis_key)
        return await original_mark_complete(
            redis_key, fingerprint, response_payload,
        )

    # Patch the run_turn module's reference (not idempotency's) — see
    # the import-block comment for why.
    monkeypatch.setattr(app_run_turn, "mark_complete", counting_mark_complete)

    headers = _required_headers(
        idempotency_key="550e8400-e29b-41d4-a716-446655440014", user_id="concurrent-user-001",
    )

    # Fire two POSTs truly concurrently. asyncio.gather schedules
    # both before either resolves, so the SET NX race in
    # app.idempotency.acquire_or_check fires for real.
    first_task = async_client.post("/v1/turn", json=VALID_BODY, headers=headers)
    second_task = async_client.post("/v1/turn", json=VALID_BODY, headers=headers)
    first, second = await asyncio.gather(first_task, second_task)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    # Byte-equal responses — the loser of the SET NX race polled until
    # the winner marked completion, then returned the cached payload.
    assert first.json() == second.json(), (
        "Concurrent dedup regression: two requests with the same key + "
        "same body returned different responses. The atomic SET NX critical "
        "section in acquire_or_check is leaking the race."
    )

    # mark_complete fires exactly once — the winner. The loser
    # short-circuits via replay_done, never reaching the handler's
    # mark_complete call.
    assert len(mark_complete_invocations) == 1, (
        "Concurrent dedup regression: mark_complete was called "
        f"{len(mark_complete_invocations)} times; the SET-NX critical "
        "section should serialise concurrent duplicates to exactly one "
        "handler invocation."
    )


def test_run_turn_same_key_different_body_returns_409_envelope(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: same X-Idempotency-Key + different request body → 409 +
          ApiResponse envelope shape.
    WHEN: a buggy client (or attacker) reuses an idempotency key
          across two distinct payloads.
    WHY:  Codex round-3 BLOCKER 1b — fingerprint of canonical-JSON
          body is stored alongside the cached response so a different
          body cannot replay the wrong reply. The 409 is the
          contract-prescribed response.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    headers = _required_headers(
        idempotency_key="550e8400-e29b-41d4-a716-446655440015", user_id="user-001",
    )

    first_body = {
        "conversation_id": "conv-001",
        "user_message": "the first message",
    }
    second_body = {
        "conversation_id": "conv-001",
        "user_message": "a completely different message",
    }

    first = client.post("/v1/turn", json=first_body, headers=headers)
    assert first.status_code == 200, first.text

    second = client.post("/v1/turn", json=second_body, headers=headers)
    assert second.status_code == 409, second.text

    envelope = second.json()
    assert envelope == {
        "success": False,
        "msg": envelope["msg"],  # message wording is not load-bearing
        "error": "idempotency_key_reused_with_different_body",
        "data": None,
    }
    # Belt-and-suspenders: the error wording must NOT be empty.
    assert isinstance(envelope["msg"], str) and len(envelope["msg"]) > 0


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
        "/v1/turn", json=VALID_BODY,
        headers=_required_headers(
            idempotency_key="550e8400-e29b-41d4-a716-446655440016", user_id="user-A",
        ),
    )
    user_b = client.post(
        "/v1/turn", json=VALID_BODY,
        headers=_required_headers(
            idempotency_key="550e8400-e29b-41d4-a716-446655440016", user_id="user-B",
        ),
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


def test_run_turn_returns_400_envelope_when_idempotency_key_missing(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: missing X-Idempotency-Key → 400 + ApiResponse envelope shape.
    WHEN: gates open + body valid + ONLY X-User-Id header present (no
          X-Idempotency-Key).
    WHY:  Codex round-3 BLOCKER 1a — F10 + the coordinator's PR #98
          commit 31d1dac contract update require the header to be
          REQUIRED. The previous round-2 code generated a server-side
          UUID4 which deduplicated nothing on retry. This test proves
          the 400-envelope path fires INSTEAD of the silent fallback.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post(
        "/v1/turn", json=VALID_BODY,
        headers={"X-User-Id": "test-user"},  # intentionally no X-Idempotency-Key
    )

    assert response.status_code == 400, response.text
    envelope = response.json()
    assert envelope == {
        "success": False,
        "msg": envelope["msg"],  # wording is not load-bearing
        "error": "idempotency_key_required",
        "data": None,
    }
    assert isinstance(envelope["msg"], str) and len(envelope["msg"]) > 0


def test_run_turn_returns_400_envelope_when_user_id_missing(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: missing X-User-Id → 400 + ApiResponse envelope shape.
    WHEN: gates open + body valid + ONLY X-Idempotency-Key header
          present (no X-User-Id).
    WHY:  Codex round-4 BLOCKER 2 — the previous round-3 code fell
          back to an "unknown-user" sentinel which collapsed the
          idempotency cache scope. Two unrelated callers with missing
          headers could replay each other's cached responses (cross-
          tenant data-leak shape). This test proves the 400-envelope
          path fires INSTEAD of the silent fallback.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post(
        "/v1/turn", json=VALID_BODY,
        # intentionally no X-User-Id — only X-Idempotency-Key set
        headers={"X-Idempotency-Key": _DEFAULT_TEST_IDEMPOTENCY_KEY},
    )

    assert response.status_code == 400, response.text
    envelope = response.json()
    assert envelope == {
        "success": False,
        "msg": envelope["msg"],
        "error": "user_id_header_required",
        "data": None,
    }
    assert isinstance(envelope["msg"], str) and len(envelope["msg"]) > 0


def test_run_turn_returns_400_envelope_when_idempotency_key_not_uuid(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: non-UUID X-Idempotency-Key → 400 + envelope (error:
          "idempotency_key_invalid_format").
    WHEN: gates open + body valid + X-Idempotency-Key="hello-world".
    WHY:  Codex round-4 BLOCKER 3 — a malicious or buggy client can
          stuff PII or message-content text into the header value;
          that text would then land in Redis keys + structured logs
          (H6 surface). UUID validation at the route boundary bounds
          the value to a known-non-PII shape by construction.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post(
        "/v1/turn", json=VALID_BODY,
        headers={
            "X-Idempotency-Key": "hello-world",  # NOT a UUID
            "X-User-Id": "test-user-default",
        },
    )

    assert response.status_code == 400, response.text
    envelope = response.json()
    assert envelope == {
        "success": False,
        "msg": envelope["msg"],
        "error": "idempotency_key_invalid_format",
        "data": None,
    }


def test_run_turn_returns_400_envelope_when_idempotency_key_empty_string(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: empty-string X-Idempotency-Key → 400 + envelope (error:
          "idempotency_key_invalid_format" — empty fails UUID parse).
    WHEN: gates open + body valid + X-Idempotency-Key="".
    WHY:  Codex round-4 BLOCKER 3 — defence-in-depth on the validation
          boundary. An empty string is technically a "present" header
          (so `idempotency_key is None` check doesn't fire) but is
          clearly invalid. UUID parse catches it.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post(
        "/v1/turn", json=VALID_BODY,
        headers={
            "X-Idempotency-Key": "",
            "X-User-Id": "test-user-default",
        },
    )

    assert response.status_code == 400, response.text
    envelope = response.json()
    # FastAPI may strip empty headers OR pass them through — either
    # response is acceptable as long as it's 400 + envelope. The
    # `error` field should be one of the two header-rejection codes.
    assert envelope["error"] in {
        "idempotency_key_required",
        "idempotency_key_invalid_format",
    }
    assert envelope["success"] is False
    assert envelope["data"] is None


async def test_init_redis_raises_system_exit_in_production_without_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: init_redis() raises SystemExit when environment=production
          AND redis_sentinel_enabled=False.
    WHEN: a misconfigured deploy sets ENVIRONMENT=production without
          flipping REDIS_SENTINEL_ENABLED=true.
    WHY:  Codex round-4 BLOCKER 1 + J1 HOT-tier — the previous round-3
          fix only logged a WARNING on the single-primary fallback.
          A warning is not enforcement: a deploy with the wrong env
          var would land silently and the service would run on a
          single-primary Redis with no Sentinel failover protection,
          violating C11. The round-4 fix makes the violation
          fail-closed: process refuses to start.

          This test bypasses two layers of the auto-use `fake_redis`
          fixture: (1) calls `_REAL_INIT_REDIS_FOR_TESTS()` to hit
          the un-stubbed init_redis captured at conftest module-load,
          and (2) sets `app.idempotency._redis = None` so the
          `if _redis is not None: return` short-circuit doesn't fire
          before the production-fail-closed gate runs. Production
          deploys start with `_redis = None` by construction; the
          test reproduces that fresh-startup state.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    # Intentionally do NOT set REDIS_SENTINEL_ENABLED — defaults False.

    # Force the fresh-startup state by un-injecting the fakeredis
    # the auto-use fixture pre-set. Without this the short-circuit
    # would fire before the fail-closed gate.
    monkeypatch.setattr(app_idempotency, "_redis", None)

    with pytest.raises(SystemExit) as exit_info:
        await _REAL_INIT_REDIS_FOR_TESTS()

    # The SystemExit message names the env var the operator must set
    # AND the alternative remediation; grep for "C11 violation" so a
    # future reader sees the constraint citation in the test text.
    exit_message = str(exit_info.value)
    assert "C11 violation" in exit_message, (
        f"expected SystemExit to cite C11; got: {exit_message!r}"
    )
    assert "REDIS_SENTINEL_ENABLED" in exit_message, (
        "expected SystemExit message to name the env var to flip; "
        f"got: {exit_message!r}"
    )


async def test_init_redis_does_not_raise_in_local_without_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: init_redis() returns cleanly in environment=local even when
          redis_sentinel_enabled=False (no SystemExit).
    WHEN: laptop dev / docker-compose default — environment="local"
          with no Sentinel hosts configured.
    WHY:  the BLOCKER 1 fail-closed gate must NOT fire outside
          production. Laptop dev needs to keep working against the
          docker-compose single-primary Redis (or fakeredis in tests).
          Negative control on the production-fail-closed test above.

          The fake_redis fixture has already monkeypatched
          `_redis` to a FakeRedis instance, so init_redis short-
          circuits on the `if _redis is not None: return` check
          after passing the fail-closed gate.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    # REDIS_SENTINEL_ENABLED defaults False.

    # Asserting NO exception raises — pytest auto-fails the test if
    # SystemExit (or anything else) propagates out.
    await _REAL_INIT_REDIS_FOR_TESTS()


def test_run_turn_returns_503_when_flag_unset_default(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """WHAT: 503 when only the env var defaults (flag OFF) are set.
    WHEN: ENABLE_RUN_TURN_STUB unset → defaults False.
    WHY:  default-off is the explicit safety posture — a freshly
          spawned dev environment must NOT accidentally serve the stub.
          The gate fires BEFORE the X-Idempotency-Key required check
          so this test deliberately omits both headers (we're proving
          the gate is the outermost check).
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
          Gate fires before idempotency-key required check.
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
          422 fires BEFORE the route handler body, so the gate +
          idempotency-key required check don't run.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    response = client.post(
        "/v1/turn", json={"user_message": "no conversation id"},
        headers=_required_headers(),
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
        headers=_required_headers(),
    )

    assert response.status_code == 422, response.text


# ===========================================================================
# ⭐ ROUND-5 BLOCKER 96-A — IDEMPOTENCY LOCK FAILURE CLEANUP
# ===========================================================================


def test_run_turn_releases_idempotency_lock_when_handler_raises_so_retry_starts_fresh(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis,
) -> None:
    """WHAT: when the handler raises mid-execution AFTER acquiring the
          F10 dedup lock (we monkeypatch `mark_complete` to raise),
          the lock is released from Redis + a same-key retry sees a
          fresh `acquired` decision (different response.id proves it
          ran the handler again instead of replaying a cached
          payload).
    WHEN: every chat turn that hits an exception between
          `acquire_or_check` returning `acquired` and `mark_complete`
          running — bug in our code today, transient downstream
          failure once Day-5+ LLM calls land.
    WHY:  Codex PR-#96 round-5 BLOCKER 96-A. The round-4 (and
          earlier) code had no failure-path cleanup; an exception in
          this window held the in-progress lock for the full 24h F10
          dedup TTL, blocking every legitimate retry with the same
          X-Idempotency-Key for 24 hours. The fix wraps the
          post-acquire window in try/except + calls
          `release_in_progress_lock` on exception (then re-raises
          so FastAPI's default exception handler produces the 500).

          Regression-gate shape:
          - Patch `app.run_turn.mark_complete` (the import-shadowed
            local reference; same pattern PR #96 round-3's concurrent
            test uses + same pattern PR #104 round-4's parallel-fetch
            test uses) to raise `RuntimeError`.
          - POST with a unique X-Idempotency-Key + verify 500.
          - Inspect fake_redis directly: the key must be gone (a
            DELETE was issued by `release_in_progress_lock`).
          - Un-patch + POST again with the SAME key + verify 200 +
            response.id is fresh (NOT a replay of any cached
            payload, since no payload was cached + the lock no
            longer holds the retry off).

          Why `response.id` proves "fresh execution":
          `MessageResponse.id = str(uuid4())` at handler-time; if the
          second request returned a replayed cached payload from the
          first attempt, the `id` would have been frozen at the
          first attempt. Different `id` → fresh handler invocation,
          which is the exact "retry starts fresh" property the fix
          promises.
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("ENABLE_RUN_TURN_STUB", "true")

    # Build a dedicated TestClient with `raise_server_exceptions=False`
    # so the simulated handler exception becomes the expected HTTP 500
    # response instead of propagating into the test thread + crashing
    # the test before we can assert on the lock state. The default
    # TestClient (used by every other test in this file) re-raises
    # server-side exceptions for fast diagnostics; here we deliberately
    # want the production-shape error path that FastAPI's exception
    # handler produces in real deployments.
    from app.main import app

    failure_test_client = TestClient(app, raise_server_exceptions=False)

    # Unique key for this test (avoids any cross-test fakeredis bleed
    # despite the per-test fixture reset).
    failure_test_idempotency_key = _fresh_uuid_key()
    headers = _required_headers(idempotency_key=failure_test_idempotency_key)

    # Compute the Redis key the handler will use so we can inspect
    # fakeredis state directly. Mirrors `compute_idempotency_key`'s
    # format string — kept inline to avoid coupling the test to the
    # production helper's internals (a future refactor of the helper
    # MUST update both sites; explicit copy makes the test failure
    # mode loud rather than silent).
    expected_redis_key = (
        f"idempotency:orchestrator:run-turn:{headers['X-User-Id']}:"
        f"{failure_test_idempotency_key}"
    )

    # Patch mark_complete to raise. Use `monkeypatch.setattr` so the
    # un-patch happens automatically when this test function returns,
    # leaving the second request (below) to hit the REAL mark_complete.
    async def raising_mark_complete_for_failure_test(*args, **kwargs):
        """WHAT: drop-in coroutine that raises RuntimeError every time.
        WHEN: substituted in for `app.run_turn.mark_complete` for the
              first POST in this regression test.
        WHY:  simulates a handler-side failure AFTER lock acquisition
              + BEFORE `mark_complete` ran. Day-5+ LLM-client errors,
              Pydantic validation errors on response build, etc.
              all land in this same window.
        """
        raise RuntimeError(
            "simulated handler failure for round-5 BLOCKER 96-A regression test"
        )

    monkeypatch.setattr(
        app_run_turn, "mark_complete", raising_mark_complete_for_failure_test,
    )

    # First POST — handler acquires lock, builds response, hits the
    # patched raising `mark_complete`, the try/except catches +
    # releases the lock + re-raises, FastAPI maps it to a 500.
    first_response = failure_test_client.post(
        "/v1/turn", json=VALID_BODY, headers=headers,
    )
    assert first_response.status_code == 500, (
        f"expected handler exception to surface as HTTP 500; "
        f"got status_code={first_response.status_code}, "
        f"body={first_response.text!r}"
    )

    # Verify the lock was released — fakeredis should not have the key.
    # This is the CORE assertion: without the release_in_progress_lock
    # call in the handler's except branch, this key would still exist
    # in fakeredis (state=in_progress + 24h TTL) + the same-key retry
    # below would 503 with in_flight_timeout (or replay-done if some
    # other code path had cached a fake response).
    # `fakeredis.aioredis.FakeRedis.get(...)` is an async coroutine
    # (it mirrors `redis.asyncio.Redis.get`); driving it from this sync
    # test requires `asyncio.run(...)` to push the coroutine through
    # to its return value. Returns the raw value (decode_responses=True
    # → str) on hit, None on miss. We expect None.
    cached_value_after_failure = asyncio.run(fake_redis.get(expected_redis_key))
    assert cached_value_after_failure is None, (
        f"expected the in-progress lock to be released after handler "
        f"exception; fakeredis still has key={expected_redis_key!r} "
        f"with value={cached_value_after_failure!r}. The "
        f"release_in_progress_lock call in the except branch is the "
        f"only mechanism that should clear this; if this assertion "
        f"fails, the lock-leak bug is back."
    )

    # Un-patch + POST again with the SAME idempotency key. The handler
    # should treat this as a fresh request (acquired decision) +
    # execute end-to-end (real mark_complete is back) + return a
    # MessageResponse with a NEWLY-generated `id`. If the lock had
    # leaked, this retry would 503 (in_flight_timeout after the poll
    # ceiling) instead of 200.
    monkeypatch.setattr(
        app_run_turn, "mark_complete", app_idempotency.mark_complete,
    )

    second_response = failure_test_client.post(
        "/v1/turn", json=VALID_BODY, headers=headers,
    )
    assert second_response.status_code == 200, (
        f"expected same-key retry after lock-release to start fresh + "
        f"return 200; got status_code={second_response.status_code}, "
        f"body={second_response.text!r}. If this is 503 with "
        f"`idempotency_in_flight`, the round-5 fix didn't release the "
        f"lock; if it's 200 but the id matches the first attempt, the "
        f"retry replayed a stale cached payload."
    )

    second_body = second_response.json()
    assert second_body["conversation_id"] == VALID_BODY["conversation_id"]
    # `id` should be a freshly-generated uuid4 from the second handler
    # invocation. There's no first-attempt `id` to compare against
    # (first attempt 500'd before returning a body), but we can still
    # verify the id is a valid UUID-shaped string + non-empty.
    assert isinstance(second_body["id"], str) and len(second_body["id"]) >= 32, (
        f"expected freshly-generated UUID id on the second attempt; "
        f"got {second_body['id']!r}"
    )


# ===========================================================================
# RELATED FILES:
#   conftest.py              — `client` + `async_client` + `clean_settings_cache`
#                               + `fake_redis` fixtures
#   ../app/run_turn.py       — handler under test (gates + F10 atomic dedup
#                               + 400/409/503-envelope error paths)
#   ../app/idempotency.py    — F10 Redis dedup layer; the concurrent test
#                               spies on mark_complete to count handler
#                               invocations
#   ../app/models/turn.py    — Pydantic models whose validation surface
#                               the 422 tests exercise
#   ../app/config.py         — `enable_run_turn_stub` + `environment` +
#                               `redis_url` + `redis_sentinel_enabled`
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — chat-ai MessageResponse parity contract +
#                               ApiResponse envelope shape used in
#                               4xx/5xx error paths
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                            — internal RPC contract; PR #98 commit
#                               31d1dac spells out C11 + atomic dedup
#                               + 400 reject which these tests exercise
# ===========================================================================
