"""Phase 21γ.P34.M2a — Discovery Feed endpoint shell.

Two categories of tests + a synthetic latency benchmark:

  1. SOURCE-PIN — defends the endpoint shape, fallback path, and
     Anshuman-envelope contract.
  2. BEHAVIOURAL — exercises the in-mem composer (pin overlay,
     seen-set dedup, per-session shuffle, pagination, envelope
     shaping) with stubbed Redis + Postgres. No real IO.
  3. SYNTHETIC LATENCY — measures `build_feed_page` against a stub
     pool returning 3,600 rows. Reports the p95 the M2a path can
     hit when DB latency is removed; the real wall-clock will be
     this + asyncpg round-trip time (typically +5-15ms).
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


def test_discovery_route_exposes_anshuman_compatible_path():
    src = (REPO / "app" / "routes" / "discovery.py").read_text()
    assert '"/api/v2/discovery/influencer-feed"' in src
    # Query params per design doc §8
    assert "offset" in src
    assert "limit" in src
    assert "with_metadata" in src
    assert "session_id" in src


def test_main_wires_discovery_router():
    src = (REPO / "app" / "main.py").read_text()
    assert "from routes.discovery import router as discovery_router" in src
    assert "app.include_router(discovery_router)" in src


def test_service_module_defines_required_symbols():
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    for name in (
        "async def build_feed_page",
        "async def _read_feed_global",
        "async def _read_pins",
        "async def _read_seen_set",
        "async def _record_seen",
        "async def _hydrate_bot_rows",
        "async def _fallback_active_bot_ids",
        "def _apply_pin_overlay",
        "def _shuffle_for_session",
        "def _shape_bot",
        "FEED_GLOBAL_KEY",
        "SEEN_SET_PREFIX",
    ):
        assert name in src, f"missing symbol: {name}"


def test_envelope_shape_matches_design_doc_contract():
    """Design doc §8 — `FeedResponse{influencers[], total_count, offset,
    limit, has_more, feed_generated_at}`. The shape MUST stay byte-
    compatible so mobile parsing doesn't change at cutover."""
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
    """Per-bot subset shipped to mobile — id/name/display_name/
    avatar_url/description/category/created_at."""
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
    """?with_metadata=true exposes the M1 columns + the rank_source
    tag so Rishi can sanity-check from a browser."""
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    assert '"archetype"' in src
    assert '"gender"' in src
    assert '"rank_source"' in src


def test_dormant_first_fallback_documented():
    """Design property: feed:global Redis miss → SELECT fallback so
    M2a is usable without M2c. If a future PR removes the fallback,
    mobile e2e testing breaks."""
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    assert "_fallback_active_bot_ids" in src
    assert 'rank_source = "fallback_select"' in src


def test_endpoint_does_not_require_jwt():
    """Cold-start path: no JWT → cold-start global feed. Don't gate
    behind get_current_user — mobile can hit this anonymously."""
    src = (REPO / "app" / "routes" / "discovery.py").read_text()
    # `_maybe_user_id` returns None on missing/invalid auth; no
    # call to `get_current_user` (which raises 401 on failure).
    assert "def _maybe_user_id" in src
    assert "get_current_user" not in src


# ══════════════════════════════════════════════════════════════════════
# 2. BEHAVIOURAL — composer correctness
# ══════════════════════════════════════════════════════════════════════


class _StubPool:
    """asyncpg-pool stand-in. Returns canned rows for fetch().
    `pin_rows` + `hydration_rows` + `fallback_rows` are configured
    per test."""

    def __init__(
        self,
        *,
        pin_rows: list[dict] | None = None,
        hydration_rows: list[dict] | None = None,
        fallback_rows: list[dict] | None = None,
    ):
        self.pin_rows = pin_rows or []
        self.hydration_rows = hydration_rows or []
        self.fallback_rows = fallback_rows or []
        self.queries: list[str] = []

    async def fetch(self, sql, *args):
        self.queries.append(sql.strip().split("\n")[0])
        if "trending_overrides" in sql:
            return self.pin_rows
        if "id = ANY" in sql:
            return self.hydration_rows
        if "FROM ai_influencers" in sql and "is_active" in sql:
            return self.fallback_rows
        return []


def _stub_redis_off(monkeypatch):
    """Force `discovery_feed._get_redis` to return None so Redis paths
    degrade open + fallback runs."""
    from services import discovery_feed

    async def fake():
        return None

    monkeypatch.setattr(discovery_feed, "_get_redis", fake)


