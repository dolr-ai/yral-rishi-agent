"""Phase 21γ.P34.M2c — Stage A scoring + `feed:global` Redis blob.

Three categories:

  1. SOURCE-PIN — defends weight constants, SQL shape, wiring.
  2. BEHAVIOURAL — exercises `compute_scores`, `_rank_percentile`,
     `_compute_momentum`, `rank_once` (with stubbed pool + redis).
  3. INTEGRATION — `rank_once` end-to-end with a stubbed pool.

Pure-Python `compute_scores` is the heart of the algorithm — the SQL
fetches signals, scoring blends them. Tests exercise the latter
without spinning a DB.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

REPO = Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════════
# 1. SOURCE-PIN
# ══════════════════════════════════════════════════════════════════════


def test_engagement_weights_match_design_doc():
    """Design §4: engagement = 0.40·popularity + 0.25·depth + 0.20·quality
    + 0.15·streak. Pin the literals so a future "let's tune this" PR
    has to consciously edit this test."""
    src = (REPO / "app" / "services" / "feed_ranker.py").read_text()
    assert "W_ENGAGEMENT_POPULARITY = 0.40" in src
    assert "W_ENGAGEMENT_DEPTH = 0.25" in src
    assert "W_ENGAGEMENT_QUALITY = 0.20" in src
    assert "W_ENGAGEMENT_STREAK = 0.15" in src


def test_discovery_weights_match_design_doc():
    """Design §4: discovery = 0.45·newness + 0.30·momentum
    + 0.15·underexposure + 0.10·quality."""
    src = (REPO / "app" / "services" / "feed_ranker.py").read_text()
    assert "W_DISCOVERY_NEWNESS = 0.45" in src
    assert "W_DISCOVERY_MOMENTUM = 0.30" in src
    assert "W_DISCOVERY_UNDEREXPOSURE = 0.15" in src
    assert "W_DISCOVERY_QUALITY = 0.10" in src


def test_engagement_weights_sum_to_one():
    from services.feed_ranker import (
        W_ENGAGEMENT_DEPTH,
        W_ENGAGEMENT_POPULARITY,
        W_ENGAGEMENT_QUALITY,
        W_ENGAGEMENT_STREAK,
    )

    total = (
        W_ENGAGEMENT_POPULARITY
        + W_ENGAGEMENT_DEPTH
        + W_ENGAGEMENT_QUALITY
        + W_ENGAGEMENT_STREAK
    )
    assert abs(total - 1.0) < 1e-9, f"engagement weights sum to {total}, not 1.0"


def test_discovery_weights_sum_to_one():
    from services.feed_ranker import (
        W_DISCOVERY_MOMENTUM,
        W_DISCOVERY_NEWNESS,
        W_DISCOVERY_QUALITY,
        W_DISCOVERY_UNDEREXPOSURE,
    )

    total = (
        W_DISCOVERY_NEWNESS
        + W_DISCOVERY_MOMENTUM
        + W_DISCOVERY_UNDEREXPOSURE
        + W_DISCOVERY_QUALITY
    )
    assert abs(total - 1.0) < 1e-9, f"discovery weights sum to {total}, not 1.0"


def test_blend_weights_sum_to_one():
    from services.feed_ranker import W_BLEND_DISCOVERY, W_BLEND_ENGAGEMENT

    total = W_BLEND_ENGAGEMENT + W_BLEND_DISCOVERY
    assert abs(total - 1.0) < 1e-9


def test_kill_switch_includes_feed_ranker():
    src = (REPO / "app" / "kill_switch.py").read_text()
    assert '"feed_ranker": "ENABLE_FEED_RANKER_LOOP"' in src


def test_kill_switch_feed_ranker_defaults_on():
    """Low-cost read-only job; ON by default. Verified by `is_enabled`
    returning True when no env var is set (and feed_ranker is NOT in
    `_DEFAULT_OFF_LOOPS`)."""
    import os

    from kill_switch import is_enabled

    for k in (
        "GEMINI_BACKGROUND_LOOPS_ENABLED",
        "ENABLE_FEED_RANKER_LOOP",
    ):
        os.environ.pop(k, None)
    assert is_enabled("feed_ranker") is True


def test_main_wires_feed_ranker_loop():
    src = (REPO / "app" / "main.py").read_text()
    assert "from services.feed_ranker import feed_ranker_loop" in src
    assert "feed_ranker_task = asyncio.create_task(feed_ranker_loop())" in src
    assert "feed_ranker_task.cancel()" in src
    assert "await feed_ranker_task" in src


def test_signals_sql_uses_only_select_no_writes():
    """Replica safety: every signal-fetching SQL chunk must be pure
    SELECT. No INSERT/UPDATE/DELETE/MERGE/REFRESH on the primary path.

    2026-06-18: the original mega-CTE was split into 4 chunks to fix
    the DiskFullError. Each chunk needs the same pure-SELECT property
    — pin them collectively by scanning all `_SQL_*` constants."""
    src = (REPO / "app" / "services" / "feed_ranker.py").read_text()
    sql_start = src.index("_SQL_BOTS")
    sql_end = src.index("async def _fetch_signals")
    sql_block = src[sql_start:sql_end]
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
            f"feed_ranker SQL contains write keyword: {forbidden!r}"
        )


def test_signals_sql_chunked_into_four_constants():
    """2026-06-18 fix for DiskFullError: the mega-CTE was split into
    4 separate queries (bots / recent_msgs / quality_latest / streaks)
    that get JOINed in Python. Pin the chunked shape so a future
    "let's combine these again" refactor has to consciously edit this
    test — and re-verify against the prod DiskFullError that motivated
    the split."""
    src = (REPO / "app" / "services" / "feed_ranker.py").read_text()
    for name in (
        "_SQL_BOTS",
        "_SQL_RECENT_MSGS",
        "_SQL_QUALITY_LATEST",
        "_SQL_STREAKS",
    ):
        assert name in src, f"missing chunked SQL constant: {name}"


def test_signals_sql_filters_to_active_bots():
    """The bots SELECT must filter on is_active='active' so deleted /
    inactive bots don't pollute the ranking."""
    src = (REPO / "app" / "services" / "feed_ranker.py").read_text()
    assert "i.is_active = 'active'" in src


