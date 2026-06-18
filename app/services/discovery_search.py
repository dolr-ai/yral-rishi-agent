"""Phase 21γ.P34.Search — discovery search endpoint.

Per docs/discovery-feed-search-addendum-2026-06-18.md §2 + §4.

`GET /api/v2/discovery/search?q=<text>&limit=20`

  - Pure SQL on the existing `idx_ai_influencers_search_trgm` GIN
    trigram index (migration 043). No LLM, no Redis.
  - Lowercased server-side before similarity match (the index
    expression is LOWER(...), so both sides must lowercase).
  - Returns the feed envelope per-bot SHAPE + two extra fields:
      `kind`     = "influencer" (always; future-proof slot for a
                   later user-search if/when v2 grows a user
                   directory)
      `subtitle` = "<archetype> · <category>" string for mobile to
                   render under the name. Falls back to 'unknown'
                   when archetype is the M1 default 'unknown' value.
  - Active bots only (`WHERE is_active = 'active'`).

## Fail-open envelope

  - Empty / whitespace q → `{"results": [], "count": 0}` (NOT 422;
    mobile debounces while typing, this is the natural stream state).
  - Postgres pool unreachable → 503 (handled at the route layer; the
    service raises and the route's catastrophic-only handler maps it).

## Latency

  ~10-30 ms on the 3.7k-row catalog. The index makes the `%`
  candidate filter cheap; similarity() runs only on the candidate
  set; ORDER BY + LIMIT keep the result small.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# The concatenation expression — MUST stay byte-identical to the
# index expression in migration 043. If you reorder or add a field,
# the planner stops using the GIN index and the endpoint slows to
# a sequential scan.
_CONCAT_SQL = (
    "LOWER("
    "i.display_name || ' ' || i.name "
    "|| ' ' || COALESCE(i.category,   '') "
    "|| ' ' || COALESCE(i.archetype,  '') "
    "|| ' ' || COALESCE(i.description, '')"
    ")"
)


# Per-query trgm threshold. Postgres default is 0.3, which is too
# strict for short / partial queries ("tara" against a long display
# name only scores ~0.2). 0.05 covers reasonable mobile UX without
# returning pure noise. SET LOCAL keeps it scoped to the transaction.
_SIMILARITY_THRESHOLD = 0.05


_SEARCH_SQL = f"""
SELECT i.id, i.name, i.display_name, i.avatar_url, i.description,
       i.category, i.created_at, i.archetype,
       COALESCE(stats.message_count, 0) AS msg_count,
       similarity({_CONCAT_SQL}, $1) AS sim
FROM ai_influencers i
LEFT JOIN influencer_trending_stats stats
    ON stats.influencer_id = i.id
WHERE i.is_active = 'active'
  AND {_CONCAT_SQL} % $1
ORDER BY sim DESC, msg_count DESC
LIMIT $2
"""


# ─── envelope shaping ───────────────────────────────────────────────────


def _isoformat(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _build_subtitle(archetype: str | None, category: str | None) -> str:
    """`<archetype> · <category>`. 'unknown' archetype renders as
    literal 'unknown' per the addendum (mobile UX team handles the
    visual treatment); empty category collapses to just the archetype
    so we don't ship a trailing separator."""
    a = (archetype or "unknown").strip() or "unknown"
    c = (category or "").strip()
    if c:
        return f"{a} · {c}"
    return a


def _shape_result(row: dict) -> dict:
    """Per-row search envelope. The first 7 fields match the discovery
    feed's per-bot shape (id, name, display_name, avatar_url,
    description, category, created_at). `kind` + `subtitle` are
    search-specific."""
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "display_name": row.get("display_name") or "",
        "avatar_url": row.get("avatar_url") or "",
        "description": row.get("description") or "",
        "category": row.get("category") or "",
        "created_at": _isoformat(row.get("created_at")),
        # Future-proof slot for a later user-search; today always
        # "influencer" so mobile can already key off kind without
        # changing the parser when user-search ships.
        "kind": "influencer",
        "subtitle": _build_subtitle(row.get("archetype"), row.get("category")),
    }


# ─── search orchestrator ────────────────────────────────────────────────


async def search(pool, q: str, limit: int) -> dict:
    """Return `{"results": [...], "count": N}`. Empty / whitespace
    `q` returns the empty envelope without touching Postgres."""
    q_clean = (q or "").strip().lower()
    if not q_clean:
        return {"results": [], "count": 0}
    # Length cap defense — bound the similarity calc cost even if a
    # malicious client bypasses the route's Pydantic validator.
    q_clean = q_clean[:100]

    # `SET LOCAL` is transaction-scoped, so we need a single connection
    # for both the SET + the SELECT. acquire() + transaction() is the
    # asyncpg idiom for this.
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                f"SET LOCAL pg_trgm.similarity_threshold = {_SIMILARITY_THRESHOLD}"
            )
            rows = await conn.fetch(_SEARCH_SQL, q_clean, limit)

    results = [_shape_result(dict(r)) for r in rows]
    return {"results": results, "count": len(results)}
