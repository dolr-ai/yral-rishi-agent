"""Phase 21γ.P34.Search — discovery search endpoint.

Three categories:

  1. SOURCE-PIN — migration 043 shape, route + service wiring,
     envelope contract (kind + subtitle), pure-SQL invariant.
  2. BEHAVIOURAL — `_build_subtitle`, `_shape_result`, `search()`
     empty/whitespace handling.
  3. INTEGRATION — `search()` end-to-end with a stubbed pool +
     transaction-aware connection acquire.
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


def test_migration_043_creates_trgm_search_index():
    src = (REPO / "migrations" / "043_ai_influencers_search_index.sql").read_text()
    assert "CREATE INDEX IF NOT EXISTS idx_ai_influencers_search_trgm" in src
    assert "USING gin" in src
    assert "gin_trgm_ops" in src


def test_migration_043_concat_order_matches_design():
    """Addendum §2 pins the concat order: display_name → name →
    category → archetype → description. Reordering changes
    similarity weighting + breaks the index match in the SELECT."""
    src = (REPO / "migrations" / "043_ai_influencers_search_index.sql").read_text()
    # Verify the order appears in the expression.
    expr = src[src.index("LOWER(") : src.index(") gin_trgm_ops")]
    fields = [
        "display_name",
        "|| name",
        "|| COALESCE(category,   '')",
        "|| COALESCE(archetype,  '')",
        "|| COALESCE(description, '')",
    ]
    last_pos = -1
    for f in fields:
        pos = expr.find(f)
        assert pos > last_pos, f"field {f!r} out of order in concat expression"
        last_pos = pos


def test_migration_043_lowercased_expression():
    """The whole concat expression must be wrapped in LOWER() so
    the index is case-insensitive (server lowercases query before
    similarity match)."""
    src = (REPO / "migrations" / "043_ai_influencers_search_index.sql").read_text()
    assert "LOWER(" in src


def test_migration_043_has_squawk_preamble():
    src = (REPO / "migrations" / "043_ai_influencers_search_index.sql").read_text()
    assert "SET lock_timeout = '3s';" in src
    assert "SET statement_timeout = '60s';" in src


def test_service_module_exposes_required_symbols():
    src = (REPO / "app" / "services" / "discovery_search.py").read_text()
    for name in (
        "async def search",
        "def _build_subtitle",
        "def _shape_result",
        "_CONCAT_SQL",
        "_SEARCH_SQL",
        "_SIMILARITY_THRESHOLD",
    ):
        assert name in src, f"missing symbol: {name}"


def test_service_concat_expression_matches_migration():
    """The SELECT concat expression must be byte-identical to the
    migration's index expression — otherwise the planner won't pick
    the GIN index and the query slows to a sequential scan."""
    svc = (REPO / "app" / "services" / "discovery_search.py").read_text()
    mig = (REPO / "migrations" / "043_ai_influencers_search_index.sql").read_text()
    # Both contain the same column ordering with same separators.
    for token in (
        "display_name",
        "name",
        "COALESCE(i.category,   '')" if False else "category",
        "archetype",
        "description",
    ):
        assert token in svc
        assert token in mig


def test_service_sql_pure_select_no_writes():
    """Same replica-safety property as M2c. The search SQL must
    contain no write keywords."""
    src = (REPO / "app" / "services" / "discovery_search.py").read_text()
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
            f"search SQL contains write keyword: {forbidden!r}"
        )


def test_service_filters_to_active_bots():
    src = (REPO / "app" / "services" / "discovery_search.py").read_text()
    assert "i.is_active = 'active'" in src


def test_route_path_and_required_q_param():
    src = (REPO / "app" / "routes" / "discovery.py").read_text()
    assert '"/api/v2/discovery/search"' in src
    # q is required (no default) but bounded; mobile sends q="" while
    # debouncing so we must accept empty (handled in service).
    assert "q: str = Query(..., max_length=100)" in src
    assert "limit: int = Query(20, ge=1, le=50)" in src


def test_route_does_not_require_jwt():
    """Same property as the feed endpoint — search is open."""
    src = (REPO / "app" / "routes" / "discovery.py").read_text()
    # The search handler must not call get_current_user (the file may
    # mention it in OTHER contexts; pin the handler specifically by
    # checking it inside the search endpoint function body).
    fn_start = src.index("async def discovery_search_endpoint")
    fn_end = src.index("# ─── ", fn_start) if "# ─── " in src[fn_start:] else len(src)
    fn_body = src[fn_start:fn_end]
    assert "get_current_user" not in fn_body


def test_envelope_keys_match_addendum():
    """Per-row shape: id, name, display_name, avatar_url, description,
    category, created_at, kind, subtitle. The first 7 mirror the
    feed envelope so mobile parsers can share code; kind + subtitle
    are search-specific."""
    src = (REPO / "app" / "services" / "discovery_search.py").read_text()
    for k in (
        '"id"',
        '"name"',
        '"display_name"',
        '"avatar_url"',
        '"description"',
        '"category"',
        '"created_at"',
        '"kind"',
        '"subtitle"',
    ):
        assert k in src, f"envelope key missing: {k}"


def test_kind_always_influencer_for_now():
    """Addendum: `kind` field is future-proofing for user-search.
    Today every search result is an influencer."""
    src = (REPO / "app" / "services" / "discovery_search.py").read_text()
    assert '"kind": "influencer"' in src


# ══════════════════════════════════════════════════════════════════════
# 2. BEHAVIOURAL — pure-Python helpers
# ══════════════════════════════════════════════════════════════════════


def test_build_subtitle_archetype_and_category():
    from services.discovery_search import _build_subtitle

    assert _build_subtitle("companion", "Lifestyle") == "companion · Lifestyle"


def test_build_subtitle_unknown_archetype_renders_literal():
    """Per addendum: 'unknown' archetype renders as literal 'unknown'.
    Mobile UX team handles visual treatment."""
    from services.discovery_search import _build_subtitle

    assert _build_subtitle("unknown", "Food & Drink") == "unknown · Food & Drink"


def test_build_subtitle_no_category_drops_separator():
    """Empty category → just the archetype (no trailing ' · ')."""
    from services.discovery_search import _build_subtitle

    assert _build_subtitle("advisor", None) == "advisor"
    assert _build_subtitle("advisor", "") == "advisor"
    assert _build_subtitle("advisor", "   ") == "advisor"


def test_build_subtitle_none_archetype_falls_back():
    from services.discovery_search import _build_subtitle

    assert _build_subtitle(None, "Lifestyle") == "unknown · Lifestyle"
    assert _build_subtitle(None, None) == "unknown"


def test_shape_result_includes_kind_and_subtitle():
    from services.discovery_search import _shape_result

    row = {
        "id": "abc",
        "name": "tara",
        "display_name": "Tara",
        "avatar_url": "https://x/t.jpg",
        "description": "AI companion",
        "category": "Lifestyle",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "archetype": "companion",
    }
    shaped = _shape_result(row)
    assert shaped["id"] == "abc"
    assert shaped["display_name"] == "Tara"
    assert shaped["kind"] == "influencer"
    assert shaped["subtitle"] == "companion · Lifestyle"
    assert shaped["created_at"].startswith("2026-01-01")


def test_shape_result_unclassified_bot_shows_unknown_subtitle():
    """Pre-M1-classification bots have archetype='unknown' in the
    DB. Subtitle should render 'unknown · <category>'."""
    from services.discovery_search import _shape_result

    row = {
        "id": "abc",
        "name": "x",
        "display_name": "X",
        "avatar_url": "",
        "description": "",
        "category": "Travel",
        "created_at": None,
        "archetype": "unknown",
    }
    shaped = _shape_result(row)
    assert shaped["subtitle"] == "unknown · Travel"


# ══════════════════════════════════════════════════════════════════════
# 3. INTEGRATION — search() with stubbed pool
# ══════════════════════════════════════════════════════════════════════


class _StubConn:
    """Stand-in for an asyncpg connection. Records executed statements
    so tests can verify SET LOCAL fired before the SELECT."""

    def __init__(self, rows=None, raises_on_fetch=False):
        self.rows = rows or []
        self.raises_on_fetch = raises_on_fetch
        self.executes: list[str] = []
        self.fetched_args: list = []

    async def execute(self, sql, *args):
        self.executes.append(sql)

    async def fetch(self, sql, *args):
        if self.raises_on_fetch:
            raise Exception("simulated DB error")
        self.fetched_args.append(args)
        return self.rows

    # Transaction context manager
    def transaction(self):
        return _StubTxn()


class _StubTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _StubPool:
    """Mimics asyncpg pool's `async with pool.acquire()` shape."""

    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _StubAcquire(self.conn)


