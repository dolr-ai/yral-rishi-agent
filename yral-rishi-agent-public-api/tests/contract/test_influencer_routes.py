# ---------------------------------------------------------------------------
# test_influencer_routes.py — contract tests for /api/v1/influencers/* +
# the BLOCKER-4 write-set + admin stubs.
#
# ⭐ START HERE: each test asserts envelope + response-model shape per
# interface-contracts/00-api-contract.md, OR the flag-off 503 gate on
# the read endpoints, OR the BLOCKER-4 service_unavailable stubs on
# the write + admin endpoints, OR the BLOCKER-6 Cache-Control header
# on the list endpoint, OR the Day-8 directory-RPC failure-mapping
# paths (503 on unreachable / 404 on missing / bad-shape).
#
# DAY-8 CHANGE (PR-B): the list + by-id read endpoints proxy through
# Session 4's influencer-and-profile-directory. Tests mock
# `directory_client.list_influencers` / `_.get_influencer` to bypass
# the real HTTP call — same mock-the-boundary pattern Day-4C's
# test_orchestrator_proxy uses for the orchestrator path. The
# /trending endpoint stays on the Day-2 stub (no contract declared
# yet for trending in 01-internal-rpc-contracts.md).
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app import directory_client


# A canonical InfluencerResponse JSON the mocked directory returns
# on the happy path. Shape matches `app/api/response_models.py
# InfluencerResponse` verbatim.
_HAPPY_INFLUENCER = {
    "id": "tara-real-influencer-id",
    "display_name": "Tara",
    "bio": "A real influencer record returned by the mocked directory.",
    "avatar_url": "https://cdn.example.com/avatars/tara.png",
    "archetype": "companion",
    "is_nsfw": False,
    "follower_count": 1024,
    "creator_user_id": None,
    "is_active": "active",
}


def _make_mock_response(status_code: int, json_body) -> httpx.Response:
    """Build a synthetic httpx.Response for the mocked directory.

    WHAT: returns an httpx.Response with the given status + JSON body
          (dict OR list). Used by per-test mocks to stub the directory's
          exact response shape (status + body).
    WHEN: called by per-test directory mocks below.
    WHY:  mirrors test_orchestrator_proxy._make_mock_response so the
          two test files read the same way; the route handlers crossed
          the same httpx.Response boundary so the helpers stay parallel.
    """
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(json_body).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


@pytest.fixture
def mock_directory_list_happy(monkeypatch):
    """Patch directory_client.list_influencers → 200 with [_HAPPY_INFLUENCER].

    WHAT: replaces directory_client.list_influencers with an AsyncMock
          that returns a 200 response carrying a single-element list of
          _HAPPY_INFLUENCER. Returns the AsyncMock so tests can assert
          on call_args (limit / offset / headers).
    WHEN: used by the happy-path list tests.
    WHY:  bypass the real HTTP layer; lets tests exercise the wrap +
          envelope path without a running Session 4 directory service.
    """
    mock = AsyncMock(return_value=_make_mock_response(200, [_HAPPY_INFLUENCER]))
    monkeypatch.setattr(directory_client, "list_influencers", mock)
    return mock


@pytest.fixture
def mock_directory_by_id_happy(monkeypatch):
    """Patch directory_client.get_influencer → 200 with _HAPPY_INFLUENCER.

    WHAT: same pattern as the list fixture; AsyncMock returns a 200
          response carrying _HAPPY_INFLUENCER. Tests can override the
          mock's `id` via _patch_id_in_response if needed.
    WHEN: used by the happy-path by-id tests.
    WHY:  same — bypass real HTTP for deterministic + fast tests.
    """
    mock = AsyncMock(return_value=_make_mock_response(200, _HAPPY_INFLUENCER))
    monkeypatch.setattr(directory_client, "get_influencer", mock)
    return mock


# ===========================================================================
# GET /api/v1/influencers — Day-8 directory-RPC happy path
# ===========================================================================


