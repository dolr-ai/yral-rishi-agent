"""Phase 21γ.P34.InboxSearch — search the user's existing
conversations by bot metadata.

Three categories:

  1. SOURCE-PIN — endpoint shape, JWT-required guard, SQL invariants
     (user_id filter, active-bots-only, pure SELECT).
  2. BEHAVIOURAL — `_build_subtitle`, `_shape_result`, `search()`
     empty-q handling, length cap.
  3. INTEGRATION — `search()` end-to-end with a stubbed pool.

No real DB or network — every IO is stubbed.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

REPO = Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════════
# 1. SOURCE-PIN
# ══════════════════════════════════════════════════════════════════════


def test_route_path_and_required_q_param():
    src = (REPO / "app" / "routes" / "inbox_search.py").read_text()
    assert '"/api/v2/chat/conversations/search"' in src
    assert "q: str = Query(..., max_length=100)" in src
    assert "limit: int = Query(20, ge=1, le=50)" in src


def test_route_requires_jwt():
    """Privacy hard requirement: JWT is required, 401 on missing.
    Source-pin the `get_current_user(request)` call which raises 401
    when the header is absent or malformed."""
    src = (REPO / "app" / "routes" / "inbox_search.py").read_text()
    assert "from auth import get_current_user" in src
    # Must be called BEFORE the pool acquire so unauth callers don't
    # even touch the DB.
    fn_start = src.index("async def conversations_search_endpoint")
    fn_end = len(src)
    fn_body = src[fn_start:fn_end]
    auth_pos = fn_body.index("get_current_user(request)")
    pool_pos = fn_body.index("get_pool()")
    assert auth_pos < pool_pos, "JWT check must run before pool acquire"


def test_main_wires_inbox_search_router():
    src = (REPO / "app" / "main.py").read_text()
    assert "from routes.inbox_search import router as inbox_search_router" in src
    assert "app.include_router(inbox_search_router)" in src


def test_service_module_exposes_required_symbols():
    src = (REPO / "app" / "services" / "inbox_search.py").read_text()
    for name in (
        "async def search",
        "def _build_subtitle",
        "def _shape_result",
        "_CONCAT_SQL",
        "_SEARCH_SQL",
        "_SIMILARITY_THRESHOLD",
    ):
        assert name in src, f"missing symbol: {name}"


def test_sql_filters_to_caller_user_id():
    """Privacy: SQL MUST filter `c.user_id = $1`. If a future PR
    drops this clause, every user sees every conversation. Critical."""
    src = (REPO / "app" / "services" / "inbox_search.py").read_text()
    assert "c.user_id = $1" in src


def test_sql_filters_to_active_bots():
    """Don't surface conversations whose bot was deactivated."""
    src = (REPO / "app" / "services" / "inbox_search.py").read_text()
    assert "i.is_active = 'active'" in src


def test_sql_filters_to_ai_chat_conversations():
    """Exclude human_chat conversations — inbox search is for the
    AI-bot inbox only. Human chats live in a separate UI."""
    src = (REPO / "app" / "services" / "inbox_search.py").read_text()
    assert "c.conversation_type = 'ai_chat'" in src


def test_sql_pure_select_no_writes():
    """Replica safety — same property as discovery_search +
    feed_ranker. Inbox search reads only."""
    src = (REPO / "app" / "services" / "inbox_search.py").read_text()
    sql_block = src[src.index("_SEARCH_SQL") : src.index("# ─── envelope")]
    for forbidden in (
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "MERGE ",
        "REFRESH MATERIALIZED",
        "TRUNCATE ",
        "ALTER ",
        "CREATE ",
        "DROP ",
    ):
        assert forbidden not in sql_block, (
            f"inbox_search SQL contains write keyword: {forbidden!r}"
        )


def test_sql_concat_matches_brief():
    """Brief specifies trgm match on `display_name || category ||
    archetype` (NOT the broader discovery_search concat which
    includes name + description). Inbox UI ranks by what the user
    actually sees in their chat list."""
    src = (REPO / "app" / "services" / "inbox_search.py").read_text()
    concat = src[src.index("_CONCAT_SQL = ") : src.index("_SEARCH_SQL = ")]
    assert "display_name" in concat
    assert "category" in concat
    assert "archetype" in concat
    # name + description NOT in the concat (intentional vs discovery_search)
    assert "i.name" not in concat
    assert "description" not in concat


