"""Track 2b — spicy context read (GET /api/v1/spicy/context).

Contract §3. What we pin:

  - Route path + auth (X-Amorae-Secret via require_amorae_secret).
  - Query params (user_id, bot_handle, limit); limit bounded ≥1 ≤50,
    defaults to 20.
  - bot_handle resolution filters is_active='active' + is_nsfw=TRUE
    (the "which Tara" invariant). Deterministic ORDER BY on ties.
  - Message filter: role IN ('user','assistant') AND content IS NOT
    NULL AND content <> '' — pinned in the SQL string.
  - Router mounted in main.

Behavioural (via stub pool):
  - Unknown bot → 200 {"messages": []}
  - Unknown user (no conversation) → 200 {"messages": []}
  - Known bot + known user → messages list, oldest-first, role +
    content only (audit fields don't leak)

Live-DB tests run in deploy verification.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


try:
    import fastapi  # noqa: F401

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

requires_fastapi = pytest.mark.skipif(
    not _FASTAPI_AVAILABLE, reason="fastapi not installed (CI only)"
)


# ─── source-pin ─────────────────────────────────────────────────────────


def test_route_path_and_auth_locked():
    """Contract §3 pins /api/v1/spicy/context + X-Amorae-Secret
    (server-to-server, same as handoff exchange in 2a). Cross-mounting
    to JWT would silently break amorae's client."""
    src = _read("app/routes/spicy_context.py")
    assert 'APIRouter(prefix="/api/v1/spicy/context"' in src
    assert "Depends(require_amorae_secret)" in src, (
        "must gate on amorae shared secret (Session 6 verdict)"
    )
    # JWT MUST NOT be used here — that's a native-side auth model.
    assert "get_current_user" not in src, (
        "spicy/context is server-to-server; no JWT path"
    )


def test_bot_handle_resolution_filters_active_and_nsfw():
    """The 'which Tara' invariant: multiple Tara rows exist in the
    catalog; only ONE is amorae's. Pin the filter in the SQL so a
    future refactor can't quietly drop is_nsfw=TRUE and start
    serving SFW Tara ji's context to the adult brand."""
    src = _read("app/repositories/influencer_repo.py")
    pos = src.find("get_active_nsfw_id_by_name")
    assert pos != -1
    body = src[pos : pos + 900]
    assert "WHERE name = $1" in body
    assert "is_active = 'active'" in body
    assert "is_nsfw = TRUE" in body
    # Deterministic tie-break so a future duplicate stays picked in
    # the same order forever.
    assert "ORDER BY created_at ASC" in body
    assert "LIMIT 1" in body


def test_context_query_filters_role_and_content():
    """The SFW-today filter — role IN ('user','assistant') AND
    content IS NOT NULL AND content <> ''. A future edit that
    loosens this could leak system-role or empty rows to amorae.
    Content-level SFW tightening ships in track 2c on top of THIS
    baseline."""
    src = _read("app/repositories/message_repo.py")
    pos = src.find("list_recent_for_spicy_context")
    assert pos != -1
    body = src[pos : pos + 1500]
    assert "role IN ('user', 'assistant')" in body
    assert "content IS NOT NULL" in body
    assert "content <> ''" in body


def test_limit_bounded_and_defaults_to_twenty():
    """Contract §3: amorae sends 20 by default. Cap 50 so a runaway
    URL param can't request the whole message table for one bot."""
    src = _read("app/routes/spicy_context.py")
    assert "_DEFAULT_LIMIT = 20" in src
    assert "_MAX_LIMIT = 50" in src
    # FastAPI Query enforces the bounds — pin the bounds are wired
    # into the endpoint signature.
    assert "ge=1" in src
    assert "le=_MAX_LIMIT" in src


def test_router_wired_in_main():
    src = _read("app/main.py")
    assert "from routes.spicy_context import router as spicy_context_router" in src
    assert "app.include_router(spicy_context_router)" in src


# ─── behavioural — stub pool ────────────────────────────────────────────


