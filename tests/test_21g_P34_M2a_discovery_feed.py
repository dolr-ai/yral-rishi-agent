"""Phase 21γ.P34.M2a — Discovery Feed endpoint shell.

Minimum scope per the 2026-06-18 Session 6 tighten brief: SQL +
pin overlay + envelope. No Redis. No shuffle. No dedup.

  1. SOURCE-PIN — endpoint shape + Anshuman-envelope contract + the
     two M2a query-param additions (with_metadata, debug_source).
  2. BEHAVIOURAL — pin overlay + envelope shaping + pagination, all
     with a stubbed asyncpg pool. No real IO.
  3. SYNTHETIC LATENCY — `build_feed_page` against a 3,600-row stub
     pool. Reports the in-mem cost; real-world adds asyncpg
     round-trips (~5-15 ms).
"""

import asyncio
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

REPO = Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════════
# 1. SOURCE-PIN
# ══════════════════════════════════════════════════════════════════════


def test_discovery_route_path_and_query_params():
    src = (REPO / "app" / "routes" / "discovery.py").read_text()
    assert '"/api/v2/discovery/influencer-feed"' in src
    for k in ("offset", "limit", "with_metadata", "debug_source", "session_id"):
        assert k in src, f"query param missing: {k}"


def test_main_wires_discovery_router():
    src = (REPO / "app" / "main.py").read_text()
    assert "from routes.discovery import router as discovery_router" in src
    assert "app.include_router(discovery_router)" in src


def test_service_exposes_required_symbols():
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    for name in (
        "async def build_feed_page",
        "async def _read_active_pins",
        "async def _ordered_active_ids",
        "async def _hydrate_page",
        "def _shape_bot",
    ):
        assert name in src, f"missing symbol: {name}"


def test_envelope_shape_matches_anshuman_contract():
    """Design doc §8 — `FeedResponse{influencers[], total_count,
    offset, limit, has_more, feed_generated_at}`. Byte-compatible so
    mobile parsing doesn't change at cutover."""
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    for k in (
        '"influencers"',
        '"total_count"',
        '"offset"',
        '"limit"',
        '"has_more"',
        '"feed_generated_at"',
    ):
        assert k in src, f"envelope key missing: {k}"


def test_per_bot_keys_match_anshuman():
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    for k in (
        '"id"',
        '"name"',
        '"display_name"',
        '"avatar_url"',
        '"description"',
        '"category"',
        '"created_at"',
    ):
        assert k in src, f"per-bot key missing: {k}"


def test_with_metadata_surfaces_archetype_and_gender():
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    assert '"archetype"' in src
    assert '"gender"' in src


def test_debug_source_marker_present():
    """?debug_source=v2 → `{debug_source: "v2"}` in response. M7
    cutover-prep — lets Rishi confirm Motorola is hitting v2."""
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    assert '"debug_source"' in src
    assert '"v2"' in src


def test_endpoint_does_not_gate_on_jwt():
    """Cold-start: no JWT → same response. M4 will add personalization;
    M2a serves logged-out + logged-in identically."""
    src = (REPO / "app" / "routes" / "discovery.py").read_text()
    assert "get_current_user" not in src


def test_no_redis_dependency_on_m2a():
    """M2a brief: NO Redis client init / call. If a future PR sneaks
    a Redis import or `_get_redis()` back into the service module,
    mobile testing gets blocked on the cache being available. The
    docstring is allowed to MENTION Redis (rationale for the deferral
    to M2c); only the code is fenced."""
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    assert "redis.asyncio" not in src
    assert "_get_redis" not in src
    assert "aioredis" not in src


def test_session_id_accepted_but_unused():
    """Contract stability: mobile sends session_id now; backend ignores
    it on M2a, will consume it on M2b for per-session shuffle. The
    underscore-prefixed discard at the top of the route body is the
    deliberate signal."""
    src = (REPO / "app" / "routes" / "discovery.py").read_text()
    assert "session_id: str | None = Query(" in src
    assert "_ = session_id" in src


# ══════════════════════════════════════════════════════════════════════
# 2. BEHAVIOURAL — pin overlay + envelope shaping
# ══════════════════════════════════════════════════════════════════════


class _StubPool:
    """asyncpg-pool stand-in. Returns canned rows for fetch()."""

    def __init__(
        self,
        *,
        pin_rows: list[dict] | None = None,
        active_rows: list[dict] | None = None,
        hydration_rows: list[dict] | None = None,
    ):
        self.pin_rows = pin_rows or []
        self.active_rows = active_rows or []
        self.hydration_rows = hydration_rows or []

    async def fetch(self, sql, *args):
        if "trending_overrides" in sql:
            return self.pin_rows
        if "id = ANY" in sql:
            return self.hydration_rows
        if "FROM ai_influencers" in sql and "is_active" in sql:
            return self.active_rows
        return []