def test_envelope_keys_symmetric_with_discovery_search():
    """Per-row envelope mirrors `/api/v2/discovery/search` exactly so
    mobile parses all three search-bar surfaces (discovery feed,
    discovery search, inbox search) through one DTO. PR #1197
    mobile review caught the original `influencer_*`-prefixed spec
    as an asymmetry — inbox rows rendered blank because the mobile
    parser only knew the unprefixed shape.

    Invoke `_shape_result` directly so we inspect the actual returned
    dict-key set, not the source text (the SQL alias reads are
    legit `row.get("influencer_display_name")` calls — they're
    row-dict keys, not wire keys)."""
    from services.inbox_search import _shape_result

    shaped = _shape_result(
        {
            "conversation_id": "c1",
            "influencer_id": "bot1",
            "influencer_display_name": "Tara",
            "influencer_avatar_url": "https://x/t.jpg",
            "archetype": "companion",
            "category": "Lifestyle",
            "last_message_at": datetime(2026, 6, 18, tzinfo=timezone.utc),
            "message_count": 1,
        }
    )
    assert set(shaped.keys()) == {
        "conversation_id",
        "influencer_id",
        "display_name",
        "avatar_url",
        "subtitle",
        "last_message_at",
        "message_count",
    }


def test_order_by_similarity_then_recency_then_volume():
    """Brief specifies ORDER BY similarity DESC, last_message_at
    DESC, message_count DESC. Verify the chain so a future PR can't
    silently swap the tiebreaker order. Anchor the search inside
    the _SEARCH_SQL block specifically — the module docstring also
    mentions ORDER BY rationale + we don't want to false-match there."""
    src = (REPO / "app" / "services" / "inbox_search.py").read_text()
    sql_block = src[src.index("_SEARCH_SQL = f") : src.index("# ─── envelope")]
    order_clause = sql_block[sql_block.index("ORDER BY") : sql_block.index("LIMIT $3")]
    assert "sim DESC" in order_clause
    assert "last_message_at DESC" in order_clause
    assert "message_count DESC" in order_clause
    assert order_clause.index("sim DESC") < order_clause.index("last_message_at DESC")
    assert order_clause.index("last_message_at DESC") < order_clause.index(
        "message_count DESC"
    )


# ══════════════════════════════════════════════════════════════════════
# 2. BEHAVIOURAL — helpers
# ══════════════════════════════════════════════════════════════════════


def test_build_subtitle_archetype_and_category():
    from services.inbox_search import _build_subtitle

    assert _build_subtitle("companion", "Lifestyle") == "companion · Lifestyle"


def test_build_subtitle_unknown_archetype_renders_literal():
    from services.inbox_search import _build_subtitle

    assert _build_subtitle("unknown", "Travel") == "unknown · Travel"


def test_build_subtitle_no_category_drops_separator():
    from services.inbox_search import _build_subtitle

    assert _build_subtitle("advisor", None) == "advisor"
    assert _build_subtitle("advisor", "") == "advisor"
    assert _build_subtitle("advisor", "   ") == "advisor"


def test_build_subtitle_none_archetype_falls_back():
    from services.inbox_search import _build_subtitle

    assert _build_subtitle(None, "Lifestyle") == "unknown · Lifestyle"
    assert _build_subtitle(None, None) == "unknown"


def test_shape_result_full_row():
    """SQL row uses prefixed aliases (`influencer_display_name`) as
    asyncpg row-dict keys; wire shape exposes the unprefixed
    discovery-search-symmetric names (`display_name`)."""
    from services.inbox_search import _shape_result

    row = {
        "conversation_id": "c1",
        "influencer_id": "bot1",
        "influencer_display_name": "Tara",
        "influencer_avatar_url": "https://x/t.jpg",
        "archetype": "companion",
        "category": "Lifestyle",
        "last_message_at": datetime(2026, 6, 18, tzinfo=timezone.utc),
        "message_count": 42,
    }
    shaped = _shape_result(row)
    assert shaped["conversation_id"] == "c1"
    assert shaped["influencer_id"] == "bot1"
    assert shaped["display_name"] == "Tara"
    assert shaped["avatar_url"] == "https://x/t.jpg"
    assert shaped["subtitle"] == "companion · Lifestyle"
    assert shaped["last_message_at"].startswith("2026-06-18")
    assert shaped["message_count"] == 42
    # Prefixed keys MUST NOT appear in the wire shape.
    assert "influencer_display_name" not in shaped
    assert "influencer_avatar_url" not in shaped
    assert "influencer_subtitle" not in shaped


def test_shape_result_handles_missing_aggregates():
    """If `agg` LATERAL produces no rows (conversation has zero
    messages, brand new), the COALESCE in SQL produces
    last_message_at=c.created_at + message_count=0. Shape must
    surface those without crashing."""
    from services.inbox_search import _shape_result

    row = {
        "conversation_id": "c1",
        "influencer_id": "bot1",
        "influencer_display_name": "X",
        "influencer_avatar_url": "",
        "archetype": "unknown",
        "category": "",
        "last_message_at": None,
        "message_count": 0,
    }
    shaped = _shape_result(row)
    assert shaped["message_count"] == 0
    assert shaped["last_message_at"] is None
    assert shaped["subtitle"] == "unknown"


