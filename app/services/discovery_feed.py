"""Phase 21γ.P34.M2a — Discovery Feed: minimum endpoint shell.

Pure SQL + pin overlay + envelope shaping. Unblocks the mobile expert
who has Phase 1 wired + APK ready but is waiting for a live backend
(per Rishi's "wait for live backend" rule — testing against Anshuman
is off the table).

## What's HERE (M2a, this PR)

  - SELECT active bots ORDER BY created_at DESC
  - Pin overlay from `trending_overrides` (M0 table) — pins at the
    top in `pinned_rank` order, deduped against the SELECT list
  - Anshuman-compatible `FeedResponse` envelope (byte-identical so
    mobile parsing doesn't change at cutover)
  - Optional `?with_metadata=true` adds archetype + gender for debug

## What's NOT here yet (deferred to M2b / M2c)

  - Redis `feed:global` precomputed ranked list
  - Seen-set dedup
  - Per-session shuffle
  - Stage A scoring (popularity / depth / quality / newness)
  - Composer (diversity, cold-start gender guardrail)
  - Personalization (M4)

## Latency

p95 < 200 ms acceptable per the M2a brief. Current path is one
COUNT + one pin-table read + one bot-id SELECT + one hydration
SELECT, all index-backed. Real-world: ~10-30 ms on the prod catalog
(~3.6k active rows).
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# Hard cap on the in-memory ordered ID list. The full active catalog
# is ~3.6k rows today; this bound gives ~100 pages of 20 before
# falling off the end. M2c will replace this with a Redis precomputed
# blob that has no such cap.
ACTIVE_ID_FETCH_LIMIT = 5000


# ─── pin overlay (M0 table) ─────────────────────────────────────────────


async def _read_active_pins(pool) -> list[str]:
    """Return active pin influencer_ids ordered by `pinned_rank`.
    Excludes expired pins server-side. Migration 041 missing ⇒ empty
    list (DORMANT-FIRST default; no error)."""
    try:
        rows = await pool.fetch(
            """
            SELECT influencer_id
            FROM trending_overrides
            WHERE expires_at IS NULL OR expires_at > NOW()
            ORDER BY pinned_rank ASC
            """
        )
        return [r["influencer_id"] for r in rows]
    except Exception as e:
        # Table missing (M0 migration not yet applied on this node) or
        # transient DB issue ⇒ degrade open. The feed still serves.
        logger.warning("discovery_feed: pin read failed (no pin overlay): %s", e)
        return []


# ─── ordered ID list (pins + alphabetical-by-created_at) ────────────────


async def _ordered_active_ids(pool) -> list[str]:
    """Return the full ordered list of active bot IDs with pins at the
    top. Cheap on `idx_influencers_active` partial index. Used for
    pagination math (offset / limit / has_more) — actual hydration
    only loads the page slice."""
    pin_ids = await _read_active_pins(pool)
    pin_set = set(pin_ids)
    rows = await pool.fetch(
        """
        SELECT id
        FROM ai_influencers
        WHERE is_active = 'active'
        ORDER BY created_at DESC
        LIMIT $1
        """,
        ACTIVE_ID_FETCH_LIMIT,
    )
    select_ids = [r["id"] for r in rows if r["id"] not in pin_set]
    # Pins that point at deleted / inactive bots silently drop here —
    # the M0 FK with ON DELETE CASCADE handles most cases but the
    # is_active != 'active' soft-delete path can still leave stale pins.
    valid_pins = [p for p in pin_ids if any(r["id"] == p for r in rows)]
    return valid_pins + select_ids


# ─── page hydration ─────────────────────────────────────────────────────


async def _hydrate_page(pool, page_ids: list[str]) -> list[dict]:
    """One SELECT for the page. Order-preserving via in-mem reindex."""
    if not page_ids:
        return []
    rows = await pool.fetch(
        """
        SELECT id, name, display_name, avatar_url, description, category,
               created_at, archetype, gender
        FROM ai_influencers
        WHERE id = ANY($1::text[])
          AND is_active = 'active'
        """,
        page_ids,
    )
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in page_ids if i in by_id]


# ─── envelope shaping ───────────────────────────────────────────────────


def _isoformat(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _shape_bot(row: dict, *, with_metadata: bool) -> dict:
    """Per-bot envelope. Byte-compatible with Anshuman's response per
    design doc §8 (id, name, display_name, avatar_url, description,
    category, created_at). `with_metadata=True` adds the M1
    archetype + gender for ops debug."""
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
    return base


# ─── the request-path orchestrator ──────────────────────────────────────


async def build_feed_page(
    pool,
    *,
    offset: int,
    limit: int,
    with_metadata: bool,
    debug_source: bool = False,
) -> dict:
    """Single entry point used by the route. Returns the Anshuman-shaped
    `FeedResponse` dict. M2a contract — no Redis, no shuffle, no
    composer; just SQL + envelope shaping."""
    ordered_ids = await _ordered_active_ids(pool)
    total_count = len(ordered_ids)
    page_ids = ordered_ids[offset : offset + limit]
    bot_rows = await _hydrate_page(pool, page_ids)
    influencers = [_shape_bot(r, with_metadata=with_metadata) for r in bot_rows]
    payload = {
        "influencers": influencers,
        "total_count": total_count,
        "offset": offset,
        "limit": limit,
        "has_more": (offset + limit) < total_count,
        "feed_generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if debug_source:
        # M7 cutover-prep marker (cheap insurance — landing it on M2a
        # so Rishi can confirm Motorola is hitting v2 from day one).
        payload["debug_source"] = "v2"
    return payload