def test_shape_bot_envelope_minimal():
    from services.discovery_feed import _shape_bot

    row = {
        "id": "abc",
        "name": "tara",
        "display_name": "Tara",
        "avatar_url": "https://x/t.jpg",
        "description": "AI companion",
        "category": "Lifestyle",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "archetype": "companion",
        "gender": "female",
    }
    shaped = _shape_bot(row, with_metadata=False)
    assert shaped["id"] == "abc"
    assert shaped["display_name"] == "Tara"
    assert shaped["created_at"].startswith("2026-01-01")
    assert "archetype" not in shaped
    assert "gender" not in shaped


def test_shape_bot_envelope_with_metadata():
    from services.discovery_feed import _shape_bot

    row = {
        "id": "abc",
        "name": "tara",
        "display_name": "Tara",
        "avatar_url": "",
        "description": "",
        "category": "Lifestyle",
        "created_at": None,
        "archetype": "companion",
        "gender": "female",
    }
    shaped = _shape_bot(row, with_metadata=True)
    assert shaped["archetype"] == "companion"
    assert shaped["gender"] == "female"


def test_shape_bot_unknown_metadata_defaults():
    """Pre-M1-classification bots have archetype/gender = 'unknown' in
    the DB. Shape must surface that exactly (not None / empty) so
    debug UI doesn't show blank cells."""
    from services.discovery_feed import _shape_bot

    row = {
        "id": "abc",
        "name": "tara",
        "display_name": "Tara",
        "avatar_url": "",
        "description": "",
        "category": "",
        "created_at": None,
    }
    shaped = _shape_bot(row, with_metadata=True)
    assert shaped["archetype"] == "unknown"
    assert shaped["gender"] == "unknown"


def test_build_feed_page_basic_no_pins(monkeypatch):
    """End-to-end on stub pool — no pins, 20 active bots, page 0/20."""
    from services import discovery_feed

    active = [{"id": f"bot_{i}"} for i in range(20)]
    hydration = [
        {
            "id": f"bot_{i}",
            "name": f"bot{i}",
            "display_name": f"Bot {i}",
            "avatar_url": "",
            "description": "",
            "category": "Lifestyle",
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "archetype": "companion",
            "gender": "neutral",
        }
        for i in range(20)
    ]
    pool = _StubPool(active_rows=active, hydration_rows=hydration)

    payload = asyncio.run(
        discovery_feed.build_feed_page(pool, offset=0, limit=20, with_metadata=False)
    )
    assert len(payload["influencers"]) == 20
    assert payload["total_count"] == 20
    assert payload["offset"] == 0
    assert payload["limit"] == 20
    assert payload["has_more"] is False
    assert "feed_generated_at" in payload
    assert "debug_source" not in payload


def test_build_feed_page_pagination_has_more(monkeypatch):
    from services import discovery_feed

    active = [{"id": f"bot_{i:03d}"} for i in range(100)]
    hydration = [
        {
            "id": f"bot_{i:03d}",
            "name": f"bot{i}",
            "display_name": f"Bot {i}",
            "avatar_url": "",
            "description": "",
            "category": "Lifestyle",
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "archetype": "companion",
            "gender": "neutral",
        }
        for i in range(100)
    ]
    pool = _StubPool(active_rows=active, hydration_rows=hydration)
    payload = asyncio.run(
        discovery_feed.build_feed_page(pool, offset=0, limit=20, with_metadata=False)
    )
    assert payload["total_count"] == 100
    assert payload["has_more"] is True


def test_build_feed_page_pins_take_top_slots(monkeypatch):
    """Pins move to slot 1..N. Pre-existing copies in the SELECT are
    deduped so a pinned bot doesn't appear twice."""
    from services import discovery_feed

    active = [{"id": f"bot_{i:03d}"} for i in range(20)]
    # Pin bot_010 (which IS in the active set) + bot_500 (NOT in
    # active — should be silently dropped per the stale-pin-tolerance
    # contract).
    pin_rows = [
        {"influencer_id": "bot_010"},
        {"influencer_id": "bot_500"},
    ]
    hydration = [
        {
            "id": f"bot_{i:03d}",
            "name": f"bot{i}",
            "display_name": f"Bot {i}",
            "avatar_url": "",
            "description": "",
            "category": "Lifestyle",
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "archetype": "companion",
            "gender": "neutral",
        }
        for i in range(20)
    ]
    pool = _StubPool(pin_rows=pin_rows, active_rows=active, hydration_rows=hydration)
    payload = asyncio.run(
        discovery_feed.build_feed_page(pool, offset=0, limit=20, with_metadata=False)
    )
    ids = [b["id"] for b in payload["influencers"]]
    # bot_010 lifted to position 0
    assert ids[0] == "bot_010"
    # No duplicate
    assert ids.count("bot_010") == 1
    # Stale pin bot_500 silently dropped (FK protects most cases, but
    # the request-path code must tolerate this gracefully).
    assert "bot_500" not in ids