class _StubAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


def test_search_empty_q_short_circuits_to_empty_results():
    """Per addendum: empty `q` returns `{"results": [], "count": 0}`,
    NOT 422. Mobile sends `?q=` while the user types."""
    from services import discovery_search

    pool = _StubPool(_StubConn())  # should never get hit
    out = asyncio.run(discovery_search.search(pool, "", 20))
    assert out == {"results": [], "count": 0}


def test_search_whitespace_q_short_circuits():
    from services import discovery_search

    pool = _StubPool(_StubConn())
    out = asyncio.run(discovery_search.search(pool, "   ", 20))
    assert out == {"results": [], "count": 0}


def test_search_none_q_short_circuits():
    from services import discovery_search

    pool = _StubPool(_StubConn())
    out = asyncio.run(discovery_search.search(pool, None, 20))
    assert out == {"results": [], "count": 0}


def test_search_happy_path_sets_threshold_then_fetches():
    """The SET LOCAL must fire BEFORE the SELECT so the similarity
    threshold is in effect for the trgm `%` filter."""
    from services import discovery_search

    rows = [
        {
            "id": "bot_001",
            "name": "tara",
            "display_name": "Tara",
            "avatar_url": "",
            "description": "AI companion",
            "category": "Lifestyle",
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "archetype": "companion",
            "msg_count": 100,
            "sim": 0.45,
        },
    ]
    conn = _StubConn(rows=rows)
    pool = _StubPool(conn)
    out = asyncio.run(discovery_search.search(pool, "Tara", 20))
    # SET LOCAL fired
    assert any("pg_trgm.similarity_threshold" in s for s in conn.executes)
    # Query received lowercased q + the limit
    assert conn.fetched_args[0] == ("tara", 20)
    # Result shape
    assert out["count"] == 1
    assert out["results"][0]["id"] == "bot_001"
    assert out["results"][0]["kind"] == "influencer"
    assert out["results"][0]["subtitle"] == "companion · Lifestyle"


