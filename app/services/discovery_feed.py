"""Phase 21γ.P34.M2a — Discovery Feed: endpoint shell.

Reads precomputed `feed:global` from Redis when present; falls back to
a simple `SELECT` from `ai_influencers` when the blob doesn't exist
yet (M2c hasn't run, or first boot of a fresh node). The Stage A
ranking job (M2c) is a separate PR; this module only does the request
path.

## Hot-path composition

Per design doc §6 (Composer):

  1. **feed:global** — base ranked list of bot_ids. Built offline by
     M2c. M2a falls back to alphabetical-by-created_at when missing.
  2. **trending_overrides pins** (M0 table) — slots 1..N reserved
     for admin-pinned bots in `pinned_rank` order.
  3. **Seen-set dedup** — Redis `SET feed:seen:<session_id>` with
     TTL; the user doesn't see the same bot twice on subsequent pages.
  4. **Per-session shuffle** — deterministic (seeded by session_id)
     so a single user gets a stable order across pagination calls
     within the same session.

## Latency budget — p95 < 100ms

Request path is read-only + bounded:

  - 1× Redis GET for feed:global         (~2 ms)
  - 1× Redis SMEMBERS for seen-set       (~2 ms)
  - 1× Postgres SELECT (id IN $1)         (~5-15 ms for 50 ids)
  - In-mem pin overlay + shuffle + paginate (~1 ms)
  - 1× Redis SADD for seen-set            (~2 ms, fire-and-forget)

Total p95 budget ~25-30 ms even worst-case. The fallback path (no
Redis blob) does the full SELECT — slower but still < 100 ms on the
~3.6k-row catalog.

## DORMANT-FIRST

The endpoint always serves SOMETHING (fallback path), so mobile can
do real e2e testing on M2a alone without waiting for M2c. The Stage
A ranking job is an optimization, not a correctness gate. This is
the same design property B6 + M1 use — ship dormant, let the
operator enable the smart path later.
"""

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ─── Redis keys + TTLs ──────────────────────────────────────────────────


# feed:global is the precomputed ranked list of bot_ids (JSON array).
# Built by M2c; consumed here as the base for every request. Missing
# blob ⇒ SELECT fallback (no error surfaced).
FEED_GLOBAL_KEY = "feed:global"

# feed:seen:<session_id> = SET of bot_ids the user already saw in this
# session. Bounded TTL so an idle session eventually gets a fresh
# rotation without explicit reset.
SEEN_SET_PREFIX = "feed:seen:"
SEEN_SET_TTL_SEC = 24 * 60 * 60  # 24h

# Bound on how big the seen-set can grow before we stop adding (defense
# against pathological infinite-scroll filling Redis). 5000 ≈ way past
# the current catalog size; never expect to hit it in practice.
SEEN_SET_CAP = 5000


# ─── Redis lazy client (mirrors rate_limiter / cost_breaker pattern) ────


_redis_client = None


async def _get_redis():
    """Return a shared asyncio Redis client, or None on init failure.
    Same shape as `cost_breaker._get_redis` + `rate_limiter._get_redis`:
    failures degrade open (caller falls back to non-cached path)."""
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
        logger.warning("discovery_feed: Redis init failed (degrade open): %s", e)
        return None


# ─── feed:global read ───────────────────────────────────────────────────


async def _read_feed_global() -> list[str] | None:
    """Read the precomputed ranked bot_id list from Redis. Returns
    None on cache miss or any Redis error (caller falls back)."""
    redis = await _get_redis()
    if redis is None:
        return None
    try:
        raw = await redis.get(FEED_GLOBAL_KEY)
    except Exception as e:
        logger.warning("discovery_feed: feed:global read failed: %s", e)
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return None
        return [str(x) for x in parsed]
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("discovery_feed: feed:global parse failed: %s", e)
        return None


# ─── seen-set dedup ─────────────────────────────────────────────────────


def _seen_set_key(session_id: str) -> str:
    return SEEN_SET_PREFIX + session_id


async def _read_seen_set(session_id: str) -> set[str]:
    """Best-effort. Redis down ⇒ empty set (no dedup); the user just
    sees the same bot twice on a refresh, which is acceptable
    degraded behaviour vs returning 500."""
    if not session_id:
        return set()
    redis = await _get_redis()
    if redis is None:
        return set()
    try:
        members = await redis.smembers(_seen_set_key(session_id))
        return set(members or ())
    except Exception as e:
        logger.warning("discovery_feed: seen-set read failed: %s", e)
        return set()