def test_signals_sql_references_required_signal_sources():
    """Each of the 4 signal-source tables must appear in exactly one
    chunk. If a future PR drops a chunk, the corresponding signal
    silently defaults to 0/0.5 and the ranking degrades."""
    src = (REPO / "app" / "services" / "feed_ranker.py").read_text()
    # bots chunk references the matview
    assert "influencer_trending_stats" in src
    # recent_msgs chunk reads from messages + conversations
    assert "FROM messages m" in src
    assert "JOIN conversations c ON c.id = m.conversation_id" in src
    # quality_latest chunk reads from bot_quality_scores
    assert "FROM bot_quality_scores" in src
    # streaks chunk reads from conversations
    assert "FROM conversations" in src


def test_feed_global_key_matches_m2a_consumer():
    """The producer key MUST match what M2a's `_read_feed_global`
    consumes. If these drift, the M2a endpoint stays on its fallback
    path forever and nobody notices."""
    src_producer = (REPO / "app" / "services" / "feed_ranker.py").read_text()
    src_consumer = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    assert 'FEED_GLOBAL_KEY = "feed:global"' in src_producer
    assert 'FEED_GLOBAL_KEY = "feed:global"' in src_consumer


# ══════════════════════════════════════════════════════════════════════
# 2. BEHAVIOURAL — pure-Python scoring helpers
# ══════════════════════════════════════════════════════════════════════


def test_rank_percentile_distinct_values():
    from services.feed_ranker import _rank_percentile

    out = _rank_percentile([10.0, 20.0, 30.0, 40.0])
    assert out[10.0] == 0.0
    assert abs(out[20.0] - (1 / 3)) < 1e-9
    assert abs(out[30.0] - (2 / 3)) < 1e-9
    assert out[40.0] == 1.0


def test_rank_percentile_ties_share_position():
    from services.feed_ranker import _rank_percentile

    out = _rank_percentile([10.0, 10.0, 20.0])
    # Two distinct values ⇒ denominator 1; rank 0 / 1.
    assert out[10.0] == 0.0
    assert out[20.0] == 1.0


def test_rank_percentile_single_value_returns_05():
    """Single-value input ⇒ 0.5 for all (avoids 0/0 + bias toward
    "everyone tied at 1.0" which would let one signal dominate)."""
    from services.feed_ranker import _rank_percentile

    out = _rank_percentile([42.0, 42.0, 42.0])
    assert out[42.0] == 0.5