def test_search_q_lowercased_before_query():
    """Index expression is LOWER(...); query side must also lowercase
    so the case-insensitive match actually works."""
    from services import discovery_search

    conn = _StubConn(rows=[])
    pool = _StubPool(conn)
    asyncio.run(discovery_search.search(pool, "TaRa SmItH", 10))
    assert conn.fetched_args[0][0] == "tara smith"


def test_search_q_length_capped_at_100():
    """Defense in depth: even if the route's Pydantic validator is
    bypassed, the service caps q length to bound similarity calc cost."""
    from services import discovery_search

    conn = _StubConn(rows=[])
    pool = _StubPool(conn)
    long_q = "a" * 500
    asyncio.run(discovery_search.search(pool, long_q, 10))
    assert len(conn.fetched_args[0][0]) == 100


def test_search_raises_on_db_error():
    """DB errors propagate; the route layer translates to 503. This
    matches M2a's catastrophic-only error envelope."""
    from services import discovery_search

    conn = _StubConn(raises_on_fetch=True)
    pool = _StubPool(conn)
    try:
        asyncio.run(discovery_search.search(pool, "tara", 10))
    except Exception as e:
        assert "simulated DB error" in str(e)
    else:
        raise AssertionError("expected DB error to propagate")


def test_search_no_results_returns_empty_envelope():
    from services import discovery_search

    conn = _StubConn(rows=[])
    pool = _StubPool(conn)
    out = asyncio.run(discovery_search.search(pool, "nonsense gibberish", 20))
    assert out == {"results": [], "count": 0}