async def _record_seen(session_id: str, bot_ids: list[str]) -> None:
    """Fire-and-forget. Failure here MUST NOT block the request — the
    user gets the page; next request may show repeats."""
    if not session_id or not bot_ids:
        return
    redis = await _get_redis()
    if redis is None:
        return
    try:
        key = _seen_set_key(session_id)
        # Cap defense: skip if set is already huge (operator misconfig
        # or pathological client).
        existing = await redis.scard(key)
        if existing >= SEEN_SET_CAP:
            return
        await redis.sadd(key, *bot_ids)
        await redis.expire(key, SEEN_SET_TTL_SEC)
    except Exception as e:
        logger.warning("discovery_feed: seen-set write failed (non-fatal): %s", e)


# ─── per-session shuffle ────────────────────────────────────────────────


def _shuffle_for_session(bot_ids: list[str], session_id: str) -> list[str]:
    """Deterministic shuffle keyed on session_id so a single user gets
    a stable order across pagination calls within the session, but
    different users see different orders.

    Implemented as a stable sort by SHA1(session_id + bot_id) so the
    permutation is uniform-ish + repeatable. Avoids Python's
    `random.seed` which has thread-safety + state-leak surprises."""
    if not session_id:
        return bot_ids

    def _key(bot_id: str) -> bytes:
        return hashlib.sha1(f"{session_id}|{bot_id}".encode("utf-8")).digest()

    return sorted(bot_ids, key=_key)


# ─── trending_overrides pin overlay (M0) ────────────────────────────────


async def _read_pins(pool) -> list[dict]:
    """Active pins ordered by rank. Excludes expired pins server-side
    so the request-path code doesn't have to filter."""
    try:
        rows = await pool.fetch(
            """
            SELECT influencer_id, pinned_rank
            FROM trending_overrides
            WHERE expires_at IS NULL OR expires_at > NOW()
            ORDER BY pinned_rank ASC
            """
        )
        return [dict(r) for r in rows]
    except Exception as e:
        # Migration 041 not applied yet ⇒ empty pin list. No pins is
        # the correct DORMANT default.
        logger.warning("discovery_feed: pin read failed (no pin overlay): %s", e)
        return []


def _apply_pin_overlay(
    base_ids: list[str], pins: list[dict], all_known: set[str]
) -> list[str]:
    """Insert pinned bots at their `pinned_rank` slots (1-based);
    push the previously-occupant + everything below down one slot.
    Bots already in base_ids are removed first so a pin doesn't
    double-include them.

    Pinned bots that aren't in `all_known` (deleted/inactive) are
    silently skipped — the FK protects most cases but the loop tick
    between unpin and feed serve could still see a stale pin."""
    if not pins:
        return base_ids
    pinned_set = {p["influencer_id"] for p in pins}
    deduped = [b for b in base_ids if b not in pinned_set]
    result = list(deduped)
    for p in sorted(pins, key=lambda r: r["pinned_rank"]):
        bot_id = p["influencer_id"]
        if bot_id not in all_known:
            continue
        # pinned_rank is 1-based; clamp to range
        slot = max(0, min(p["pinned_rank"] - 1, len(result)))
        result.insert(slot, bot_id)
    return result


# ─── fallback: alphabetical (created_at DESC) SELECT ────────────────────


async def _fallback_active_bot_ids(pool, soft_limit: int = 500) -> list[str]:
    """Used when feed:global doesn't exist yet. Returns the most
    recent `soft_limit` active bot ids by created_at. Cheap on the
    is_active partial index."""
    rows = await pool.fetch(
        """
        SELECT id
        FROM ai_influencers
        WHERE is_active = 'active'
        ORDER BY created_at DESC
        LIMIT $1
        """,
        soft_limit,
    )
    return [r["id"] for r in rows]


# ─── DB hydration: load full bot rows for the page's IDs ────────────────


