"""Phase 21γ.P34.M2a + M2b — Discovery Feed: endpoint shell + composer.

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
  5. **M2b composer** — reorders the shuffled tail for archetype
     diversity, ≥3-skilled-bots-on-page-1 guarantee, and cold-start
     gender guardrail (soft; only fires for users below the
     5-conversation / 1-deep-chat threshold per design §4 + §5).
     Composer fails open to the M2a shuffled order if metadata can't
     load (pre-M1-classification bots still have archetype='unknown'
     so the composer naturally degrades to a no-op there).

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
from collections import Counter, defaultdict
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


# ─── M2b composer ───────────────────────────────────────────────────────


# Cold-start threshold per design §4: a user is "warm" (eligible for
# personalization, no gender guardrail) once they cross 5 conversations
# OR 1 in-depth chat (≥10 user messages in one conversation). M2b
# implements the conversation-count side; the in-depth-chat refinement
# stays for a follow-up (it requires a more expensive subquery, and
# convs ≥ 5 already covers the bulk of warm users).
COLD_START_CONV_THRESHOLD = 5

# Composer applies a soft gender guardrail to the FIRST N slots of a
# cold-start user's feed: no single gender may exceed `GENDER_MAX_SHARE`
# of the prefix. 0.6 = at most 60% any one gender in the first 10 cards
# (~6/10 — leaves room for 2 of the dominant gender to still appear
# while breaking the "all-female" or "all-male" first-screen impression
# that triggered the design call).
GENDER_GUARDRAIL_PREFIX_LEN = 10
GENDER_MAX_SHARE = 0.6

# Skill guarantee per design §5: ≥3 skilled influencers on page 1
# (cold-start exploration prefers introducing the breadth of YRAL's
# skill bots — nutrition, coach, etc. — early). Applied across the
# top N slots regardless of personalization state.
SKILL_GUARANTEE_TOP_N = 3


async def _is_cold_start_user(pool, user_id: str | None) -> bool:
    """Return True if the user should get cold-start composition
    (gender guardrail + breadth-first diversity). No JWT = cold-start
    by definition. JWT-bearing users are looked up by conversation
    count; any DB failure fails open to cold-start (the safer default
    for the gender guardrail — we'd rather over-apply than under-apply
    on a fresh user)."""
    if not user_id:
        return True
    try:
        n = await pool.fetchval(
            "SELECT COUNT(*) FROM conversations WHERE user_id = $1",
            user_id,
        )
        return (n or 0) < COLD_START_CONV_THRESHOLD
    except Exception as e:
        logger.warning(
            "discovery_feed: cold-start lookup failed (fail-open to cold-start): %s",
            e,
        )
        return True


async def _read_composer_metadata(pool, bot_ids: list[str]) -> dict[str, dict]:
    """Bulk-read the four metadata columns the composer cares about.
    Cheap on the ANY($1) path — narrow columns, single SELECT. Returns
    a dict keyed by bot_id; missing rows mean the bot was deleted /
    inactive (caller treats as if metadata was empty)."""
    if not bot_ids:
        return {}
    try:
        rows = await pool.fetch(
            """
            SELECT id, archetype, gender, category, skill_slug
            FROM ai_influencers
            WHERE id = ANY($1::text[])
              AND is_active = 'active'
            """,
            bot_ids,
        )
        return {r["id"]: dict(r) for r in rows}
    except Exception as e:
        # Metadata SELECT failure ⇒ composer fails open (M2a shuffled
        # order). The route still serves a feed; just no diversity pass.
        logger.warning(
            "discovery_feed: composer metadata read failed (fail-open): %s", e
        )
        return {}


def _apply_skill_guarantee(
    ids: list[str], meta: dict[str, dict], top_n: int
) -> list[str]:
    """Reorder so the first `top_n` slots contain as many skilled bots
    as possible (up to top_n). Preserves relative order within the
    skilled + non-skilled groups so the upstream shuffle order is
    respected as a tiebreaker."""
    if not ids or top_n <= 0:
        return ids
    skilled = [b for b in ids if (meta.get(b) or {}).get("skill_slug")]
    if len(skilled) == 0:
        return ids
    unskilled = [b for b in ids if not (meta.get(b) or {}).get("skill_slug")]
    take = min(top_n, len(skilled))
    prefix = skilled[:take]
    rest = skilled[take:] + unskilled
    return prefix + rest


def _interleave_by_archetype(ids: list[str], meta: dict[str, dict]) -> list[str]:
    """Round-robin across archetype groups so the page doesn't end
    up "5 companions, then 5 advisors." Within each archetype the
    upstream order is preserved as a tiebreaker."""
    if not ids:
        return ids
    by_arch: dict[str, list[str]] = defaultdict(list)
    for b in ids:
        arch = (meta.get(b) or {}).get("archetype") or "unknown"
        by_arch[arch].append(b)
    # If everything maps to the same bucket (e.g. pre-M1-backfill catalog
    # where everyone is 'unknown'), the interleave is a no-op and we
    # return the input order untouched.
    if len(by_arch) <= 1:
        return ids
    interleaved: list[str] = []
    # Cycle through buckets in a stable order so the diversity pattern
    # is reproducible — sorted() over archetype names.
    bucket_order = sorted(by_arch.keys())
    while any(by_arch[a] for a in bucket_order):
        for arch in bucket_order:
            if by_arch[arch]:
                interleaved.append(by_arch[arch].pop(0))
    return interleaved


def _apply_gender_guardrail(
    ids: list[str],
    meta: dict[str, dict],
    *,
    prefix_len: int,
    max_share: float,
) -> list[str]:
    """Soft constraint: in the first `prefix_len` slots, no single
    gender may exceed `max_share` of the slots. When violated, swap a
    dominant-gender bot from the prefix with a non-dominant bot from
    the tail until the constraint is satisfied or no swap candidate
    remains. 'unknown' is never counted as dominant (we don't want to
    swap pre-classification bots out of the prefix)."""
    if len(ids) <= prefix_len:
        return ids
    prefix = list(ids[:prefix_len])
    tail = list(ids[prefix_len:])
    # Cap iterations so a degenerate catalog (e.g. ALL one gender)
    # can't infinite-loop.
    for _ in range(prefix_len):
        counts = Counter((meta.get(b) or {}).get("gender") or "unknown" for b in prefix)
        # Drop 'unknown' from dominance comparison.
        scoreable = {g: c for g, c in counts.items() if g != "unknown"}
        if not scoreable:
            break
        dominant_gender, dominant_count = max(scoreable.items(), key=lambda kv: kv[1])
        if dominant_count / prefix_len <= max_share:
            break
        # Find a non-dominant, non-unknown bot in the tail to swap in.
        swap_in = None
        for i, b in enumerate(tail):
            g = (meta.get(b) or {}).get("gender")
            if g and g not in (dominant_gender, "unknown"):
                swap_in = i
                break
        if swap_in is None:
            break  # no candidate; give up gracefully
        # Find a dominant-gender bot in the prefix (prefer later
        # positions so the very-top is least disrupted).
        swap_out = None
        for j in range(len(prefix) - 1, -1, -1):
            if (meta.get(prefix[j]) or {}).get("gender") == dominant_gender:
                swap_out = j
                break
        if swap_out is None:
            break
        prefix[swap_out], tail[swap_in] = tail[swap_in], prefix[swap_out]
    return prefix + tail


def compose_diverse_order(
    ids: list[str],
    meta: dict[str, dict],
    *,
    is_cold_start: bool,
) -> list[str]:
    """The M2b composer entry point. Reorders `ids` for:
      1. ≥3-skilled-bots prefix (always applied)
      2. Round-robin archetype interleave (always applied)
      3. Soft cold-start gender guardrail (cold-start users only)

    `meta` is the dict returned by `_read_composer_metadata`. Bots with
    missing metadata stay in their input position implicitly because
    `.get(...) or {}` makes them indistinguishable from rows where
    every classifier output is 'unknown'.

    DORMANT-FIRST: if `meta` is empty (read failure or pre-M1 catalog)
    the function returns `ids` unchanged — the M2a shuffle remains
    in effect."""
    if not ids or not meta:
        return ids
    with_skill = _apply_skill_guarantee(ids, meta, SKILL_GUARANTEE_TOP_N)
    # Skill prefix stays fixed; interleave the rest by archetype so
    # the "first 3 skilled" promise isn't broken by the diversity pass.
    skill_prefix_len = min(SKILL_GUARANTEE_TOP_N, len(with_skill))
    head = with_skill[:skill_prefix_len]
    tail = with_skill[skill_prefix_len:]
    interleaved_tail = _interleave_by_archetype(tail, meta)
    combined = head + interleaved_tail
    if is_cold_start:
        combined = _apply_gender_guardrail(
            combined,
            meta,
            prefix_len=GENDER_GUARDRAIL_PREFIX_LEN,
            max_share=GENDER_MAX_SHARE,
        )
    return combined


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


def _shape_bot(
    row: dict,
    *,
    with_metadata: bool,
    rank_source: str,
    composer_state: str = "none",
) -> dict:
    """Per-bot envelope. Byte-compatible with Anshuman's response per
    design doc §8 (id, name, display_name, avatar_url, description,
    category, created_at). When `with_metadata=True`, surface the M1
    archetype + gender + the rank_source tag + the M2b composer_state
    for debugging."""
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
        # composer_state: "cold_start" | "warm" | "none". None when the
        # composer no-op'd (empty metadata, single-archetype catalog).
        # Lets Rishi confirm the cold-start path fired by curling with
        # / without a JWT.
        base["composer_state"] = composer_state
    return base


# ─── the request-path orchestrator ──────────────────────────────────────


async def build_feed_page(
    pool,
    *,
    offset: int,
    limit: int,
    with_metadata: bool,
    session_id: str,
    user_id: str | None = None,
) -> dict:
    """Single entry point used by the route. Returns the Anshuman-shaped
    FeedResponse dict. Latency bound: see module docstring (p95 < 100ms).

    `user_id` (when present) drives the M2b cold-start gating — users
    below the 5-conversation threshold get the gender guardrail applied
    to their first-screen composition; warmer users skip it. None ⇒
    cold-start (the safe default)."""
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

    # 4b. M2b composer — reorder the shuffled tail for diversity + skill
    #     guarantee + cold-start gender guardrail. Pins stay above the
    #     composer's reach (operator intent wins over algorithmic
    #     diversity). The composer needs metadata; we bulk-read it for
    #     the WHOLE tail before pagination so the diversity pass spans
    #     the full slate, not just the visible page.
    is_cold_start = await _is_cold_start_user(pool, user_id)
    composer_meta = await _read_composer_metadata(pool, shuffled_tail)
    composed_tail = compose_diverse_order(
        shuffled_tail, composer_meta, is_cold_start=is_cold_start
    )
    composed = pinned_head + composed_tail
    # composer_state surfaces in `with_metadata=true` responses so
    # Rishi can confirm the cold-start path fired by curling with /
    # without a JWT. "none" means metadata empty ⇒ composer was a no-op.
    if not composer_meta:
        composer_state = "none"
    else:
        composer_state = "cold_start" if is_cold_start else "warm"

    # 5. Pagination — slice + compute total_count + has_more.
    total_count = len(composed)
    page_ids = composed[offset : offset + limit]

    # 6. Hydrate from DB (single SELECT).
    bot_rows = await _hydrate_bot_rows(pool, page_ids)
    influencers = [
        _shape_bot(
            r,
            with_metadata=with_metadata,
            rank_source=rank_source,
            composer_state=composer_state,
        )
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
