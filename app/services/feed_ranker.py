"""Phase 21γ.P34.M2c — Stage A scoring + `feed:global` Redis blob.

Offline background job that computes a per-bot composite score and
writes the ranked bot_id list to Redis at `feed:global`. The M2a
endpoint reads that blob on every request; when present, it lifts
`rank_source` from `"fallback_select"` to `"feed_global"`. Mobile
sees no envelope change — only the order shifts.

## Formulas (design doc §4)

  engagement = 0.40·popularity + 0.25·depth_ratio
             + 0.20·quality    + 0.15·streak

  discovery  = 0.45·newness    + 0.30·momentum
             + 0.15·underexposure + 0.10·quality

The two scores are blended 50/50 to produce the final ranking. The
feed is discovery-leaning by design ("weighted toward FRESH" per the
design's comment block), so an even blend lets the discovery formula
dominate the order while still rewarding genuinely engaging bots.

## Signal sources

  popularity      ← influencer_trending_stats matview (conv + msg counts)
                    + unique-users JOIN on conversations
  depth_ratio     ← msg_count / max(conv_count, 1), rank-normalized
  quality         ← latest bot_quality_scores.score_overall per bot
  streak          ← MAX(conversations.current_streak_days) per bot
  newness         ← 1 - (age_sec / max_age_sec)
  momentum        ← msgs_last_7d / max(msgs_prev_7d, 1), capped + normalized
  underexposure   ← 1 - msg_count percentile rank
  (impressions-based underexposure stays for a follow-up — we don't
  track impressions today; inverse-msg-count is a reasonable proxy.)

## Replica safety

Pure SELECT only — no INSERT/UPDATE/DELETE. Patroni's read-replica
routing handles the rishi-6 endpoint at the infra layer
(`DATABASE_URL` per swarm node); this module's code-level guarantee
is "never writes to Postgres." Matches the etl_integrity / quality
loops already in the codebase.

## 15-minute cadence

Same as `_trending_stats_refresher` in main.py (the matview pass
this scoring builds on). One full pass is ~200-500ms on 3.6k bots
with 3M messages (pre-aggregated CTE), so 15 min between passes
gives a wide margin.

## DORMANT-FIRST

  - kill_switch gate: `ENABLE_FEED_RANKER_LOOP` defaults ON (this is
    a low-cost read-only job; no need to ship dormant)
  - Redis init failure → log + skip the pass; M2a fallback path
    handles the absence cleanly
  - Catalog signal absent (quality/streak/etc. missing for a bot) →
    coalesce to defaults; bot still ranks (just lower on that axis)
  - Single-bot catalog → all signals tie at 0.5; deterministic
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ─── score weights (locked from design §4) ──────────────────────────────


# Engagement formula
W_ENGAGEMENT_POPULARITY = 0.40
W_ENGAGEMENT_DEPTH = 0.25
W_ENGAGEMENT_QUALITY = 0.20
W_ENGAGEMENT_STREAK = 0.15

# Discovery formula (note: feed is discovery-leaning — design's
# comment "weighted toward FRESH")
W_DISCOVERY_NEWNESS = 0.45
W_DISCOVERY_MOMENTUM = 0.30
W_DISCOVERY_UNDEREXPOSURE = 0.15
W_DISCOVERY_QUALITY = 0.10

# Final blend between engagement + discovery. 50/50 lets the
# discovery formula dominate (it's already discovery-leaning) while
# still rewarding genuinely engaging bots.
W_BLEND_ENGAGEMENT = 0.50
W_BLEND_DISCOVERY = 0.50


# ─── runtime knobs ──────────────────────────────────────────────────────


FEED_GLOBAL_KEY = "feed:global"
FEED_GLOBAL_TTL_SEC = 60 * 60  # 1h — generous; the loop refreshes every 15m

LOOP_INTERVAL_SEC = 15 * 60  # 15 min, matching trending matview refresh
INITIAL_DELAY_SEC = 90  # let the cluster warm up before the first pass

# Cap on the bot_id list we serialize. The full catalog is ~3.6k today;
# 5k gives ~100x growth headroom without bloating the Redis blob.
MAX_RANKED_BOTS = 5000


# ─── Redis lazy client (mirrors discovery_feed pattern) ─────────────────


_redis_client = None


async def _get_redis():
    """Same shape as discovery_feed._get_redis. None on init failure ⇒
    caller logs + skips the pass."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis

        from redis_config import get_redis_url

        url = get_redis_url()
        if url:
            _redis_client = aioredis.from_url(url, decode_responses=True)
        else:
            _redis_client = aioredis.Redis(
                host=os.environ.get("REDIS_HOST", "redis-primary"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                password=os.environ.get("REDIS_PASSWORD") or None,
                decode_responses=True,
            )
        return _redis_client
    except Exception as e:
        logger.warning("feed_ranker: Redis init failed: %s", e)
        return None


# ─── signal-fetching SQL (single round-trip, pre-aggregated CTEs) ───────


_SIGNALS_SQL = """
WITH recent_msgs AS (
    SELECT c.influencer_id AS bot_id,
           COUNT(*) FILTER (
             WHERE m.created_at > NOW() - INTERVAL '7 days'
           ) AS msgs_recent,
           COUNT(*) FILTER (
             WHERE m.created_at > NOW() - INTERVAL '14 days'
               AND m.created_at <= NOW() - INTERVAL '7 days'
           ) AS msgs_prior
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE m.role = 'user'
      AND m.created_at > NOW() - INTERVAL '14 days'
    GROUP BY c.influencer_id
),
quality_latest AS (
    SELECT bot_id, score_overall AS quality
    FROM (
      SELECT bot_id, score_overall,
             ROW_NUMBER() OVER (
               PARTITION BY bot_id ORDER BY created_at DESC
             ) AS rn
      FROM bot_quality_scores
    ) q
    WHERE rn = 1
),
streaks_bot AS (
    SELECT influencer_id AS bot_id,
           MAX(current_streak_days) AS streak,
           COUNT(DISTINCT user_id)  AS unique_users
    FROM conversations
    GROUP BY influencer_id
)
SELECT
    i.id,
    EXTRACT(EPOCH FROM (NOW() - i.created_at))::float    AS age_sec,
    COALESCE(stats.conversation_count, 0)::float         AS conv_count,
    COALESCE(stats.message_count, 0)::float              AS msg_count,
    COALESCE(rm.msgs_recent, 0)::float                   AS msgs_recent,
    COALESCE(rm.msgs_prior, 0)::float                    AS msgs_prior,
    COALESCE(ql.quality, 0.5)::float                     AS quality,
    COALESCE(sb.streak, 0)::float                        AS streak,
    COALESCE(sb.unique_users, 0)::float                  AS unique_users
FROM ai_influencers i
LEFT JOIN influencer_trending_stats stats ON stats.influencer_id = i.id
LEFT JOIN recent_msgs    rm ON rm.bot_id = i.id
LEFT JOIN quality_latest ql ON ql.bot_id = i.id
LEFT JOIN streaks_bot    sb ON sb.bot_id = i.id
WHERE i.is_active = 'active'
"""


async def _fetch_signals(pool) -> list[dict]:
    rows = await pool.fetch(_SIGNALS_SQL)
    return [dict(r) for r in rows]


# ─── pure-Python scoring + normalization ────────────────────────────────


def _rank_percentile(values: list[float]) -> dict[float, float]:
    """Map distinct values to [0, 1] by sorted-rank position. Ties get
    the same percentile. Single-value input → 0.5 (avoid 0/0 + bias)."""
    distinct_sorted = sorted(set(values))
    if len(distinct_sorted) <= 1:
        return {v: 0.5 for v in values}
    denom = len(distinct_sorted) - 1
    return {v: i / denom for i, v in enumerate(distinct_sorted)}


def _compute_momentum(msgs_recent: float, msgs_prior: float) -> float:
    """Recent-vs-prior 7-day ratio, capped at 2× and rescaled to [0,1].

    Zero prior + zero recent ⇒ 0 (cold catalog). Zero prior + positive
    recent ⇒ 1.0 (brand-new bot picking up traffic — design's
    `newness` weight handles long-term-new bots; momentum captures
    the "just started getting attention" signal)."""
    if msgs_prior == 0 and msgs_recent == 0:
        return 0.0
    if msgs_prior == 0:
        return 1.0
    ratio = min(msgs_recent / msgs_prior, 2.0)
    return ratio / 2.0


def compute_scores(rows: list[dict]) -> list[tuple[str, float]]:
    """Pure function: takes signal rows, returns
    [(bot_id, final_score), …] sorted DESC by score.

    Pure so the unit tests can pin the algorithm without spinning
    a DB. The loop calls this with `_fetch_signals` output."""
    if not rows:
        return []

    conv_rank = _rank_percentile([r["conv_count"] for r in rows])
    msg_rank = _rank_percentile([r["msg_count"] for r in rows])
    uu_rank = _rank_percentile([r["unique_users"] for r in rows])
    quality_rank = _rank_percentile([r["quality"] for r in rows])
    streak_rank = _rank_percentile([r["streak"] for r in rows])

    # depth_ratio = msg_count / conv_count (clamp conv to 1 to avoid /0)
    depth_raw = [r["msg_count"] / max(r["conv_count"], 1.0) for r in rows]
    depth_rank = _rank_percentile(depth_raw)

    max_age = max(r["age_sec"] for r in rows) or 1.0

    scored: list[tuple[str, float]] = []
    for r, dr in zip(rows, depth_raw):
        bid = r["id"]
        popularity = (
            0.4 * conv_rank[r["conv_count"]]
            + 0.4 * msg_rank[r["msg_count"]]
            + 0.2 * uu_rank[r["unique_users"]]
        )
        depth = depth_rank[dr]
        quality_score = quality_rank[r["quality"]]
        streak_score = streak_rank[r["streak"]]

        # newness: smaller age = closer to 1.0
        newness = 1.0 - (r["age_sec"] / max_age)
        momentum = _compute_momentum(r["msgs_recent"], r["msgs_prior"])
        # underexposure: inverse of msg_count rank. A future PR with
        # real impressions data should replace this proxy.
        underexposure = 1.0 - msg_rank[r["msg_count"]]

        engagement = (
            W_ENGAGEMENT_POPULARITY * popularity
            + W_ENGAGEMENT_DEPTH * depth
            + W_ENGAGEMENT_QUALITY * quality_score
            + W_ENGAGEMENT_STREAK * streak_score
        )
        discovery = (
            W_DISCOVERY_NEWNESS * newness
            + W_DISCOVERY_MOMENTUM * momentum
            + W_DISCOVERY_UNDEREXPOSURE * underexposure
            + W_DISCOVERY_QUALITY * quality_score
        )
        final = W_BLEND_ENGAGEMENT * engagement + W_BLEND_DISCOVERY * discovery
        scored.append((bid, final))

    scored.sort(key=lambda kv: kv[1], reverse=True)
    return scored


# ─── Redis write ────────────────────────────────────────────────────────


async def _write_feed_global(bot_ids: list[str]) -> bool:
    """Serialize + SET. Returns True on success, False on any Redis
    error. Errors are logged but never raised — caller is the loop;
    failure just means M2a keeps using its fallback path."""
    redis = await _get_redis()
    if redis is None:
        return False
    try:
        payload = json.dumps(bot_ids)
        await redis.set(FEED_GLOBAL_KEY, payload, ex=FEED_GLOBAL_TTL_SEC)
        return True
    except Exception as e:
        logger.warning("feed_ranker: feed:global write failed: %s", e)
        return False


# ─── one full pass + the loop ───────────────────────────────────────────


async def rank_once(pool) -> dict:
    """One full pass — fetch signals, compute scores, write the
    ranked id list to Redis. Returns a stats dict for the loop log."""
    t0 = datetime.now(timezone.utc)
    try:
        rows = await _fetch_signals(pool)
    except Exception as e:
        logger.exception("feed_ranker: signal fetch failed: %s", e)
        return {"ok": False, "bots": 0, "elapsed_ms": 0, "stage": "fetch"}

    scored = compute_scores(rows)
    bot_ids = [bid for bid, _ in scored[:MAX_RANKED_BOTS]]

    wrote = await _write_feed_global(bot_ids)
    elapsed_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
    return {
        "ok": wrote,
        "bots": len(bot_ids),
        "elapsed_ms": int(elapsed_ms),
        "stage": "complete" if wrote else "write_failed",
    }


async def feed_ranker_loop():
    """Run rank_once every LOOP_INTERVAL_SEC. Gated on the
    `feed_ranker` kill switch (defaults ON — low-cost read-only job)."""
    from database import get_pool
    from kill_switch import is_enabled

    await asyncio.sleep(INITIAL_DELAY_SEC)
    while True:
        try:
            if not is_enabled("feed_ranker"):
                await asyncio.sleep(LOOP_INTERVAL_SEC)
                continue
            pool = await get_pool()
            stats = await rank_once(pool)
            logger.info(
                "feed_ranker: %d bots ranked in %dms (stage=%s)",
                stats["bots"],
                stats["elapsed_ms"],
                stats["stage"],
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("feed_ranker: pass failed (non-fatal) — retry next tick")
        await asyncio.sleep(LOOP_INTERVAL_SEC)
