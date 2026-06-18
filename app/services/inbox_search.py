"""Phase 21γ.P34.InboxSearch — search the user's existing
conversations by bot display_name / category / archetype.

Sibling of `discovery_search` for the inbox tab. Triggered by the
shared search bar above the Discover + Inbox tabs (Rishi
2026-06-18 PM); the bar switches behaviour based on which tab is
focused.

## Scope (v1 per brief)

  - Trigram match on `LOWER(display_name || category || archetype)`
  - Filtered to `conversations.user_id = <jwt_sub>` (privacy hard
    requirement — never any cross-user surfacing)
  - Active bots only (skip rows that point at deactivated bots)
  - Per-conversation aggregates (last_message_at, message_count)
    pulled via LATERAL on the messages table
  - ORDER BY similarity DESC, last_message_at DESC, message_count DESC

## Not in v1 (deferred — bigger lifts)

  - Full-text message-body search (Phase 1c — needs an FTS index)
  - User search (v2 has no user directory)
  - Cross-user search (privacy violation, never)

## Why no trgm index

The trgm match runs only against the calling user's conversations
(typically <100). `idx_conversations_user_id` makes the user filter
selective; sequential similarity calc on <100 rows is microseconds.
A new trgm index would only help users with thousands of bots —
not the current shape. Spec says: "trigger off the existing JOIN
for v1 — measure first."

## Fail-open

  - Empty / whitespace q → `{"results": [], "count": 0}` (NOT 422 —
    mobile sends `?q=` while debouncing)
  - q length-capped at 100 chars (defense in depth)
  - Postgres pool unreachable → raised; route translates to 503
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# Per-query trgm threshold. Same rationale as discovery_search: 0.3
# default is too strict for short / partial mobile UX. 0.05 covers
# reasonable matches without returning noise. SET LOCAL scopes it to
# the transaction.
_SIMILARITY_THRESHOLD = 0.05


# Match expression — display_name + category + archetype only (per
# brief). NOT the broader discovery_search concat (which includes name
# + description). Inbox search ranks by what the user actually sees in
# their chat list, not internal bot identifiers.
_CONCAT_SQL = (
    "LOWER("
    "i.display_name "
    "|| ' ' || COALESCE(i.category,  '') "
    "|| ' ' || COALESCE(i.archetype, '')"
    ")"
)


_SEARCH_SQL = f"""
SELECT
    c.id            AS conversation_id,
    c.influencer_id,
    i.display_name  AS influencer_display_name,
    i.avatar_url    AS influencer_avatar_url,
    i.archetype,
    i.category,
    COALESCE(agg.last_message_at, c.created_at) AS last_message_at,
    COALESCE(agg.message_count, 0)              AS message_count,
    similarity({_CONCAT_SQL}, $2)               AS sim
FROM conversations c
JOIN ai_influencers i ON i.id = c.influencer_id
LEFT JOIN LATERAL (
    SELECT MAX(created_at) AS last_message_at,
           COUNT(*)        AS message_count
    FROM messages
    WHERE conversation_id = c.id
) agg ON true
WHERE c.user_id = $1
  AND c.conversation_type = 'ai_chat'
  AND c.influencer_id IS NOT NULL
  AND i.is_active = 'active'
  AND similarity({_CONCAT_SQL}, $2) >= {_SIMILARITY_THRESHOLD}
ORDER BY sim DESC,
         last_message_at DESC NULLS LAST,
         message_count DESC
LIMIT $3
"""


# ─── envelope shaping ───────────────────────────────────────────────────


def _isoformat(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _build_subtitle(archetype: str | None, category: str | None) -> str:
    """Same shape as discovery_search's `_build_subtitle`. Kept
    local (vs imported) so the inbox-search module stays
    self-contained — both helpers are 5 lines, duplication cheaper
    than cross-module coupling for one string format."""
    a = (archetype or "unknown").strip() or "unknown"
    c = (category or "").strip()
    return f"{a} · {c}" if c else a


def _shape_result(row: dict) -> dict:
    """Per-row inbox-search envelope per brief — conversation_id +
    the bot identity fields the inbox UI renders, plus the
    `subtitle`/`influencer_subtitle` strings. The two subtitle
    fields are the same value (brief lists both; mobile may render
    differently across the two contexts)."""
    subtitle = _build_subtitle(row.get("archetype"), row.get("category"))
    return {
        "conversation_id": row["conversation_id"],
        "influencer_id": row["influencer_id"],
        "influencer_display_name": row.get("influencer_display_name") or "",
        "influencer_avatar_url": row.get("influencer_avatar_url") or "",
        "influencer_subtitle": subtitle,
        "last_message_at": _isoformat(row.get("last_message_at")),
        "message_count": int(row.get("message_count") or 0),
        "subtitle": subtitle,
    }


# ─── orchestrator ───────────────────────────────────────────────────────


async def search(pool, user_id: str, q: str, limit: int) -> dict:
    """Return `{"results": [...], "count": N}` for `user_id`. Empty
    `q` short-circuits without touching Postgres. `user_id` is the
    caller's JWT sub (route enforces JWT presence)."""
    q_clean = (q or "").strip().lower()
    if not q_clean:
        return {"results": [], "count": 0}
    q_clean = q_clean[:100]

    rows = await pool.fetch(_SEARCH_SQL, user_id, q_clean, limit)
    results = [_shape_result(dict(r)) for r in rows]
    return {"results": results, "count": len(results)}