def test_list_influencers_returns_envelope_with_list(client, mock_directory_list_happy):
    """Influencer list: envelope wraps a list[InfluencerResponse].

    WHAT: GETs the influencer list (directory mocked to 200 with one
          item) + asserts envelope shape + the data is a list.
    WHEN: happy-path with placeholder flag ON + directory reachable.
    WHY:  the influencer list is the catalog mobile renders on the
          chat tab landing; envelope-wrap is non-negotiable per A8.
    """
    response = client.get("/api/v1/influencers")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"success", "msg", "error", "data"}
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert len(body["data"]) == 1


def test_list_influencers_each_item_matches_influencer_response(
    client, mock_directory_list_happy,
):
    """Influencer list: every item has the InfluencerResponse shape.

    WHAT: GETs the list + iterates items, asserting each carries the
          required InfluencerResponse fields with correct types.
    WHEN: happy-path with placeholder flag ON + directory reachable.
    WHY:  mobile renders each catalog card by field (display_name +
          avatar + bio + archetype + is_nsfw); a missing field on one
          row = blank card. Guards against silent shape drift.
    """
    response = client.get("/api/v1/influencers")
    for influencer in response.json()["data"]:
        assert isinstance(influencer["id"], str)
        assert isinstance(influencer["display_name"], str)
        assert isinstance(influencer["bio"], str)
        assert isinstance(influencer["avatar_url"], str)
        assert isinstance(influencer["archetype"], str)
        assert isinstance(influencer["is_nsfw"], bool)
        assert isinstance(influencer["follower_count"], int)
        assert influencer["is_active"] in ("active", "discontinued")


def test_list_influencers_sets_cache_control_300s(client, mock_directory_list_happy):
    """Cache-Control max-age=300 on /api/v1/influencers (BLOCKER 6).

    WHAT: GETs the list + asserts the Cache-Control response header is
          present with `max-age=300`.
    WHEN: happy-path with placeholder flag ON + directory reachable.
    WHY:  Codex PR #97 BLOCKER 6 — the locked contract requires this
          header so mobile (+ any CDN in front) can cache the catalog
          for 5 minutes, reducing list-endpoint load by orders of
          magnitude under steady-state traffic. The Day-8 rewrite
          preserves this header even though the inner data is no
          longer canned.
    """
    response = client.get("/api/v1/influencers")
    assert response.status_code == 200
    cache_control = response.headers.get("cache-control", "")
    assert "max-age=300" in cache_control, (
        f"Expected 'max-age=300' in Cache-Control header; got: {cache_control!r}"
    )


def test_list_influencers_propagates_limit_offset_to_directory(
    client, mock_directory_list_happy,
):
    """Day-8: (limit, offset) query params reach the directory call.

    WHAT: GETs /api/v1/influencers?limit=42&offset=7 + asserts that the
          mocked directory_client.list_influencers was called with the
          same limit + offset kwargs.
    WHEN: pagination-aware mobile clients.
    WHY:  PR-B's wrapper is a 1:1 proxy on pagination per the DEP-013
          proposed RPC contract; the public-api surface must NOT
          silently rewrite or drop the params.
    """
    client.get("/api/v1/influencers?limit=42&offset=7")
    assert mock_directory_list_happy.await_count == 1
    call_kwargs = mock_directory_list_happy.await_args.kwargs
    assert call_kwargs["limit"] == 42
    assert call_kwargs["offset"] == 7


def test_list_influencers_default_pagination_when_no_params(
    client, mock_directory_list_happy,
):
    """Day-8: default pagination is (limit=20, offset=0) per the locked-+-proposed contract.

    WHAT: GETs /api/v1/influencers with NO query string + asserts the
          directory was called with limit=20, offset=0.
    WHEN: legacy mobile clients that don't send pagination.
    WHY:  defaults must match mobile's ChatRemoteDataSource.kt:50-70
          (limit=20 page size; offset=0 first page) so existing mobile
          builds keep working unchanged.
    """
    client.get("/api/v1/influencers")
    call_kwargs = mock_directory_list_happy.await_args.kwargs
    assert call_kwargs["limit"] == 20
    assert call_kwargs["offset"] == 0


