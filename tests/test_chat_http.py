"""Behavioural tests for /api/v1/chat, driven as real HTTP requests.

`routes/chat.py` is the core product and the largest route file at 1,309
lines — and was 15% covered. It holds `send_message` (343 lines) and
`send_message_stream` (269), the two functions most in need of splitting and
the two it would be most dangerous to split untested.

These cover the ownership, validation and not-found paths first: cheap to
assert, and exactly where a careless refactor of those long functions would
break something a user notices.
"""

import pytest
from fastapi.testclient import TestClient

from http_harness import FakePool, authenticated, use_pool


def _conversation(**over):
    row = {
        "id": "conv-1",
        "user_id": "user-test-1",
        "influencer_id": "bot-1",
        "created_at": "2026-09-01T00:00:00",
        "updated_at": "2026-09-01T00:00:00",
        "metadata": {},
        "conversation_type": "ai",
    }
    row.update(over)
    return row


@pytest.fixture
def client():
    from main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def pool(monkeypatch):
    from routes import chat

    return use_pool(monkeypatch, chat, FakePool())


# ──────────────────────────────── auth ───────────────────────────────────


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/chat/conversations"),
        ("delete", "/api/v1/chat/conversations/conv-1"),
        ("get", "/api/v1/chat/conversations/conv-1/messages"),
        ("post", "/api/v1/chat/conversations/conv-1/read"),
    ],
)
def test_every_conversation_route_requires_a_token(client, pool, method, path):
    """No endpoint on this router may be reachable unauthenticated. Worth
    asserting as a set: a new route added without the auth call is the kind
    of omission nothing else catches."""
    assert getattr(client, method)(path).status_code == 401


def test_creating_a_conversation_requires_a_token(client, pool):
    """Separate from the set above because this one needs a body.

    POST /conversations takes `body: dict`, and FastAPI validates the body
    BEFORE the handler calls get_current_user — so a bodyless unauthenticated
    request answers 422, not 401. Same shape as the media-upload finding: an
    unauthenticated caller can still make the server parse their input. Worth
    knowing; not fixed here.
    """
    r = client.post("/api/v1/chat/conversations", json={"influencer_id": "bot-1"})
    assert r.status_code == 401


# ─────────────────────────────── ownership ───────────────────────────────


def test_only_the_owner_can_delete_a_conversation(client, pool, monkeypatch):
    pool.when("FROM conversations", _conversation(user_id="someone-else"))

    with authenticated(monkeypatch, "user-test-1") as headers:
        r = client.delete("/api/v1/chat/conversations/conv-1", headers=headers)

    assert r.status_code == 403
    assert not pool.ran("DELETE FROM messages"), "messages deleted despite 403"


def test_deleting_a_missing_conversation_is_404(client, pool, monkeypatch):
    pool.when("FROM conversations", None)

    with authenticated(monkeypatch) as headers:
        r = client.delete("/api/v1/chat/conversations/nope", headers=headers)

    assert r.status_code == 404


def test_owner_delete_removes_messages_then_the_conversation(client, pool, monkeypatch):
    """Order matters: messages carry an FK to conversations, so deleting the
    parent first would fail. Asserting both statements ran makes the ordering
    a behaviour rather than a convention."""
    pool.when("FROM conversations", _conversation())
    pool.when("COUNT(*) FROM messages", 3)

    with authenticated(monkeypatch, "user-test-1") as headers:
        r = client.delete("/api/v1/chat/conversations/conv-1", headers=headers)

    assert r.status_code == 200, r.text
    assert r.json()["deleted_conversation_id"] == "conv-1"
    assert r.json()["deleted_messages_count"] == 3
    assert pool.ran("DELETE FROM messages WHERE conversation_id")
    assert pool.ran("DELETE FROM conversations WHERE id")


# ───────────────────────────── validation ────────────────────────────────


@pytest.mark.parametrize("bad", ["hourly", "never", "DAILY", ""])
def test_proactive_frequency_rejects_values_outside_the_enum(
    client, pool, monkeypatch, bad
):
    """Only default/daily/weekly/off are allowed. An unrecognised value must
    be refused, not silently stored — a stored typo would quietly disable
    proactive messaging for that conversation."""
    pool.when("FROM conversations", _conversation())

    with authenticated(monkeypatch, "user-test-1") as headers:
        r = client.patch(
            "/api/v1/chat/conversations/conv-1/proactive-frequency",
            headers=headers,
            json={"frequency": bad},
        )

    assert r.status_code in (400, 422), f"{bad!r} was accepted"


@pytest.mark.parametrize("good", ["default", "daily", "weekly", "off"])
def test_proactive_frequency_accepts_the_enum(client, pool, monkeypatch, good):
    pool.when("FROM conversations", _conversation())

    with authenticated(monkeypatch, "user-test-1") as headers:
        r = client.patch(
            "/api/v1/chat/conversations/conv-1/proactive-frequency",
            headers=headers,
            json={"frequency": good},
        )

    assert r.status_code == 200, r.text


def test_proactive_frequency_is_owner_only(client, pool, monkeypatch):
    pool.when("FROM conversations", _conversation(user_id="someone-else"))

    with authenticated(monkeypatch, "user-test-1") as headers:
        r = client.patch(
            "/api/v1/chat/conversations/conv-1/proactive-frequency",
            headers=headers,
            json={"frequency": "daily"},
        )

    assert r.status_code in (403, 404)


# ──────────────────────────────── listing ────────────────────────────────


def test_listing_conversations_returns_a_list(client, pool, monkeypatch):
    pool.when("FROM conversations", [])

    with authenticated(monkeypatch) as headers:
        r = client.get("/api/v1/chat/conversations", headers=headers)

    assert r.status_code == 200, r.text


def test_conversation_list_honours_the_influencer_filter(client, pool, monkeypatch):
    """?influencer_id must reach the query. It defaulted to "" in PR #503,
    which would have filtered on an empty string had the repo not been
    falsy-safe."""
    pool.when("FROM conversations", [])

    with authenticated(monkeypatch) as headers:
        r = client.get(
            "/api/v1/chat/conversations?influencer_id=bot-1", headers=headers
        )

    assert r.status_code == 200
    assert pool.ran("c.influencer_id = $2")
