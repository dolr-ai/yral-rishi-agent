"""Phase 21γ.P34.M7 — shadow-diff v2 discovery feed vs Anshuman's.

Fetches both feeds at offset=0 + a sample limit, computes their
catalog overlap + ordering deltas + source-exclusive bots, logs to
Sentry as a breadcrumb + returns the diff to the admin caller.

## What this checks

Pre-cutover sanity: are the two systems serving comparable bot
sets? Mobile is byte-compatible across both envelopes (same
FeedResponse shape), so the cutover risk is "the v2 feed shows
wildly different bots than chat-ai's." This endpoint surfaces that
quantitatively before Rishi green-lights the alpha-team Remote
Config flip.

Anshuman's recsys is NOT personalized (per
`project_ansuman_recsys_facts` memory: no `user_id` param, same
global list per offset). So this diff doesn't take a user_id —
it's a global catalog-overlap check.

## Output schema

  {
    "v2_count":            N,
    "anshuman_count":      N,
    "overlap_pct":         0.0..100.0,
    "only_in_v2":          [bot_id, …]  (first 20)
    "only_in_anshuman":    [bot_id, …]  (first 20)
    "ordering_deltas":     [(rank_v2, rank_anshuman, bot_id), …]
                           — bots present in both, with their
                           position in each list. First 20.
    "checked_at":          ISO-8601
  }

## Cheap implementation

  - Single httpx GET to Anshuman (5s timeout)
  - Single discovery_feed.build_feed_page call (reuses M2a path)
  - Pure-Python diff (set ops + zip)
  - No DB writes; Sentry breadcrumb optional (no-op when SDK absent)
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


ANSHUMAN_FEED_URL = (
    "https://recsys-influencer-feed.ansuman.yral.com/api/v1/influencer-feed"
)
HTTP_TIMEOUT_SEC = 5.0
SAMPLE_LIST_TRUNCATE = 20


# ─── Anshuman HTTP fetch ────────────────────────────────────────────────


async def _fetch_anshuman_ids(limit: int) -> list[str]:
    """Pull the top `limit` bot ids from Anshuman's feed at offset=0.
    Network / parse errors surface as exceptions to the caller; the
    admin route translates them to a structured error in the diff
    envelope rather than 5xx'ing."""
    import httpx

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
        resp = await client.get(
            ANSHUMAN_FEED_URL,
            params={"offset": 0, "limit": limit, "with_metadata": "false"},
        )
        resp.raise_for_status()
        body = resp.json()

    if not isinstance(body, dict):
        raise ValueError(
            f"Anshuman feed returned non-object response: {type(body).__name__}"
        )
    influencers = body.get("influencers")
    if not isinstance(influencers, list):
        raise ValueError("Anshuman feed missing influencers[] array")
    return [str(i.get("id")) for i in influencers if i and i.get("id")]


# ─── v2 fetch (re-use the live M2a path) ────────────────────────────────


async def _fetch_v2_ids(pool, limit: int, session_id: str) -> list[str]:
    """Build v2's page-0 feed using the same `build_feed_page` mobile
    hits. Uses an anonymous session_id (no JWT) so the comparison
    reflects what a logged-out browser would see — Anshuman's feed
    is also unauthenticated."""
    from services import discovery_feed

    payload = await discovery_feed.build_feed_page(
        pool,
        offset=0,
        limit=limit,
        with_metadata=False,
        session_id=session_id,
        user_id=None,
    )
    influencers = payload.get("influencers") or []
    return [str(i.get("id")) for i in influencers if i.get("id")]


# ─── pure-Python diff ───────────────────────────────────────────────────


def compute_diff(v2_ids: list[str], anshuman_ids: list[str]) -> dict:
    """Catalog overlap + ordering deltas. Pure function so the unit
    tests can pin the math without spinning a DB or hitting Anshuman."""
    v2_set = set(v2_ids)
    an_set = set(anshuman_ids)
    union = v2_set | an_set
    intersection = v2_set & an_set
    overlap_pct = (100.0 * len(intersection) / len(union)) if union else 0.0

    v2_rank = {bid: i for i, bid in enumerate(v2_ids)}
    an_rank = {bid: i for i, bid in enumerate(anshuman_ids)}
    ordering = [(v2_rank[bid], an_rank[bid], bid) for bid in intersection]
    # Sort by absolute rank delta DESC so the biggest divergences
    # surface first in the truncated list.
    ordering.sort(key=lambda t: -abs(t[0] - t[1]))

    only_v2 = [bid for bid in v2_ids if bid not in an_set]
    only_an = [bid for bid in anshuman_ids if bid not in v2_set]

    return {
        "v2_count": len(v2_ids),
        "anshuman_count": len(anshuman_ids),
        "overlap_pct": round(overlap_pct, 1),
        "only_in_v2": only_v2[:SAMPLE_LIST_TRUNCATE],
        "only_in_anshuman": only_an[:SAMPLE_LIST_TRUNCATE],
        "ordering_deltas": ordering[:SAMPLE_LIST_TRUNCATE],
    }


def _log_sentry_breadcrumb(diff: dict) -> None:
    """Best-effort — Sentry SDK may be absent in dev. Never raises."""
    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(
            category="discovery.shadow_diff",
            level="info",
            message=(
                f"shadow-diff: overlap={diff['overlap_pct']:.1f}% "
                f"v2={diff['v2_count']} ansuman={diff['anshuman_count']}"
            ),
            data={k: diff[k] for k in ("v2_count", "anshuman_count", "overlap_pct")},
        )
    except Exception:
        pass  # never let Sentry block the diff response


# ─── orchestrator ───────────────────────────────────────────────────────


async def shadow_diff(pool, limit: int, session_id: str) -> dict:
    """Fetch both feeds + compute the diff. Returns the diff dict
    plus error context if either side failed (no 5xx — operator wants
    actionable output, not a 500)."""
    t0 = datetime.now(timezone.utc)
    errors: list[str] = []

    try:
        v2_ids = await _fetch_v2_ids(pool, limit, session_id)
    except Exception as e:
        logger.warning("shadow_diff: v2 fetch failed: %s", e)
        errors.append(f"v2_fetch: {type(e).__name__}: {e}")
        v2_ids = []

    try:
        anshuman_ids = await _fetch_anshuman_ids(limit)
    except Exception as e:
        logger.warning("shadow_diff: Anshuman fetch failed: %s", e)
        errors.append(f"anshuman_fetch: {type(e).__name__}: {e}")
        anshuman_ids = []

    diff = compute_diff(v2_ids, anshuman_ids)
    diff["checked_at"] = t0.isoformat()
    diff["elapsed_ms"] = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    if errors:
        diff["errors"] = errors
    else:
        # Only breadcrumb the clean-fetch path — failed fetches log a
        # warning above, no need to also breadcrumb the partial diff.
        _log_sentry_breadcrumb(diff)
    return diff