def test_list_influencers_validates_limit_upper_bound(client):
    """Day-8: limit > 100 → envelope-shaped 400 (Pydantic Query validation).

    WHAT: GETs /api/v1/influencers?limit=999 + asserts the main.py
          RequestValidationError handler returns envelope-shaped 400
          with error="validation_failed".
    WHEN: misbehaving / probing clients.
    WHY:  bound the upstream load Session 4's directory takes; without
          a cap, one client could request the full 3.6k-row catalog
          in one shot and slow the directory for everyone.
    """
    response = client.get("/api/v1/influencers?limit=999")
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "validation_failed"


def test_list_influencers_validates_offset_non_negative(client):
    """Day-8: offset < 0 → envelope-shaped 400.

    WHAT: GETs /api/v1/influencers?offset=-1 + asserts the validation
          handler returns envelope-shaped 400.
    WHEN: misbehaving clients.
    WHY:  negative offset is meaningless for a 0-indexed catalog;
          fail fast at the API surface rather than passing a bogus
          value to the directory.
    """
    response = client.get("/api/v1/influencers?offset=-1")
    assert response.status_code == 400


def test_list_influencers_returns_503_when_flag_off(client_flag_off):
    """Influencer list: flag-off path returns 503 service_unavailable.

    WHAT: GETs the list with placeholder flag OFF; asserts envelope-
          shaped 503. No directory mock needed because the flag dep
          short-circuits before the handler body runs.
    WHEN: production-default state.
    WHY:  production-safety contract — the read path must not leak
          half-built behavior to real mobile traffic before Session 4's
          directory is fully ratified + the placeholder flag is
          intentionally flipped.
    """
    response = client_flag_off.get("/api/v1/influencers")
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"


# ===========================================================================
# GET /api/v1/influencers — Day-8 directory-RPC failure paths (J1-HOT)
# ===========================================================================


def test_list_influencers_directory_connect_error_maps_to_503(client, monkeypatch):
    """Day-8: directory ConnectError → envelope-shaped 503.

    WHAT: patches directory_client.list_influencers to raise
          httpx.ConnectError (simulating directory container missing);
          asserts public-api returns 503 with error="service_unavailable".
    WHEN: directory service down (rolling-update window, crash, etc.).
    WHY:  internal-RPC failures should NEVER leak raw upstream codes
          to mobile. The 503 envelope is the locked failure contract
          per A8 + Day-4C orchestrator-failure precedent.
    """
    monkeypatch.setattr(
        directory_client,
        "list_influencers",
        AsyncMock(side_effect=httpx.ConnectError("directory unreachable")),
    )
    response = client.get("/api/v1/influencers")
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"


def test_list_influencers_directory_timeout_maps_to_503(client, monkeypatch):
    """Day-8: directory TimeoutException → envelope-shaped 503.

    WHAT: patches directory_client.list_influencers to raise
          httpx.TimeoutException (simulating directory hung / slow);
          asserts public-api returns 503.
    WHEN: directory compute path stalled (DB connection-limit, etc.).
    WHY:  same A8 failure contract — envelope 503, not raw upstream
          timeout. Sentry tag `directory.call.failed=timeout`
          distinguishes from connect in dashboards.
    """
    monkeypatch.setattr(
        directory_client,
        "list_influencers",
        AsyncMock(side_effect=httpx.TimeoutException("directory hung")),
    )
    response = client.get("/api/v1/influencers")
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"


def test_list_influencers_directory_5xx_maps_to_503(client, monkeypatch):
    """Day-8: directory non-200 → envelope-shaped 503.

    WHAT: patches directory_client.list_influencers to return a 500
          response; asserts public-api returns 503.
    WHEN: directory-side internal error (unhandled exception, DB
          query crash, etc.).
    WHY:  same envelope contract; non-200 status from internal RPC
          gets mapped to service_unavailable so mobile pattern-matches
          a single failure code regardless of upstream detail.
    """
    monkeypatch.setattr(
        directory_client,
        "list_influencers",
        AsyncMock(return_value=_make_mock_response(500, {"error": "boom"})),
    )
    response = client.get("/api/v1/influencers")
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"