# ══════════════════════════════════════════════════════════════════════
# 3. INTEGRATION — search() with stubbed pool
# ══════════════════════════════════════════════════════════════════════


class _StubPool:
    def __init__(self, rows=None, raises=False):
        self.rows = rows or []
        self.raises = raises
        self.fetched_args: list = []

    async def fetch(self, sql, *args):
        if self.raises:
            raise Exception("simulated DB error")
        self.fetched_args.append(args)
        return self.rows


def test_search_empty_q_short_circuits_to_empty_results():
    """Per brief: empty q returns {results:[], count:0}, NOT 422.
    Mobile sends q="" while debouncing."""
    from services import inbox_search

    pool = _StubPool()
    out = asyncio.run(inbox_search.search(pool, "user1", "", 20))
    assert out == {"results": [], "count": 0}
    # No DB call — short-circuit on empty q.
    assert pool.fetched_args == []


def test_search_whitespace_q_short_circuits():
    from services import inbox_search

    pool = _StubPool()
    out = asyncio.run(inbox_search.search(pool, "user1", "   ", 20))
    assert out == {"results": [], "count": 0}


def test_search_none_q_short_circuits():
    from services import inbox_search

    pool = _StubPool()
    out = asyncio.run(inbox_search.search(pool, "user1", None, 20))
    assert out == {"results": [], "count": 0}


def test_search_q_lowercased_before_query():
    """Match expression is LOWER(...); query side must lowercase too."""
    from services import inbox_search

    pool = _StubPool(rows=[])
    asyncio.run(inbox_search.search(pool, "user1", "TaRa SmItH", 10))
    # args = (user_id, q_lower, limit)
    assert pool.fetched_args[0][1] == "tara smith"


def test_search_q_length_capped_at_100():
    """Defense in depth: even if route Pydantic is bypassed, service
    caps q length."""
    from services import inbox_search

    pool = _StubPool(rows=[])
    asyncio.run(inbox_search.search(pool, "user1", "a" * 500, 10))
    assert len(pool.fetched_args[0][1]) == 100


def test_search_user_id_passed_as_first_arg():
    """Privacy: user_id binds $1 in the WHERE clause. Verify the
    service threads it through correctly so a refactor can't
    accidentally drop user-scoping."""
    from services import inbox_search

    pool = _StubPool(rows=[])
    asyncio.run(inbox_search.search(pool, "user-xyz", "tara", 5))
    assert pool.fetched_args[0][0] == "user-xyz"


def test_search_happy_path_shapes_envelope():
    from services import inbox_search

    rows = [
        {
            "conversation_id": "c1",
            "influencer_id": "bot1",
            "influencer_display_name": "Tara",
            "influencer_avatar_url": "https://x/t.jpg",
            "archetype": "companion",
            "category": "Lifestyle",
            "last_message_at": datetime(2026, 6, 18, tzinfo=timezone.utc),
            "message_count": 42,
            "sim": 0.71,
        },
        {
            "conversation_id": "c2",
            "influencer_id": "bot2",
            "influencer_display_name": "Tara's Cousin",
            "influencer_avatar_url": "",
            "archetype": "advisor",
            "category": "Travel",
            "last_message_at": datetime(2026, 6, 17, tzinfo=timezone.utc),
            "message_count": 5,
            "sim": 0.40,
        },
    ]
    pool = _StubPool(rows=rows)
    out = asyncio.run(inbox_search.search(pool, "user1", "tara", 20))
    assert out["count"] == 2
    first = out["results"][0]
    assert first["conversation_id"] == "c1"
    assert first["display_name"] == "Tara"
    assert first["avatar_url"] == "https://x/t.jpg"
    assert first["subtitle"] == "companion · Lifestyle"
    assert first["message_count"] == 42
    # Prefixed keys must not leak into the wire shape (PR #1197 fix).
    assert "influencer_subtitle" not in first
    assert "influencer_display_name" not in first
    assert "influencer_avatar_url" not in first


def test_search_no_results_returns_empty_envelope():
    from services import inbox_search

    pool = _StubPool(rows=[])
    out = asyncio.run(inbox_search.search(pool, "user1", "nonsensequery", 20))
    assert out == {"results": [], "count": 0}


def test_search_db_error_propagates():
    """Route translates to 503; service raises. Pin the propagation
    so a future fail-open refactor doesn't accidentally hide
    catastrophic errors as 200-empty."""
    from services import inbox_search

    pool = _StubPool(raises=True)
    try:
        asyncio.run(inbox_search.search(pool, "user1", "tara", 10))
    except Exception as e:
        assert "simulated DB error" in str(e)
    else:
        raise AssertionError("expected DB error to propagate")
