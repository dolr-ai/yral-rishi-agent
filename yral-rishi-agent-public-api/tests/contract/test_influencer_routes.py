# ---------------------------------------------------------------------------
# test_influencer_routes.py — contract tests for /api/v1/influencers/* read set.
#
# ⭐ START HERE: each test asserts envelope + DTO shape per
# interface-contracts/00-api-contract.md, or the flag-off 503 gate.
#
# WHAT'S NOT TESTED HERE?
# The write set (create flow, system-prompt edit, admin ban/unban,
# delete) lands in Day 6-7's parity-sprint PR. Tests follow then.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# GET /api/v1/influencers
# ===========================================================================


def test_list_influencers_returns_envelope_with_list(client):
    """Influencer list: envelope wraps a list[InfluencerDto]."""
    response = client.get("/api/v1/influencers")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"success", "msg", "error", "data"}
    assert body["success"] is True
    assert isinstance(body["data"], list)


def test_list_influencers_each_item_matches_influencer_dto(client):
    """Influencer list: every item has the InfluencerDto shape."""
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


def test_list_trending_influencers_each_item_matches_dto(client):
    """Trending: every item has the InfluencerDto shape."""
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
    """Single influencer: envelope success + InfluencerDto in data."""
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
# RELATED FILES:
#   conftest.py                            — fixtures
#   ../../app/api/influencer_routes.py     — handlers under test
#   ../../app/api/dtos.py                  — InfluencerDto shape
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
# ===========================================================================