def test_list_influencers_directory_bad_shape_maps_to_503(client, monkeypatch):
    """Day-8: directory returns malformed JSON shape → envelope-shaped 503.

    WHAT: patches directory_client.list_influencers to return a 200
          with a malformed item (missing required field); asserts
          public-api returns 503 rather than crashing or passing the
          malformed shape to mobile.
    WHEN: directory-side schema drift (Session 4 adds/removes a field
          without coordinator review).
    WHY:  defense-in-depth on the contract boundary; the
          per-item InfluencerResponse(**item) validation in the route
          handler catches drift early + surfaces it via the locked
          envelope rather than corrupting mobile's parser.
    """
    bad_item = {"id": "x"}  # missing all the required fields
    monkeypatch.setattr(
        directory_client,
        "list_influencers",
        AsyncMock(return_value=_make_mock_response(200, [bad_item])),
    )
    response = client.get("/api/v1/influencers")
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"


# ===========================================================================
# GET /api/v1/influencers/trending
# ===========================================================================


def test_list_trending_influencers_returns_envelope_with_list(client):
    """Trending: envelope wraps a list.

    WHAT: GETs the trending endpoint + asserts envelope wraps a list.
    WHEN: happy-path with placeholder flag ON.
    WHY:  trending is a separate path per the contract (mobile renders
          a distinct carousel on the chat-tab landing); the path needs
          to exist + envelope-wrap as a sibling of the full list.
    """
    response = client.get("/api/v1/influencers/trending")
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


def test_list_trending_influencers_each_item_matches_response(client):
    """Trending: every item has the InfluencerResponse shape.

    WHAT: GETs trending + iterates items asserting required fields
          present.
    WHEN: happy-path with placeholder flag ON.
    WHY:  same per-row contract as the full list — even if the
          ranking differs at the real impl, the row shape stays
          identical because mobile reuses the catalog-card renderer.
    """
    response = client.get("/api/v1/influencers/trending")
    for influencer in response.json()["data"]:
        assert "id" in influencer
        assert "display_name" in influencer
        assert "is_active" in influencer


def test_list_trending_influencers_returns_503_when_flag_off(client_flag_off):
    """Trending: flag-off path returns 503.

    WHAT: GETs trending with placeholder flag OFF; asserts 503.
    WHEN: production-default state.
    WHY:  same production-safety gate as the full list endpoint.
    """
    response = client_flag_off.get("/api/v1/influencers/trending")
    assert response.status_code == 503


# ===========================================================================
# GET /api/v1/influencers/{id} — Day-8 directory-RPC happy path
# ===========================================================================


def test_get_influencer_returns_envelope(client, mock_directory_by_id_happy):
    """Single influencer: envelope success + InfluencerResponse in data.

    WHAT: GETs a single influencer by id (directory mocked to 200);
          asserts envelope success + non-null data.
    WHEN: happy-path with placeholder flag ON + directory reachable.
    WHY:  the detail screen drives the "Chat with this influencer"
          button → conversation create → message send flow; the
          envelope contract here is load-bearing for the entry point
          into a chat.
    """
    response = client.get("/api/v1/influencers/tara-real-influencer-id")
    body = response.json()
    assert body["success"] is True
    assert body["data"] is not None
    assert body["data"]["id"] == "tara-real-influencer-id"


def test_get_influencer_forwards_path_id_to_directory(
    client, mock_directory_by_id_happy,
):
    """Day-8: influencer_id path-param reaches the directory call.

    WHAT: GETs /api/v1/influencers/some-specific-id + asserts the
          mocked directory_client.get_influencer was called with the
          same influencer_id kwarg.
    WHEN: detail-screen open.
    WHY:  the wrapper is a 1:1 proxy on the id path-param; public-api
          must NOT silently rewrite or canonicalize the id, since
          mobile's local detail-vs-list join keys on the id verbatim.
    """
    client.get("/api/v1/influencers/some-specific-id")
    assert mock_directory_by_id_happy.await_count == 1
    assert mock_directory_by_id_happy.await_args.kwargs["influencer_id"] == "some-specific-id"