def test_pin_overlay_inserts_at_rank_slots():
    """Pin at rank 1 lands at index 0; rank 3 lands at index 2 of
    the post-pin list; pre-existing occupant is pushed down."""
    from services.discovery_feed import _apply_pin_overlay

    base = ["a", "b", "c", "d", "e"]
    pins = [
        {"influencer_id": "P1", "pinned_rank": 1},
        {"influencer_id": "P2", "pinned_rank": 3},
    ]
    all_known = set(base) | {"P1", "P2"}
    result = _apply_pin_overlay(base, pins, all_known)
    assert result[0] == "P1"
    assert result[2] == "P2"
    # All original bots still present, in original relative order:
    remaining = [b for b in result if b not in ("P1", "P2")]
    assert remaining == base


def test_pin_overlay_deduplicates_pinned_bot_already_in_base():
    """If a pinned bot is also in the base ranking, the base copy
    is removed first so the bot doesn't appear twice."""
    from services.discovery_feed import _apply_pin_overlay

    base = ["a", "P1", "b", "c"]
    pins = [{"influencer_id": "P1", "pinned_rank": 1}]
    all_known = set(base)
    result = _apply_pin_overlay(base, pins, all_known)
    assert result.count("P1") == 1
    assert result[0] == "P1"


def test_pin_overlay_skips_unknown_bots():
    """Stale pin pointing at a deleted/inactive bot: silently drop
    rather than 5xx. The FK protects most cases but the loop between
    unpin and feed serve could see a stale pin."""
    from services.discovery_feed import _apply_pin_overlay

    base = ["a", "b", "c"]
    pins = [{"influencer_id": "GHOST", "pinned_rank": 1}]
    all_known = set(base)  # GHOST not in known set
    result = _apply_pin_overlay(base, pins, all_known)
    assert "GHOST" not in result
    assert result == base


def test_shuffle_is_deterministic_per_session():
    """Same session_id → same order. Different session_id → different
    order. Property is what gives stable pagination within a session
    + variety across sessions."""
    from services.discovery_feed import _shuffle_for_session

    ids = [f"bot_{i}" for i in range(50)]
    a1 = _shuffle_for_session(ids, "session_X")
    a2 = _shuffle_for_session(ids, "session_X")
    b = _shuffle_for_session(ids, "session_Y")
    assert a1 == a2
    assert a1 != b
    # Permutation property: same set, just different order.
    assert sorted(a1) == sorted(ids)


def test_shuffle_no_op_on_missing_session_id():
    """No session_id ⇒ stable input order. Lets the caller opt out
    of shuffle (e.g. for analytics replays) by omitting the param."""
    from services.discovery_feed import _shuffle_for_session

    ids = ["a", "b", "c"]
    assert _shuffle_for_session(ids, "") == ids


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
    shaped = _shape_bot(row, with_metadata=False, rank_source="fallback_select")
    assert shaped["id"] == "abc"
    assert shaped["display_name"] == "Tara"
    assert shaped["created_at"].startswith("2026-01-01")
    # No metadata keys when with_metadata=False
    assert "archetype" not in shaped
    assert "gender" not in shaped
    assert "rank_source" not in shaped


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
    shaped = _shape_bot(row, with_metadata=True, rank_source="feed_global")
    assert shaped["archetype"] == "companion"
    assert shaped["gender"] == "female"
    assert shaped["rank_source"] == "feed_global"
    # momentum + live are placeholders pending M2c / M3
    assert shaped["momentum"] is None
    assert shaped["live"] is None


def test_build_feed_page_fallback_when_redis_off(monkeypatch):
    """Smoke test of the full request path with Redis disabled.
    Validates the rank_source flips to fallback_select + the
    envelope is correctly shaped."""
    from services import discovery_feed

    _stub_redis_off(monkeypatch)

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
            "is_active": "active",
        }
        for i in range(20)
    ]
    fallback = [{"id": f"bot_{i}"} for i in range(20)]
    pool = _StubPool(hydration_rows=hydration, fallback_rows=fallback)

    payload = asyncio.run(
        discovery_feed.build_feed_page(
            pool,
            offset=0,
            limit=20,
            with_metadata=True,
            session_id="sess1",
        )
    )
    assert len(payload["influencers"]) == 20
    assert payload["total_count"] == 20
    assert payload["offset"] == 0
    assert payload["limit"] == 20
    assert payload["has_more"] is False
    assert "feed_generated_at" in payload
    # rank_source surfaces fallback (Redis off ⇒ SELECT path)
    assert payload["influencers"][0]["rank_source"] == "fallback_select"