def test_momentum_zero_both_returns_zero():
    """Cold catalog: no recent + no prior = zero momentum."""
    from services.feed_ranker import _compute_momentum

    assert _compute_momentum(0, 0) == 0.0


def test_momentum_new_bot_with_traffic_returns_one():
    """Zero prior + positive recent = brand-new bot picking up
    traffic, gets max momentum signal."""
    from services.feed_ranker import _compute_momentum

    assert _compute_momentum(50, 0) == 1.0


def test_momentum_doubled_traffic_caps_at_one():
    """recent = 2× prior ⇒ ratio capped at 2.0, rescaled to 1.0."""
    from services.feed_ranker import _compute_momentum

    assert _compute_momentum(100, 50) == 1.0
    # Triple-traffic still caps at 1.0 (don't reward outliers more).
    assert _compute_momentum(150, 50) == 1.0


def test_momentum_flat_traffic_returns_half():
    """Same recent + prior = 1.0 ratio = 0.5 normalized."""
    from services.feed_ranker import _compute_momentum

    assert _compute_momentum(50, 50) == 0.5


# ─── compute_scores end-to-end ──────────────────────────────────────────


def _signal_row(
    bid,
    *,
    age_sec=86400,
    conv_count=10,
    msg_count=100,
    msgs_recent=10,
    msgs_prior=10,
    quality=0.7,
    streak=3,
    unique_users=5,
):
    return {
        "id": bid,
        "age_sec": float(age_sec),
        "conv_count": float(conv_count),
        "msg_count": float(msg_count),
        "msgs_recent": float(msgs_recent),
        "msgs_prior": float(msgs_prior),
        "quality": float(quality),
        "streak": float(streak),
        "unique_users": float(unique_users),
    }


def test_compute_scores_empty_returns_empty():
    from services.feed_ranker import compute_scores

    assert compute_scores([]) == []


def test_compute_scores_returns_sorted_descending():
    from services.feed_ranker import compute_scores

    # Three bots: one new + active, one old + popular, one mediocre.
    rows = [
        _signal_row(
            "new_active",
            age_sec=3600,  # 1h old
            conv_count=5,
            msg_count=50,
            msgs_recent=30,
            msgs_prior=5,  # momentum 1.0
            streak=7,
        ),
        _signal_row(
            "old_popular",
            age_sec=180 * 86400,  # 180 days old
            conv_count=500,
            msg_count=5000,
            msgs_recent=100,
            msgs_prior=100,  # flat
            streak=20,
        ),
        _signal_row(
            "mediocre",
            age_sec=30 * 86400,
            conv_count=10,
            msg_count=100,
            msgs_recent=10,
            msgs_prior=10,
            streak=2,
        ),
    ]
    scored = compute_scores(rows)
    assert len(scored) == 3
    # Sorted DESC by final score
    scores = [s for _, s in scored]
    assert scores == sorted(scores, reverse=True)
    # All bot_ids are unique + present
    assert sorted(b for b, _ in scored) == sorted(r["id"] for r in rows)


def test_compute_scores_new_active_beats_old_flat():
    """Discovery formula is weighted toward newness + momentum (45+30
    = 75% of discovery). A new, accelerating bot should outrank a
    saturated old one — that's the point of the design's
    'discovery-leaning' blend."""
    from services.feed_ranker import compute_scores

    rows = [
        _signal_row(
            "new_active",
            age_sec=86400,  # 1 day
            conv_count=5,
            msg_count=50,
            msgs_recent=30,
            msgs_prior=5,
            streak=5,
            quality=0.7,
        ),
        _signal_row(
            "old_flat",
            age_sec=365 * 86400,  # 1 year
            conv_count=500,
            msg_count=5000,
            msgs_recent=50,
            msgs_prior=50,
            streak=5,
            quality=0.7,
        ),
    ]
    scored = compute_scores(rows)
    by_id = dict(scored)
    assert by_id["new_active"] > by_id["old_flat"]


def test_compute_scores_quality_signal_breaks_ties():
    """All else equal, the higher-quality bot wins. Verifies the
    quality channel is wired in both engagement + discovery."""
    from services.feed_ranker import compute_scores

    rows = [
        _signal_row("high_q", quality=0.95, streak=5),
        _signal_row("low_q", quality=0.10, streak=5),
    ]
    scored = compute_scores(rows)
    assert scored[0][0] == "high_q"