def test_get_influencer_returns_503_when_flag_off(client_flag_off):
    """Single influencer: flag-off path returns 503.

    WHAT: GETs a single influencer with placeholder flag OFF;
          asserts 503. Flag dep short-circuits before the handler
          body so no directory mock is needed.
    WHEN: production-default state.
    WHY:  same production-safety gate as the list endpoint.
    """
    response = client_flag_off.get("/api/v1/influencers/any-id")
    assert response.status_code == 503


# ===========================================================================
# GET /api/v1/influencers/{id} — Day-8 directory-RPC failure paths (J1-HOT)
# ===========================================================================


def test_get_influencer_directory_404_maps_to_envelope_404(client, monkeypatch):
    """Day-8: directory 404 → public-api envelope-shaped 404.

    WHAT: patches directory_client.get_influencer to return a 404
          response; asserts public-api returns 404 with the locked
          `not_found` error code in the envelope.
    WHEN: mobile opens a detail screen for an influencer that's been
          soft-deleted on the directory side (or never existed).
    WHY:  the locked error-codes table maps "no such resource" to the
          `not_found` code at HTTP 404; this is the ONE per-id failure
          mode that should NOT collapse to service_unavailable —
          mobile renders a distinct "influencer no longer exists"
          screen on 404.
    """
    monkeypatch.setattr(
        directory_client,
        "get_influencer",
        AsyncMock(return_value=_make_mock_response(404, {"error": "not_found"})),
    )
    response = client.get("/api/v1/influencers/gone-id")
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "not_found"
    assert body["success"] is False


def test_get_influencer_directory_connect_error_maps_to_503(client, monkeypatch):
    """Day-8: directory ConnectError on by-id → envelope-shaped 503.

    WHAT: patches directory_client.get_influencer to raise
          httpx.ConnectError (simulating directory container missing
          OR DNS miss against the Swarm overlay); asserts public-api
          returns 503 with the locked `service_unavailable` error code.
    WHEN: directory service down (rolling-update window, crash, etc.)
          on the by-id call path.
    WHY:  guards against raw upstream codes leaking to mobile on the
          detail-screen path. Per A8 + A16 every error must use the
          envelope shape; an unhandled ConnectError would surface as
          a 500 with FastAPI's default {"detail": "..."} body which
          breaks mobile's parser.
    """
    monkeypatch.setattr(
        directory_client,
        "get_influencer",
        AsyncMock(side_effect=httpx.ConnectError("directory unreachable")),
    )
    response = client.get("/api/v1/influencers/some-id")
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"


def test_get_influencer_directory_timeout_maps_to_503(client, monkeypatch):
    """Day-8: directory TimeoutException on by-id → envelope-shaped 503.

    WHAT: patches directory_client.get_influencer to raise
          httpx.TimeoutException (simulating directory compute path
          stalled past the 5s total / 2s connect timeout); asserts
          public-api returns 503.
    WHEN: directory hung on a slow DB query, blocked connection-pool
          slot, or unreachable underlying datastore.
    WHY:  same A8 envelope contract — envelope 503, not raw upstream
          timeout. Sentry tag `directory.call.failed=timeout`
          distinguishes from connect in on-call dashboards; the test
          asserts the wire-shape contract while the Sentry tag
          assertion lives in the directory_client unit-level coverage
          (see PR body Test Architecture note).
    """
    monkeypatch.setattr(
        directory_client,
        "get_influencer",
        AsyncMock(side_effect=httpx.TimeoutException("directory hung")),
    )
    response = client.get("/api/v1/influencers/some-id")
    assert response.status_code == 503


