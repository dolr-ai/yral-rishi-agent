"""Behavioural tests for /api/v1/influencers, driven as real HTTP requests.

`routes/influencers.py` was 24% covered and had three separate bugs land in
September — the deleted-name 409 (#513), the null-`reason` 500 (Sentry #602),
and an `unban` that resurrected a persona outside name uniqueness. Every one
was a request-level failure that no unit test could see.

These assert on responses: status codes, body shape, and the SQL actually
issued. Nothing here greps a source file.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from http_harness import FakePool, authenticated, use_pool


def _influencer_row(**over):
    row = {
        "id": "bot-1",
        "name": "zara",
        "display_name": "Zara",
        "avatar_url": "https://example.invalid/z.png",
        "description": "a travel guide",
        "category": "travel",
        "system_instructions": "be helpful",
        "personality_traits": {},
        "initial_greeting": "hi",
        "suggested_messages": ["a"],
        "is_active": "active",
        "is_nsfw": False,
        "parent_principal_id": "user-test-1",
        "source": "user_created",
        "created_at": "2026-09-01T00:00:00",
        "updated_at": "2026-09-01T00:00:00",
        "metadata": {},
        "skill_slug": None,
        "global_rule_overrides": {},
        "system_instructions_sections": None,
        "surface": "mobile",
        "conversation_count": 0,
        "deleted_at": None,
    }
    row.update(over)
    return row


@pytest.fixture
def client():
    from main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def pool(monkeypatch):
    from routes import influencers

    return use_pool(monkeypatch, influencers, FakePool())


# ─────────────────────────── catalogue listing ───────────────────────────


def test_unfiltered_catalogue_returns_rows(client, pool):
    """?surface omitted means the whole catalogue. This 400'd for every
    request in PR #503 because the guard tested `is not None` against a ""
    default — caught only by issuing the request."""
    pool.when("FROM ai_influencers", [_influencer_row()])
    pool.when("COUNT(*)", 1)

    r = client.get("/api/v1/influencers?limit=1")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["influencers"][0]["name"] == "zara"


def test_typod_surface_is_rejected_not_silently_unfiltered(client, pool):
    """The one failure this filter exists to prevent: a typo'd ?surface from
    amorae-web must NOT quietly serve the mainstream catalogue to the adult
    site."""
    r = client.get("/api/v1/influencers?surface=wbe")

    assert r.status_code == 400
    assert "Invalid surface" in r.json()["detail"]


@pytest.mark.parametrize("surface", ["mobile", "web", "both"])
def test_valid_surfaces_are_accepted(client, pool, surface):
    pool.when("FROM ai_influencers", [])
    pool.when("COUNT(*)", 0)

    assert client.get(f"/api/v1/influencers?surface={surface}").status_code == 200


# ─────────────────────────────── detail ──────────────────────────────────


def test_missing_influencer_is_404_not_500(client, pool):
    pool.when("FROM ai_influencers WHERE id", None)

    r = client.get("/api/v1/influencers/does-not-exist")

    assert r.status_code == 404


# ──────────────────────────────── auth ───────────────────────────────────


def test_delete_requires_a_token(client, pool):
    """No Authorization header must be 401 — the real get_current_user runs;
    only the signature check is stubbed elsewhere."""
    assert client.delete("/api/v1/influencers/bot-1").status_code == 401


def test_delete_rejects_a_malformed_authorization_header(client, pool):
    r = client.delete(
        "/api/v1/influencers/bot-1", headers={"Authorization": "Token abc"}
    )
    assert r.status_code == 401


def test_only_the_creator_can_delete(client, pool, monkeypatch):
    """Ownership is checked against the JWT subject. A stranger gets 403,
    and crucially the soft-delete must not run."""
    pool.when(
        "FROM ai_influencers WHERE id",
        _influencer_row(parent_principal_id="someone-else"),
    )

    with authenticated(monkeypatch, "user-test-1") as headers:
        r = client.delete("/api/v1/influencers/bot-1", headers=headers)

    assert r.status_code == 403
    assert not pool.ran("SET is_active = 'discontinued'"), "soft-delete ran despite 403"


def test_creator_can_delete_and_the_row_is_soft_deleted(client, pool, monkeypatch):
    """Soft delete must stamp deleted_at — that column is what releases the
    name for re-use (migration 055). Asserting on the SQL issued makes the
    #513 fix a behaviour, not a comment."""
    pool.when("FROM ai_influencers WHERE id", _influencer_row())

    with authenticated(monkeypatch, "user-test-1") as headers:
        r = client.delete("/api/v1/influencers/bot-1", headers=headers)

    assert r.status_code == 200, r.text
    assert pool.ran("SET is_active = 'discontinued'")
    assert pool.ran("deleted_at = NOW()"), "soft-delete did not release the name"


def test_deleting_a_missing_influencer_is_404(client, pool, monkeypatch):
    pool.when("FROM ai_influencers WHERE id", None)

    with authenticated(monkeypatch) as headers:
        r = client.delete("/api/v1/influencers/nope", headers=headers)

    assert r.status_code == 404


# ───────────────────────────── admin routes ──────────────────────────────


def test_ban_rejects_a_wrong_admin_key(client, pool):
    r = client.post("/api/v1/admin/influencers/bot-1", headers={"X-Admin-Key": "wrong"})
    assert r.status_code == 403


def test_ban_rejects_a_missing_admin_key(client, pool):
    assert client.post("/api/v1/admin/influencers/bot-1").status_code == 403


# ─────────────────────── name uniqueness on create ───────────────────────


def test_create_rejects_a_name_already_held_by_a_live_persona(
    client, pool, monkeypatch
):
    """409, not 500. The uniqueness check must also ignore deleted rows —
    `get_by_name` filters `deleted_at IS NULL` since migration 055."""
    pool.when("FROM ai_influencers WHERE name", _influencer_row())

    with authenticated(monkeypatch) as headers:
        r = client.post(
            "/api/v1/influencers/create",
            headers=headers,
            json={
                "name": "zara",
                "display_name": "Zara",
                "system_instructions": "be helpful and kind",
                "bot_principal_id": f"bot-{uuid.uuid4()}",
            },
        )

    assert r.status_code == 409, r.text
    assert "already taken" in r.json()["detail"]
    assert pool.ran("deleted_at IS NULL"), "uniqueness check ignored liveness"


def test_create_validates_the_name_shape(client, monkeypatch, pool):
    """Pydantic rejects before any DB work — an uppercase/spaced name is 422,
    and no query runs."""
    with authenticated(monkeypatch) as headers:
        r = client.post(
            "/api/v1/influencers/create",
            headers=headers,
            json={
                "name": "Not A Slug!",
                "display_name": "X",
                "system_instructions": "be helpful and kind",
                "bot_principal_id": "bot-9",
            },
        )

    assert r.status_code == 422