def test_build_feed_page_pagination_has_more(monkeypatch):
    from services import discovery_feed

    _stub_redis_off(monkeypatch)

    hydration_full = [
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
            "is_active": "active",
        }
        for i in range(100)
    ]
    fallback = [{"id": f"bot_{i:03d}"} for i in range(100)]
    pool = _StubPool(hydration_rows=hydration_full, fallback_rows=fallback)

    payload = asyncio.run(
        discovery_feed.build_feed_page(
            pool,
            offset=0,
            limit=20,
            with_metadata=False,
            session_id="sess1",
        )
    )
    assert payload["total_count"] == 100
    assert payload["has_more"] is True


# ══════════════════════════════════════════════════════════════════════
# 3. SYNTHETIC LATENCY BENCHMARK
# ══════════════════════════════════════════════════════════════════════


def test_synthetic_latency_under_load_3600_catalog(monkeypatch, capsys):
    """Run `build_feed_page` 100x against a 3,600-bot stub pool (the
    current prod catalog size). Report p50/p95/p99 of the in-mem +
    composition cost.

    What this measures:
      - feed:global Redis miss → fallback SELECT (stubbed instant)
      - pin overlay (small pin set: 5)
      - per-session shuffle of 3,600 ids (the SHA1-sort cost)
      - in-mem dedup + pagination
      - envelope shaping for the 50-bot page

    What this does NOT measure:
      - Real Postgres round-trip (~5-15ms typical for the ANY($1) query)
      - Real Redis round-trip (~1-3ms)
      - JSON serialization at the Starlette layer (~1ms)

    Add ~15-20ms to the reported p95 for a real-world estimate.
    The 100ms p95 budget includes those round-trips."""
    from services import discovery_feed

    _stub_redis_off(monkeypatch)

    CATALOG_SIZE = 3600
    hydration = [
        {
            "id": f"bot_{i:05d}",
            "name": f"bot{i}",
            "display_name": f"Bot {i}",
            "avatar_url": f"https://cdn.example/bot_{i}.jpg",
            "description": "A bot. " * 10,  # ~80 chars
            "category": ["Lifestyle", "Food & Drink", "Travel", "Fitness"][i % 4],
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "archetype": ["companion", "advisor", "entertainer", "educator", "creator"][
                i % 5
            ],
            "gender": ["male", "female", "neutral"][i % 3],
            "is_active": "active",
        }
        for i in range(CATALOG_SIZE)
    ]
    fallback = [{"id": h["id"]} for h in hydration]
    # 5 active pins distributed across the rank range
    pin_rows = [
        {"influencer_id": f"bot_{i:05d}", "pinned_rank": idx + 1}
        for idx, i in enumerate((10, 100, 500, 1500, 3000))
    ]

    class _BenchPool:
        """Like _StubPool but returns only the page slice for the
        hydration query, simulating asyncpg's WHERE id = ANY() behaviour."""

        def __init__(self):
            self.calls = 0

        async def fetch(self, sql, *args):
            self.calls += 1
            if "trending_overrides" in sql:
                return pin_rows
            if "id = ANY" in sql:
                # asyncpg gets the bot_id list as $1; filter the hydration
                # set to just those ids (preserves cardinality realism).
                wanted = set(args[0])
                return [h for h in hydration if h["id"] in wanted]
            if "FROM ai_influencers" in sql:
                return fallback
            return []

    pool = _BenchPool()

    samples_ms: list[float] = []
    ITERATIONS = 100
    for i in range(ITERATIONS):
        sid = f"session_{i % 10}"  # 10 distinct sessions across run
        t0 = time.perf_counter()
        asyncio.run(
            discovery_feed.build_feed_page(
                pool,
                offset=0,
                limit=50,
                with_metadata=True,
                session_id=sid,
            )
        )
        samples_ms.append((time.perf_counter() - t0) * 1000)

    p50 = statistics.median(samples_ms)
    p95 = sorted(samples_ms)[int(0.95 * len(samples_ms))]
    p99 = sorted(samples_ms)[int(0.99 * len(samples_ms))]
    avg = statistics.mean(samples_ms)

    # Print to capsys so the bench number shows in pytest -s output.
    with capsys.disabled():
        print(
            f"\n[M2a synthetic-latency] catalog={CATALOG_SIZE} "
            f"iterations={ITERATIONS} avg={avg:.2f}ms "
            f"p50={p50:.2f}ms p95={p95:.2f}ms p99={p99:.2f}ms"
        )

    # Generous upper bound — even on a slow CI box the in-mem
    # composition should fit comfortably under 50ms. The real-world
    # 100ms p95 budget includes ~15-20ms of network IO on top.
    assert p95 < 50, (
        f"M2a in-mem composer p95 = {p95:.2f}ms exceeds 50ms ceiling; "
        f"investigate the shuffle / hydration paths before adding M2b."
    )
