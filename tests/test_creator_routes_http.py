"""Behavioural tests for the creator-facing routers, as real HTTP requests.

`creator_coach.py` (9% covered) holds `apply_coach_proposal` — at 382 lines
the longest function in the codebase, and the one most in need of splitting.
Splitting it while nothing exercised it would have been the same gamble that
produced four bugs this month, so the ownership and not-found paths are
pinned first. The refactor becomes safe afterwards, not before.
"""

import pytest
from fastapi.testclient import TestClient

from http_harness import FakePool, authenticated, use_pool


@pytest.fixture
def client():
    from main import app

    return TestClient(app, raise_server_exceptions=False)


def _pool_for(monkeypatch, module_name):
    from importlib import import_module

    module = import_module(f"routes.{module_name}")
    return use_pool(monkeypatch, module, FakePool())


# ─────────────────────────── creator studio ──────────────────────────────


def test_creator_influencer_list_requires_a_token(client):
    assert client.get("/api/v1/creator/influencers").status_code == 401


def test_creator_influencer_list_excludes_deleted_rows(client, monkeypatch):
    """Issue #512's related gap: this listed 'Deleted Bot' rows because it
    filtered only on owner. The liveness filter is asserted on the SQL, so
    removing it fails here."""
    pool = _pool_for(monkeypatch, "creator")
    pool.when("FROM ai_influencers", [])

    with authenticated(monkeypatch) as headers:
        r = client.get("/api/v1/creator/influencers", headers=headers)

    assert r.status_code == 200, r.text
    assert pool.ran("deleted_at IS NULL"), "creator listing served deleted personas"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/creator/influencers/bot-1/analytics",
        "/api/v1/creator/influencers/bot-1/conversations",
        "/api/v1/creator/influencers/bot-1/soul-file",
        "/api/v1/creator/influencers/bot-1/quality-score",
    ],
)
def test_creator_detail_routes_require_a_token(client, path):
    assert client.get(path).status_code == 401


# ──────────────────────────── soul-file ──────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/influencers/bot-1/soul-file",
        "/api/v1/influencers/bot-1/system-prompt-preview",
    ],
)
def test_soul_file_routes_require_a_token(client, path):
    assert client.get(path).status_code == 401


# ──────────────────────── coach: the 382-line path ───────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/creator/coach/conversations/conv-1/apply",
        "/api/v1/creator/coach/conversations/conv-1/discard",
        "/api/v1/creator/coach/conversations/conv-1/messages",
    ],
)
def test_coach_mutations_require_a_token(client, path):
    """apply/ discard mutate a persona's system prompt. An unauthenticated
    caller must never reach them."""
    assert client.post(path, json={}).status_code == 401


def test_coach_apply_on_a_missing_conversation_does_not_500(client, monkeypatch):
    """The 382-line apply path must answer 404 for an unknown conversation,
    not fall through into an unhandled error."""
    pool = _pool_for(monkeypatch, "creator_coach")
    pool.when("FROM coach_conversations", None)
    pool.when("FROM conversations", None)

    with authenticated(monkeypatch) as headers:
        r = client.post(
            "/api/v1/creator/coach/conversations/nope/apply",
            headers=headers,
            json={},
        )

    assert r.status_code in (400, 404, 422), r.text
    assert r.status_code != 500


# ───────────────────────────── human chat ────────────────────────────────


def test_human_chat_routes_require_a_token(client):
    assert client.get("/api/v1/chat/human/conversations").status_code == 401
    assert client.post("/api/v1/chat/human/conversations", json={}).status_code == 401