async def _hydrate_bot_rows(pool, bot_ids: list[str]) -> list[dict]:
    """Single SELECT to materialize the page. Order is preserved via
    a CASE/ARRAY-POSITION trick so the caller doesn't need to re-sort."""
    if not bot_ids:
        return []
    # asyncpg's $1 = ARRAY of varchar. WITH ORDINALITY would be cleaner
    # but array_position($1::text[], i.id) is portable + index-friendly
    # enough on a small set (~50 ids per page).
    rows = await pool.fetch(
        """
        SELECT i.id, i.name, i.display_name, i.avatar_url, i.description,
               i.category, i.created_at, i.archetype, i.gender, i.is_active
        FROM ai_influencers i
        WHERE i.id = ANY($1::text[])
          AND i.is_active = 'active'
        """,
        bot_ids,
    )
    by_id = {r["id"]: dict(r) for r in rows}
    # Re-order to match input bot_ids (skipping any that were
    # inactive / deleted between Redis-build and hydration).
    return [by_id[b] for b in bot_ids if b in by_id]


# ─── envelope shaping (Anshuman-compatible) ─────────────────────────────


def _isoformat(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _shape_bot(row: dict, *, with_metadata: bool, rank_source: str) -> dict:
    """Per-bot envelope. Byte-compatible with Anshuman's response per
    design doc §8 (id, name, display_name, avatar_url, description,
    category, created_at). When `with_metadata=True`, surface the M1
    archetype + gender + the rank_source tag for debugging."""
    base = {
        "id": row["id"],
        "name": row.get("name") or "",
        "display_name": row.get("display_name") or "",
        "avatar_url": row.get("avatar_url") or "",
        "description": row.get("description") or "",
        "category": row.get("category") or "",
        "created_at": _isoformat(row.get("created_at")),
    }
    if with_metadata:
        base["archetype"] = row.get("archetype") or "unknown"
        base["gender"] = row.get("gender") or "unknown"
        # momentum / live are M2c (Stage A) + M3 (live signals); M2a
        # surfaces nulls so mobile can see the field exists without
        # waiting for those PRs.
        base["momentum"] = None
        base["live"] = None
        base["rank_source"] = rank_source
    return base


# ─── the request-path orchestrator ──────────────────────────────────────


async def build_feed_page(
    pool,
    *,
    offset: int,
    limit: int,
    with_metadata: bool,
    session_id: str,
) -> dict:
    """Single entry point used by the route. Returns the Anshuman-shaped
    FeedResponse dict. Latency bound: see module docstring (p95 < 100ms)."""
    # 1. Base ranked list — Redis-first, fallback to SELECT.
    rank_source = "feed_global"
    ranked = await _read_feed_global()
    if ranked is None:
        rank_source = "fallback_select"
        ranked = await _fallback_active_bot_ids(pool)

    # 2. Pin overlay. Done BEFORE shuffle so pins always land at the
    #    top regardless of session — operator pins are global, not
    #    per-session, by design.
    pins = await _read_pins(pool)
    all_known = set(ranked) | {p["influencer_id"] for p in pins}
    after_pins = _apply_pin_overlay(ranked, pins, all_known)

    # 3. Seen-set dedup. Skip bots the session already saw on prior pages.
    seen = await _read_seen_set(session_id)
    if seen:
        after_dedup = [b for b in after_pins if b not in seen]
    else:
        after_dedup = after_pins

    # 4. Per-session shuffle of the UNPINNED tail. Pinned bots stay
    #    fixed at the top; shuffling the tail gives every user a
    #    different exploration order without churning the operator's
    #    pinned rotation.
    pinned_ids = [p["influencer_id"] for p in pins if p["influencer_id"] in all_known]
    pinned_head = [b for b in after_dedup if b in set(pinned_ids)]
    unpinned_tail = [b for b in after_dedup if b not in set(pinned_ids)]
    shuffled_tail = _shuffle_for_session(unpinned_tail, session_id)
    composed = pinned_head + shuffled_tail

    # 5. Pagination — slice + compute total_count + has_more.
    total_count = len(composed)
    page_ids = composed[offset : offset + limit]

    # 6. Hydrate from DB (single SELECT).
    bot_rows = await _hydrate_bot_rows(pool, page_ids)
    influencers = [
        _shape_bot(r, with_metadata=with_metadata, rank_source=rank_source)
        for r in bot_rows
    ]

    # 7. Record seen-set fire-and-forget so the next request can dedup.
    if page_ids:
        # asyncio.create_task is OK here — we await up the chain, and
        # the request handler doesn't depend on this completing before
        # responding. Fail-open via the function's own try/except.
        asyncio.create_task(_record_seen(session_id, page_ids))

    return {
        "influencers": influencers,
        "total_count": total_count,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < total_count,
        "feed_generated_at": datetime.now(timezone.utc).isoformat(),
    }