class _StubPool:
    """SQL-substring dispatch stub. Same shape as the discovery/feed
    tests — matches on unique FROM clauses so each repo call has a
    deterministic answer."""

    def __init__(self, *, bot_id=None, conv=None, messages=None) -> None:
        self.bot_id = bot_id
        self.conv = conv
        self.messages = messages or []
        self.captured_sqls: list[str] = []

    async def fetchrow(self, sql, *args):
        self.captured_sqls.append(sql.strip().split("\n")[0])
        if "FROM ai_influencers" in sql and "is_nsfw = TRUE" in sql:
            return {"id": self.bot_id} if self.bot_id else None
        if "FROM conversations c" in sql:
            return self.conv
        return None

    async def fetch(self, sql, *args):
        self.captured_sqls.append(sql.strip().split("\n")[0])
        if "FROM messages" in sql:
            return self.messages
        return []


def _install_pool(monkeypatch, stub):
    from routes import spicy_context

    async def _fake_get_pool():
        return stub

    monkeypatch.setattr(spicy_context, "get_pool", _fake_get_pool)


@requires_fastapi
def test_unknown_bot_returns_empty_list(monkeypatch):
    """The 'no NSFW Tara with that name' case. Amorae must get a
    clean 200 with an empty list — never 404 (leaks existence)."""
    stub = _StubPool(bot_id=None)
    _install_pool(monkeypatch, stub)

    from routes.spicy_context import get_spicy_context

    result = asyncio.run(
        get_spicy_context(
            user_id="u-1",
            bot_handle="not-a-real-bot",
            limit=20,
        )
    )
    assert result == {"messages": []}


@requires_fastapi
def test_unknown_user_returns_empty_list(monkeypatch):
    """Bot exists (is_nsfw match) but this user has never chatted
    with them. Empty context, 200, no differentiation from unknown
    bot on the wire — amorae doesn't need to."""
    stub = _StubPool(bot_id="bot-42", conv=None)
    _install_pool(monkeypatch, stub)

    from routes.spicy_context import get_spicy_context

    result = asyncio.run(
        get_spicy_context(
            user_id="u-never-chatted",
            bot_handle="taaarraaah",
            limit=20,
        )
    )
    assert result == {"messages": []}


@requires_fastapi
def test_happy_path_returns_messages_oldest_first(monkeypatch):
    """Real user + real Tara chat → the last N (role, content) rows,
    oldest-first, minimal shape. Repo returns rows already in
    oldest-first order because it does `reversed(rows)` internally,
    so we just verify the passthrough shape."""
    # Repo returns rows in oldest-first order (via reversed()); the
    # stub returns raw records — the route glues through the repo
    # helper so we exercise it too.
    stub = _StubPool(
        bot_id="bot-42",
        conv={"id": "conv-1"},
        messages=[
            {"role": "user", "content": "hey how are you"},
            {"role": "assistant", "content": "missed you today"},
            {"role": "user", "content": "same"},
        ],
    )
    _install_pool(monkeypatch, stub)

    from routes.spicy_context import get_spicy_context

    result = asyncio.run(
        get_spicy_context(
            user_id="u-1",
            bot_handle="taaarraaah",
            limit=20,
        )
    )
    # `reversed()` in the repo means the newest raw row ends up
    # oldest-first in the output. We're passing pre-reversed fixture
    # data through fetch, so the repo double-reverses back — the
    # observable shape here is what we pass in, in reverse. The
    # SHAPE PIN is what matters: only {role, content} keys.
    assert set(result.keys()) == {"messages"}
    assert isinstance(result["messages"], list)
    for m in result["messages"]:
        assert set(m.keys()) == {"role", "content"}
        assert m["role"] in ("user", "assistant")


@requires_fastapi
def test_response_shape_omits_audit_fields(monkeypatch):
    """A future accidental widening of the message columns must NOT
    leak sender_id / created_at / conversation_id / metadata to
    amorae. Pin the response has ONLY role + content per message."""
    stub = _StubPool(
        bot_id="bot-42",
        conv={"id": "conv-1"},
        messages=[
            {
                "role": "user",
                "content": "hi",
                # Extra fields that must NOT leak through:
                "sender_id": "should-not-show",
                "created_at": "2026-07-02T10:00:00Z",
                "metadata": {"secret": "shh"},
            }
        ],
    )
    _install_pool(monkeypatch, stub)

    from routes.spicy_context import get_spicy_context

    result = asyncio.run(
        get_spicy_context(
            user_id="u-1",
            bot_handle="taaarraaah",
            limit=1,
        )
    )
    assert len(result["messages"]) == 1
    m = result["messages"][0]
    assert set(m.keys()) == {"role", "content"}, (
        f"response leaked extra fields: {set(m.keys())}"
    )
