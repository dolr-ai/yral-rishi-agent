# ---------------------------------------------------------------------------
# test_handler_auth_placeholder.py — Codex PR #97 round-5 ITEM 4 tests
# for the placeholder auth dependency wired onto chat + influencer
# endpoints.
#
# ⭐ START HERE: 2 tests covering the ITEM-4 contract:
#   - Missing Authorization header → 401 envelope
#   - Malformed Authorization header (no "Bearer " prefix) → 401 envelope
#
# PR #102 (Day 4B) will REPLACE the placeholder dependency with the
# full JWT shadow rig — those tests live in
# `tests/contract/test_handler_auth.py` on the Day-4B branch. This
# file is the Day-2-branch placeholder version that guards against the
# auth-gate-missing regression Codex flagged in round 5.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


def test_missing_authorization_header_returns_401_envelope(client_no_auth):
    """POST /api/v1/chat/conversations with NO auth header → 401 envelope.

    WHAT: sends a valid request body but NO `Authorization` header;
          asserts HTTP 401 with the locked envelope shape
          (`success: false, error: "unauthorized", data: null`).
    WHEN: any client hitting any chat / influencer endpoint without
          credentials.
    WHY:  Codex PR #97 round-5 ITEM 4 — the placeholder auth dep
          rejects unauthenticated traffic with the locked 401 envelope
          shape mobile pattern-matches on. Pre-fixup these endpoints
          allowed success without an Authorization header.
    """
    response = client_no_auth.post(
        "/api/v1/chat/conversations",
        json={"ai_influencer_id": "test-influencer-id", "conversation_type": "ai_chat"},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "unauthorized"
    assert body["data"] is None
    assert isinstance(body["msg"], str) and "auth" in body["msg"].lower()


def test_malformed_authorization_header_returns_401_envelope(client_no_auth):
    """GET /api/v1/influencers with `Authorization: Basic ...` → 401 envelope.

    WHAT: sends a request with an Authorization header that doesn't
          start with `Bearer `; asserts HTTP 401 envelope shape.
    WHEN: client / proxy attempts a non-Bearer auth scheme.
    WHY:  the placeholder dep accepts ONLY `Bearer <non-empty>`. A
          Basic-auth header (or any other scheme) is rejected with
          the same 401 envelope as missing-header so mobile's error
          handler has one branch to handle for both cases.
    """
    response = client_no_auth.get(
        "/api/v1/influencers",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "unauthorized"
    assert body["data"] is None


# ===========================================================================
# RELATED FILES:
#   conftest.py                                — provides `client_no_auth`
#   ../../app/api/auth_placeholder.py          — placeholder auth dep
#   ../../app/api/chat_routes.py               — chat routers wire the dep
#                                                via `dependencies=`
#   ../../app/api/influencer_routes.py         — influencer + admin routers
#                                                wire the dep via
#                                                `dependencies=`
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                                              — locked `unauthorized`
#                                                error code + envelope shape
# ===========================================================================