def test_get_influencer_directory_5xx_maps_to_503(client, monkeypatch):
    """Day-8: directory 500 on by-id → envelope-shaped 503.

    WHAT: patches directory_client.get_influencer to return a 500
          response (simulating directory-side unhandled exception,
          crashed DB query, etc.); asserts public-api returns 503.
    WHEN: directory's own request handler raised + FastAPI emitted
          a 500 to the public-api caller.
    WHY:  internal-RPC 5xx should NEVER leak verbatim to mobile —
          mobile's parser pattern-matches a single failure code
          (`service_unavailable`) for any directory-side trouble,
          rather than branching on every upstream 5xx variant. The
          test guards the collapse-to-503 boundary; absent the route
          handler's `if upstream.status_code != 200` check the 500
          would surface as a FastAPI-default 500 with non-envelope
          body.
    """
    monkeypatch.setattr(
        directory_client,
        "get_influencer",
        AsyncMock(return_value=_make_mock_response(500, {"error": "boom"})),
    )
    response = client.get("/api/v1/influencers/some-id")
    assert response.status_code == 503


def test_get_influencer_directory_bad_shape_maps_to_503(client, monkeypatch):
    """Day-8: directory returns malformed shape on by-id → envelope-shaped 503.

    WHAT: patches directory_client.get_influencer to return a 200
          response carrying a malformed body (a dict with only an `id`
          field, missing every other required `InfluencerResponse`
          field); asserts public-api returns 503 rather than crashing
          mid-Pydantic-decode or forwarding the malformed shape.
    WHEN: directory-side schema drift (Session 4 changes the field
          list of `InfluencerResponse` without coordinator review +
          a contract update).
    WHY:  defense-in-depth on the contract boundary. The route
          handler's `InfluencerResponse(**upstream.json())` triggers
          a Pydantic ValidationError on field-shape drift; the
          `except (ValueError, TypeError)` clause catches that +
          maps to the 503 envelope. Absent this guard, drift would
          either crash the worker (Pydantic V2 raises) or — worse —
          forward partial data to mobile whose parser then crashes
          downstream.
    """
    monkeypatch.setattr(
        directory_client,
        "get_influencer",
        AsyncMock(return_value=_make_mock_response(200, {"id": "x"})),
    )
    response = client.get("/api/v1/influencers/some-id")
    assert response.status_code == 503


# ===========================================================================
# BLOCKER 4 — write-set + admin stubs (locked paths, no 404s)
# ===========================================================================
#
# Every stub returns envelope-shaped 503 with error="service_unavailable"
# regardless of the placeholder flag (stubs ALWAYS 503 because they
# don't have a real body to gate yet). Tests use `client` since auth /
# flag state is irrelevant to the stub contract.


def _assert_stub_envelope(response, expected_status: int = 503) -> None:
    """Helper: assert envelope-shaped service_unavailable on a BLOCKER-4 stub.

    WHAT: validates an HTTP response represents a BLOCKER-4 stub —
          envelope success=False, error="service_unavailable",
          data=None, msg is non-empty + describes the stub.
    WHEN: called by every BLOCKER-4 stub test below.
    WHY:  centralizes the assertion so the BLOCKER-4 contract evolves
          in one place rather than ~8 per-test repeats.
    """
    assert response.status_code == expected_status
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "service_unavailable"
    assert body["data"] is None
    # The msg explicitly names the path so on-call sees what's stubbed.
    assert isinstance(body["msg"], str) and len(body["msg"]) > 0


def test_generate_prompt_stub_returns_503_envelope(client):
    """POST /api/v1/influencers/generate-prompt → 503 envelope.

    WHAT: POSTs an empty body + asserts the BLOCKER-4 stub envelope.
    WHEN: any client hitting the locked path before the create-flow
          real impl lands (Day 6-7 parity sprint).
    WHY:  Codex PR #97 BLOCKER 4 — the locked path must exist with the
          envelope shape; previously 404'd which mobile would treat
          as a routing bug.
    """
    _assert_stub_envelope(client.post("/api/v1/influencers/generate-prompt", json={}))


def test_validate_and_generate_metadata_stub_returns_503_envelope(client):
    """POST /api/v1/influencers/validate-and-generate-metadata → 503 envelope.

    WHAT: POSTs an empty body to step 2 of the 3-step creation flow;
          asserts the BLOCKER-4 stub envelope.
    WHEN: any client hitting the locked path before the real impl
          (Day 6-7 parity sprint).
    WHY:  same as BLOCKER-4 generate-prompt — locked path must exist
          with the envelope shape, not 404.
    """
    _assert_stub_envelope(
        client.post("/api/v1/influencers/validate-and-generate-metadata", json={}),
    )