def test_compute_scores_single_bot_emits_05_signals():
    """Single-bot catalog ⇒ every rank_percentile lookup returns 0.5
    ⇒ engagement = 0.5, discovery has newness = 1.0 (newest is also
    oldest). Score should be deterministic, no crash."""
    from services.feed_ranker import compute_scores

    rows = [_signal_row("only")]
    scored = compute_scores(rows)
    assert len(scored) == 1
    assert scored[0][0] == "only"
    # Score in [0, 1]
    assert 0.0 <= scored[0][1] <= 1.0


# ══════════════════════════════════════════════════════════════════════
# 3. INTEGRATION — rank_once with stubbed pool + redis
# ══════════════════════════════════════════════════════════════════════


class _StubPool:
    """SQL-aware stub.

    2026-06-18: feed_ranker._fetch_signals split into 4 chunked
    queries. The stub dispatches by SQL substring + serves each
    chunk from the same `rows` list (one fixture row per bot
    carrying all signal fields). Quality/streaks/recent rows are
    derived per-bot so the merge produces the same shape as the
    original mega-CTE returned."""

    def __init__(self, rows, raises=False):
        self.rows = rows
        self.raises = raises

    async def fetch(self, sql, *args):
        if self.raises:
            raise Exception("simulated DB error")
        # Empty catalog: same empty payload regardless of which chunk.
        if not self.rows:
            return []
        if "FROM ai_influencers" in sql and "is_active" in sql:
            # Bots chunk — id/age_sec/conv_count/msg_count
            return [
                {
                    "id": r["id"],
                    "age_sec": r["age_sec"],
                    "conv_count": r["conv_count"],
                    "msg_count": r["msg_count"],
                }
                for r in self.rows
            ]
        if "FROM messages m" in sql:
            return [
                {
                    "bot_id": r["id"],
                    "msgs_recent": r["msgs_recent"],
                    "msgs_prior": r["msgs_prior"],
                }
                for r in self.rows
            ]
        if "FROM bot_quality_scores" in sql:
            return [{"bot_id": r["id"], "quality": r["quality"]} for r in self.rows]
        if "FROM conversations" in sql:
            return [
                {
                    "bot_id": r["id"],
                    "streak": r["streak"],
                    "unique_users": r["unique_users"],
                }
                for r in self.rows
            ]
        return []


class _StubRedis:
    def __init__(self, raises=False):
        self.raises = raises
        self.written: dict = {}

    async def set(self, key, value, ex=None):
        if self.raises:
            raise Exception("simulated Redis error")
        self.written[key] = value


def _stub_redis(monkeypatch, redis_obj):
    from services import feed_ranker

    async def fake():
        return redis_obj

    monkeypatch.setattr(feed_ranker, "_get_redis", fake)


def test_rank_once_happy_path_writes_feed_global(monkeypatch):
    from services import feed_ranker

    rows = [
        _signal_row("a", conv_count=10, msg_count=100),
        _signal_row("b", conv_count=5, msg_count=50),
        _signal_row("c", conv_count=20, msg_count=200),
    ]
    pool = _StubPool(rows)
    redis = _StubRedis()
    _stub_redis(monkeypatch, redis)

    stats = asyncio.run(feed_ranker.rank_once(pool))
    assert stats["ok"] is True
    assert stats["bots"] == 3
    assert stats["stage"] == "complete"
    # Wrote to the canonical key
    assert "feed:global" in redis.written
    written = json.loads(redis.written["feed:global"])
    assert sorted(written) == ["a", "b", "c"]


def test_rank_once_signal_fetch_failure_reports_stage(monkeypatch):
    from services import feed_ranker

    pool = _StubPool([], raises=True)
    redis = _StubRedis()
    _stub_redis(monkeypatch, redis)

    stats = asyncio.run(feed_ranker.rank_once(pool))
    assert stats["ok"] is False
    assert stats["stage"] == "fetch"
    assert "feed:global" not in redis.written


def test_rank_once_redis_failure_reports_stage(monkeypatch):
    from services import feed_ranker

    rows = [_signal_row("a")]
    pool = _StubPool(rows)
    redis = _StubRedis(raises=True)
    _stub_redis(monkeypatch, redis)

    stats = asyncio.run(feed_ranker.rank_once(pool))
    assert stats["ok"] is False
    assert stats["stage"] == "write_failed"


