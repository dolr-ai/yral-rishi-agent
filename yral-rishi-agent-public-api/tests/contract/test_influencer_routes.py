# ---------------------------------------------------------------------------
# test_influencer_routes.py — contract tests for /api/v1/influencers/* +
# the BLOCKER-4 write-set + admin stubs.
#
# ⭐ START HERE: each test asserts envelope + response-model shape per
# interface-contracts/00-api-contract.md, OR the flag-off 503 gate on
# the read endpoints, OR the BLOCKER-4 service_unavailable stubs on
# the write + admin endpoints, OR the BLOCKER-6 Cache-Control header
# on the list endpoint.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# (Conftest provides `client` (flag ON) + `client_flag_off` (flag OFF).
# No imports needed for fixtures — pytest finds them by name match.)


# ===========================================================================
# GET /api/v1/influencers
# ===========================================================================


def test_list_influencers_returns_envelope_with_list(client):
    """Influencer list: envelope wraps a list[InfluencerResponse].

    WHAT: GETs the influencer list + asserts envelope shape + the data
          is a list.
    WHEN: happy-path with placeholder flag ON.
    WHY:  the influencer list is the catalog mobile renders on the
          chat tab landing; envelope-wrap is non-negotiable per A8.
    """
    response = client.get("/api/v1/influencers")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"success", "msg", "error", "data"}
    assert body["success"] is True
    assert isinstance(body["data"], list)


def test_list_influencers_each_item_matches_influencer_response(client):
    """Influencer list: every item has the InfluencerResponse shape.

    WHAT: GETs the list + iterates items, asserting each carries the
          required InfluencerResponse fields with correct types.
    WHEN: happy-path with placeholder flag ON.
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


def test_list_influencers_sets_cache_control_300s(client):
    """Cache-Control max-age=300 on /api/v1/influencers (BLOCKER 6).

    WHAT: GETs the list + asserts the Cache-Control response header is
          present with `max-age=300`.
    WHEN: happy-path with placeholder flag ON.
    WHY:  Codex PR #97 BLOCKER 6 — the locked contract requires this
          header so mobile (+ any CDN in front) can cache the catalog
          for 5 minutes, reducing list-endpoint load by orders of
          magnitude under steady-state traffic.
    """
    response = client.get("/api/v1/influencers")
    assert response.status_code == 200
    cache_control = response.headers.get("cache-control", "")
    assert "max-age=300" in cache_control, (
        f"Expected 'max-age=300' in Cache-Control header; got: {cache_control!r}"
    )


def test_list_influencers_returns_503_when_flag_off(client_flag_off):
    """Influencer list: flag-off path returns 503 service_unavailable.

    WHAT: GETs the list with placeholder flag OFF; asserts envelope-
          shaped 503.
    WHEN: production-default state.
    WHY:  production-safety contract — stub catalog must not leak to
          real mobile traffic before the Session 4 influencer-directory
          RPC is wired.
    """
    response = client_flag_off.get("/api/v1/influencers")
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
# GET /api/v1/influencers/{id}
# ===========================================================================


def test_get_influencer_returns_envelope(client):
    """Single influencer: envelope success + InfluencerResponse in data.

    WHAT: GETs a single influencer by id + asserts envelope success +
          non-null data.
    WHEN: happy-path with placeholder flag ON.
    WHY:  the detail screen drives the "Chat with this influencer"
          button → conversation create → message send flow; the
          envelope contract here is load-bearing for the entry point
          into a chat.
    """
    response = client.get("/api/v1/influencers/tara-test-id")
    body = response.json()
    assert body["success"] is True
    assert body["data"] is not None


def test_get_influencer_echoes_path_id(client):
    """Single influencer: returned id echoes the URL path.

    WHAT: GETs /influencers/my-custom-id + asserts the returned
          InfluencerResponse.id equals "my-custom-id".
    WHEN: happy-path with placeholder flag ON.
    WHY:  mobile's local detail-vs-list join keys on InfluencerResponse.id;
          if the stub returned a hardcoded fake id, mobile would fail
          to splice the detail into its catalog cache.
    """
    response = client.get("/api/v1/influencers/my-custom-id")
    assert response.json()["data"]["id"] == "my-custom-id"


def test_get_influencer_returns_503_when_flag_off(client_flag_off):
    """Single influencer: flag-off path returns 503.

    WHAT: GETs a single influencer with placeholder flag OFF;
          asserts 503.
    WHEN: production-default state.
    WHY:  same production-safety gate.
    """
    response = client_flag_off.get("/api/v1/influencers/any-id")
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
