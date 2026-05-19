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
    """Influencer list: envelope wraps a list[InfluencerResponse]."""
    response = client.get("/api/v1/influencers")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"success", "msg", "error", "data"}
    assert body["success"] is True
    assert isinstance(body["data"], list)


def test_list_influencers_each_item_matches_influencer_response(client):
    """Influencer list: every item has the InfluencerResponse shape."""
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
    """Codex PR #97 BLOCKER 6: the locked contract requires
    Cache-Control max-age=300 on the list endpoint so mobile (+ any CDN)
    can cache the catalog for 5 minutes."""
    response = client.get("/api/v1/influencers")
    assert response.status_code == 200
    cache_control = response.headers.get("cache-control", "")
    assert "max-age=300" in cache_control, (
        f"Expected 'max-age=300' in Cache-Control header; got: {cache_control!r}"
    )


def test_list_influencers_returns_503_when_flag_off(client_flag_off):
    """Influencer list: flag-off path returns 503."""
    response = client_flag_off.get("/api/v1/influencers")
    assert response.status_code == 503
    assert response.json()["error"] == "service_unavailable"


# ===========================================================================
# GET /api/v1/influencers/trending
# ===========================================================================


def test_list_trending_influencers_returns_envelope_with_list(client):
    """Trending: envelope wraps a list."""
    response = client.get("/api/v1/influencers/trending")
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


def test_list_trending_influencers_each_item_matches_response(client):
    """Trending: every item has the InfluencerResponse shape."""
    response = client.get("/api/v1/influencers/trending")
    for influencer in response.json()["data"]:
        assert "id" in influencer
        assert "display_name" in influencer
        assert "is_active" in influencer


def test_list_trending_influencers_returns_503_when_flag_off(client_flag_off):
    """Trending: flag-off path returns 503."""
    response = client_flag_off.get("/api/v1/influencers/trending")
    assert response.status_code == 503


# ===========================================================================
# GET /api/v1/influencers/{id}
# ===========================================================================


def test_get_influencer_returns_envelope(client):
    """Single influencer: envelope success + InfluencerResponse in data."""
    response = client.get("/api/v1/influencers/tara-test-id")
    body = response.json()
    assert body["success"] is True
    assert body["data"] is not None


def test_get_influencer_echoes_path_id(client):
    """Single influencer: returned id echoes the URL path so mobile's
    local detail-vs-list join works."""
    response = client.get("/api/v1/influencers/my-custom-id")
    assert response.json()["data"]["id"] == "my-custom-id"


def test_get_influencer_returns_503_when_flag_off(client_flag_off):
    """Single influencer: flag-off path returns 503."""
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
    """Helper: assert envelope-shaped service_unavailable on a BLOCKER-4 stub."""
    assert response.status_code == expected_status
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "service_unavailable"
    assert body["data"] is None
    # The msg explicitly names the path so on-call sees what's stubbed.
    assert isinstance(body["msg"], str) and len(body["msg"]) > 0


def test_generate_prompt_stub_returns_503_envelope(client):
    _assert_stub_envelope(client.post("/api/v1/influencers/generate-prompt", json={}))


def test_validate_and_generate_metadata_stub_returns_503_envelope(client):
    _assert_stub_envelope(
        client.post("/api/v1/influencers/validate-and-generate-metadata", json={}),
    )


def test_create_influencer_stub_returns_503_envelope(client):
    _assert_stub_envelope(client.post("/api/v1/influencers/create", json={}))


def test_edit_system_prompt_stub_returns_503_envelope(client):
    _assert_stub_envelope(
        client.patch("/api/v1/influencers/some-influencer-id/system-prompt", json={}),
    )


def test_generate_video_prompt_stub_returns_503_envelope(client):
    _assert_stub_envelope(
        client.post("/api/v1/influencers/some-influencer-id/generate-video-prompt", json={}),
    )


def test_delete_influencer_stub_returns_503_envelope(client):
    _assert_stub_envelope(client.delete("/api/v1/influencers/some-influencer-id"))


def test_admin_ban_stub_returns_503_envelope(client):
    _assert_stub_envelope(
        client.post(
            "/api/v1/admin/influencers/some-influencer-id/ban",
            json={},
            headers={"X-Admin-Key": "test-admin-key"},
        ),
    )


def test_admin_unban_stub_returns_503_envelope(client):
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