def test_rank_once_empty_catalog_writes_empty_list(monkeypatch):
    """Edge case: no active bots. The blob should still be written
    (an empty list) so M2a's fallback path doesn't fire on a
    momentarily-empty catalog. compute_scores([]) = []; write
    proceeds with []."""
    from services import feed_ranker

    pool = _StubPool([])
    redis = _StubRedis()
    _stub_redis(monkeypatch, redis)

    stats = asyncio.run(feed_ranker.rank_once(pool))
    assert stats["ok"] is True
    assert stats["bots"] == 0
    assert json.loads(redis.written["feed:global"]) == []


def test_fetch_signals_dispatches_four_chunked_queries():
    """2026-06-18 DiskFullError fix: `_fetch_signals` must execute
    the 4 chunked queries separately (bots / recent_msgs / quality /
    streaks) instead of one mega-CTE. Pin the dispatch shape so a
    future refactor that recombines them has to update this test
    AND re-verify against the prod DiskFullError that motivated
    the split."""
    from services import feed_ranker

    captured: list[str] = []

    class _CapturingPool:
        async def fetch(self, sql, *args):
            captured.append(sql.strip().split("\n")[0])
            return []

    asyncio.run(feed_ranker._fetch_signals(_CapturingPool()))
    # 4 separate fetch() calls, one per chunk.
    assert len(captured) == 4


def test_fetch_signals_merges_chunks_by_bot_id():
    """End-to-end on the SQL-aware stub: the chunked queries return
    independent payloads keyed by bot_id; `_fetch_signals` merges
    them into one row per bot with all 9 signal fields. Pin the
    output shape so the Python merge can't silently drop a signal."""
    from services import feed_ranker

    rows = [
        _signal_row(
            "a",
            age_sec=3600,
            conv_count=10,
            msg_count=100,
            msgs_recent=20,
            msgs_prior=5,
            quality=0.8,
            streak=3,
            unique_users=7,
        ),
    ]
    pool = _StubPool(rows)
    merged = asyncio.run(feed_ranker._fetch_signals(pool))
    assert len(merged) == 1
    m = merged[0]
    assert m["id"] == "a"
    assert m["age_sec"] == 3600
    assert m["conv_count"] == 10
    assert m["msg_count"] == 100
    assert m["msgs_recent"] == 20
    assert m["msgs_prior"] == 5
    assert m["quality"] == 0.8
    assert m["streak"] == 3
    assert m["unique_users"] == 7


def test_fetch_signals_handles_bot_with_no_messages():
    """A brand-new bot has no rows in `messages` / `bot_quality_scores`
    / `conversations`. The chunk queries return empty for it; the
    Python merge must default those signals to 0 / 0.5 (matching the
    old COALESCE) so compute_scores still ranks it."""
    from services import feed_ranker

    class _SparsePool:
        async def fetch(self, sql, *args):
            if "FROM ai_influencers" in sql and "is_active" in sql:
                return [
                    {
                        "id": "newbot",
                        "age_sec": 60.0,
                        "conv_count": 0.0,
                        "msg_count": 0.0,
                    }
                ]
            # Other 3 chunks return empty — newbot has no rows there.
            return []

    merged = asyncio.run(feed_ranker._fetch_signals(_SparsePool()))
    assert len(merged) == 1
    m = merged[0]
    assert m["id"] == "newbot"
    # Quality defaults to 0.5 (mid-range, matching the old SQL COALESCE)
    assert m["quality"] == 0.5
    # All count-shaped signals default to 0.0
    for k in ("msgs_recent", "msgs_prior", "streak", "unique_users"):
        assert m[k] == 0.0, f"{k} did not default to 0.0"


def test_rank_once_respects_max_ranked_cap(monkeypatch):
    """If a future PR sets a 5000-bot catalog, the blob shouldn't
    blow past MAX_RANKED_BOTS. The cap is a defense against an
    accidental Redis-memory spike on a runaway catalog growth event."""
    from services import feed_ranker

    rows = [
        _signal_row(f"bot_{i:05d}") for i in range(feed_ranker.MAX_RANKED_BOTS + 100)
    ]
    pool = _StubPool(rows)
    redis = _StubRedis()
    _stub_redis(monkeypatch, redis)

    stats = asyncio.run(feed_ranker.rank_once(pool))
    assert stats["ok"] is True
    assert stats["bots"] == feed_ranker.MAX_RANKED_BOTS
    written = json.loads(redis.written["feed:global"])
    assert len(written) == feed_ranker.MAX_RANKED_BOTS