def test_create_influencer_stub_returns_503_envelope(client):
    """POST /api/v1/influencers/create → 503 envelope.

    WHAT: POSTs an empty body to step 3 of the creation flow; asserts
          the BLOCKER-4 stub envelope.
    WHEN: any client hitting the locked path before the real impl.
    WHY:  same BLOCKER-4 contract — locked endpoint registered with
          envelope shape, not 404.
    """
    _assert_stub_envelope(client.post("/api/v1/influencers/create", json={}))


def test_edit_system_prompt_stub_returns_503_envelope(client):
    """PATCH /api/v1/influencers/{id}/system-prompt → 503 envelope.

    WHAT: PATCHes the Soul File (per B4 product term) on an influencer;
          asserts the BLOCKER-4 stub envelope.
    WHEN: any client hitting the locked path before the real impl
          (creator-side Soul File editing).
    WHY:  same BLOCKER-4 contract; the PATCH verb specifically matters
          because mobile's HTTP client only uses PATCH for partial
          updates per the chat-ai convention.
    """
    _assert_stub_envelope(
        client.patch("/api/v1/influencers/some-influencer-id/system-prompt", json={}),
    )


def test_generate_video_prompt_stub_returns_503_envelope(client):
    """POST /api/v1/influencers/{id}/generate-video-prompt → 503 envelope.

    WHAT: POSTs to the video-prompt helper; asserts BLOCKER-4 stub.
    WHEN: any client hitting the locked path before the real impl.
    WHY:  same BLOCKER-4 contract.
    """
    _assert_stub_envelope(
        client.post(
            "/api/v1/influencers/some-influencer-id/generate-video-prompt",
            json={},
        ),
    )


def test_delete_influencer_stub_returns_503_envelope(client):
    """DELETE /api/v1/influencers/{id} → 503 envelope.

    WHAT: DELETEs an influencer; asserts BLOCKER-4 stub envelope.
    WHEN: any client hitting the soft-delete endpoint before the real
          impl (which flips is_active='discontinued').
    WHY:  DELETE specifically routes via the soft-delete contract;
          locked path must exist with envelope shape.
    """
    _assert_stub_envelope(client.delete("/api/v1/influencers/some-influencer-id"))


def test_admin_ban_stub_returns_503_envelope(client):
    """POST /api/v1/admin/influencers/{id}/ban → 503 envelope.

    WHAT: POSTs to the admin-ban endpoint WITH X-Admin-Key header;
          asserts BLOCKER-4 stub envelope.
    WHEN: admin client hitting the locked path before the real impl.
    WHY:  admin path lives on a separate router (admin_influencer_router)
          per the contract; the X-Admin-Key header is contract-required
          so the test sends it even though the stub ignores it.
    """
    _assert_stub_envelope(
        client.post(
            "/api/v1/admin/influencers/some-influencer-id/ban",
            json={},
            headers={"X-Admin-Key": "test-admin-key"},
        ),
    )


def test_admin_unban_stub_returns_503_envelope(client):
    """POST /api/v1/admin/influencers/{id}/unban → 503 envelope.

    WHAT: POSTs to the admin-unban endpoint WITH X-Admin-Key header;
          asserts BLOCKER-4 stub envelope.
    WHEN: admin client hitting the locked path before the real impl.
    WHY:  symmetric to admin-ban; both admin paths must exist with
          the envelope shape.
    """
    _assert_stub_envelope(
        client.post(
            "/api/v1/admin/influencers/some-influencer-id/unban",
            json={},
            headers={"X-Admin-Key": "test-admin-key"},
        ),
    )


# ===========================================================================
# RELATED FILES:
#   conftest.py                            — fixtures
#   ../../app/api/influencer_routes.py     — handlers under test
#   ../../app/api/response_models.py       — InfluencerResponse shape
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
# ===========================================================================