def test_debug_source_marker_in_payload(monkeypatch):
    from services import discovery_feed

    active = [{"id": "bot_001"}]
    hydration = [
        {
            "id": "bot_001",
            "name": "bot1",
            "display_name": "Bot 1",
            "avatar_url": "",
            "description": "",
            "category": "Lifestyle",
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "archetype": "companion",
            "gender": "neutral",
        }
    ]
    pool = _StubPool(active_rows=active, hydration_rows=hydration)
    payload = asyncio.run(
        discovery_feed.build_feed_page(
            pool,
            offset=0,
            limit=20,
            with_metadata=False,
            debug_source=True,
        )
    )
    assert payload.get("debug_source") == "v2"


def test_pin_read_failure_does_not_kill_request(monkeypatch):
    """Migration 041 missing on a fresh node ⇒ pin read raises. M2a
    must degrade open (no pin overlay) and still serve the feed."""
    from services import discovery_feed

    class _BrokenPinPool(_StubPool):
        async def fetch(self, sql, *args):
            if "trending_overrides" in sql:
                raise Exception("relation trending_overrides does not exist")
            return await super().fetch(sql, *args)

    active = [{"id": "bot_001"}]
    hydration = [
        {
            "id": "bot_001",
            "name": "bot1",
            "display_name": "Bot 1",
            "avatar_url": "",
            "description": "",
            "category": "Lifestyle",
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "archetype": "companion",
            "gender": "neutral",
        }
    ]
    pool = _BrokenPinPool(active_rows=active, hydration_rows=hydration)
    payload = asyncio.run(
        discovery_feed.build_feed_page(pool, offset=0, limit=20, with_metadata=False)
    )
    assert len(payload["influencers"]) == 1


# ══════════════════════════════════════════════════════════════════════
# 3. SYNTHETIC LATENCY BENCHMARK
# ══════════════════════════════════════════════════════════════════════


def test_synthetic_latency_under_load_3600_catalog(capsys):
    """Run `build_feed_page` 100x against a 3,600-row stub pool (the
    current prod catalog size). Reports the in-mem cost; real-world
    adds asyncpg round-trips (~5-15 ms typical).

    Add ~15-20 ms for real Postgres for a worst-case estimate. The
    M2a budget is p95 < 200 ms (M2c will tighten toward 100 ms with
    Redis caching)."""
    from services import discovery_feed

    CATALOG_SIZE = 3600
    active_rows = [{"id": f"bot_{i:05d}"} for i in range(CATALOG_SIZE)]
    hydration = [
        {
            "id": f"bot_{i:05d}",
            "name": f"bot{i}",
            "display_name": f"Bot {i}",
            "avatar_url": f"https://cdn.example/bot_{i}.jpg",
            "description": "A bot. " * 10,
            "category": ["Lifestyle", "Food & Drink", "Travel", "Fitness"][i % 4],
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "archetype": ["companion", "advisor", "entertainer", "educator", "creator"][
                i % 5
            ],
            "gender": ["male", "female", "neutral"][i % 3],
        }
        for i in range(CATALOG_SIZE)
    ]
    pin_rows = [{"influencer_id": f"bot_{i:05d}"} for i in (10, 100, 500, 1500, 3000)]

    class _BenchPool:
        async def fetch(self, sql, *args):
            if "trending_overrides" in sql:
                return pin_rows
            if "id = ANY" in sql:
                wanted = set(args[0])
                return [h for h in hydration if h["id"] in wanted]
            if "FROM ai_influencers" in sql:
                return active_rows
            return []

    pool = _BenchPool()
    samples_ms: list[float] = []
    ITERATIONS = 100
    for _ in range(ITERATIONS):
        t0 = time.perf_counter()
        asyncio.run(
            discovery_feed.build_feed_page(pool, offset=0, limit=20, with_metadata=True)
        )
        samples_ms.append((time.perf_counter() - t0) * 1000)

    p50 = statistics.median(samples_ms)
    p95 = sorted(samples_ms)[int(0.95 * len(samples_ms))]
    p99 = sorted(samples_ms)[int(0.99 * len(samples_ms))]
    avg = statistics.mean(samples_ms)

    with capsys.disabled():
        print(
            f"\n[M2a synthetic-latency] catalog={CATALOG_SIZE} "
            f"iterations={ITERATIONS} avg={avg:.2f}ms "
            f"p50={p50:.2f}ms p95={p95:.2f}ms p99={p99:.2f}ms"
        )

    # 50 ms ceiling on the in-mem path. Real-world adds ~15-20 ms of
    # asyncpg + JSON; total well under the 200 ms M2a budget.
    assert p95 < 50, f"M2a in-mem composer p95 = {p95:.2f}ms exceeds 50ms ceiling"
