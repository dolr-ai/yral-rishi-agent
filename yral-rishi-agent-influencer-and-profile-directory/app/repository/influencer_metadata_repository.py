# ---------------------------------------------------------------------------
# influencer_metadata_repository.py — asyncpg-based data-access layer
# for the `influencer_metadata` table.
#
# ⭐ START HERE: this module is the ONLY place in the codebase that
# issues SQL against `influencer_metadata`. Everything else (HTTP routes
# in Chunk B, future caching layer) reaches the table through these
# functions. Centralising the SQL means a future schema bump touches
# ONE file.
#
# WHY ASYNCPG DIRECTLY + NOT SQLAlchemy
# Per the v2 service-template directive verbatim: "no SQLAlchemy ORM —
# direct asyncpg + Pydantic models keeps the dep tree thin per A2.1."
# We use asyncpg's native `pool.fetchrow(...)` / `pool.fetch(...)` API
# + parse each Record into an `InfluencerMetadata` Pydantic model at
# the boundary.
#
# READ METHODS — 3 PATHS THE ENDPOINTS (CHUNK B) WIRE TO
# ------------------------------------------------------------------
#   get_by_id(influencer_id)              → InfluencerMetadata | None
#       Used by `GET /v1/influencers/{id}`.
#   list_paginated(limit, offset)         → list[InfluencerMetadata]
#       Used by `GET /v1/influencers?limit&offset`. ORDER BY id ASC for
#       deterministic offset/limit pagination (no ranking semantics —
#       the parity contract doesn't specify one for the bare list).
#   list_trending(limit)                  → list[InfluencerMetadata]
#       Used by `GET /v1/influencers/trending`. ORDER BY follower_count
#       DESC restricted to `is_active = 'active'`. The partial index
#       `influencer_metadata_active_follower_count` makes this an
#       index-only scan.
#
# WHY NO WRITE METHODS TODAY
# Per the Q5 lock-in 2026-05-23 + the 3-PR plan: PR-D1 ships the
# read-only service-build (schema + repository + 3 GET endpoints);
# PR-D2 ports chat-ai data via a one-shot ETL script (coordinator-
# driven cross-cluster execution under typed Rishi YES, NOT a
# repository write method invoked at runtime). Creator-studio writes
# land as their own future PR with auth + audit log; the repository's
# write surface is intentionally absent today so a refactor adding it
# is an obvious + reviewable diff.
#
# COLUMN PROJECTION
# ------------------------------------------------------------------
# All 3 queries SELECT only the 9 contract-shape columns (id,
# display_name, bio, avatar_url, archetype, is_nsfw, follower_count,
# creator_user_id, is_active). The DB also stores `source` +
# `created_at` + `updated_at` audit columns (v2-only fresh-design
# fields), but those aren't part of the `InfluencerMetadata` Pydantic
# model + don't need to leave the DB for today's read paths. Future
# endpoints that want them (e.g. Last-Modified caching headers) extend
# the model + the SELECT list together.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from typing import Final

from app.database import get_pool
from app.models.influencer_metadata import InfluencerMetadata


# The 9 contract-shape columns. Held as a constant so all 3 queries
# project the same set + an accidental drift (e.g. one query forgetting
# `creator_user_id`) is impossible. Newline-separated for readability
# in the assembled SQL strings.
_CONTRACT_COLUMNS_FOR_SELECT: Final[str] = (
    "id, display_name, bio, avatar_url, archetype, is_nsfw, "
    "follower_count, creator_user_id, is_active"
)


async def get_by_id(influencer_id: str) -> InfluencerMetadata | None:
    """Return the influencer matching `id = $1`, or None if missing.

    WHAT: SELECT against `influencer_metadata` keyed on the primary key.
    WHEN: called once per `GET /v1/influencers/{id}` request.
    WHY:  by-id lookup is the by-far-most-frequent read path (every
          orchestrator chat turn that lands on a real influencer
          eventually triggers one via public-api). PK index makes this
          O(1) on row count.

    Returns:
        The `InfluencerMetadata` model if a row exists; `None` if the
        influencer doesn't exist. The endpoint layer (Chunk B) maps
        `None` → HTTP 404 with the documented `not_found` error code
        per the parity contract.
    """
    pool = get_pool()

    record = await pool.fetchrow(
        f"""
        SELECT {_CONTRACT_COLUMNS_FOR_SELECT}
        FROM influencer_metadata
        WHERE id = $1
        """,
        influencer_id,
    )

    if record is None:
        return None

    return InfluencerMetadata(**dict(record))


async def list_paginated(
    limit: int,
    offset: int,
) -> list[InfluencerMetadata]:
    """Return up-to-`limit` influencers starting at `offset`, ordered by `id` ASC.

    WHAT: paginated SELECT with no WHERE filter — returns ALL
          influencers, both active + discontinued. Mobile filters
          discontinued client-side based on `is_active` per the
          contract.
    WHEN: called once per `GET /v1/influencers?limit=N&offset=M`
          request.
    WHY:  flat list-RPC matching the proposed DEP-013 contract shape
          (offset/limit plain ints). Ordering by `id ASC` gives
          deterministic pagination — without it, two requests with the
          same `(limit, offset)` could return different pages if the
          underlying row order shifts (Postgres makes no ordering
          guarantee on a bare SELECT). The PK index covers the ORDER BY
          so this is an index-ordered scan, not a sort.

    Args:
        limit: max rows to return; endpoint validates 1 ≤ limit ≤ 100
               per the mobile contract bounds (Chunk B).
        offset: number of rows to skip; endpoint validates offset ≥ 0
                (Chunk B).

    Returns:
        A list of `InfluencerMetadata` instances, length 0..limit. Empty
        list when `offset` exceeds the table's row count (mobile derives
        "no more pages" from `len(items) < limit`).
    """
    pool = get_pool()

    records = await pool.fetch(
        f"""
        SELECT {_CONTRACT_COLUMNS_FOR_SELECT}
        FROM influencer_metadata
        ORDER BY id ASC
        LIMIT $1 OFFSET $2
        """,
        limit,
        offset,
    )

    return [InfluencerMetadata(**dict(record)) for record in records]


async def list_trending(limit: int) -> list[InfluencerMetadata]:
    """Return the top-`limit` ACTIVE influencers ordered by `follower_count` DESC.

    WHAT: SELECT filtered to `is_active = 'active'`, ordered by
          `follower_count DESC`, capped at `limit`. The partial index
          `influencer_metadata_active_follower_count` declared in the
          001 migration makes this an index-only scan.
    WHEN: called once per `GET /v1/influencers/trending?limit=N` request
          (limit defaults to the same 20/min-1/max-100 bounds as the
          paginated list per the mobile contract).
    WHY:  the directive labels this a "trending lite v1" — the real
          trending pipeline (engagement-weighted, time-windowed ranking)
          is a future DEP if/when the simple follower-count ordering
          turns out to mislead the catalog UX. Today, follower_count
          DESC is the minimum-viable ordering that doesn't break the
          mobile endpoint contract.

    Args:
        limit: max trending rows to return; endpoint validates bounds
               same as list_paginated (Chunk B).

    Returns:
        A list of `InfluencerMetadata` instances, length 0..limit.
        Discontinued influencers never appear regardless of their
        `follower_count` value.
    """
    pool = get_pool()

    records = await pool.fetch(
        f"""
        SELECT {_CONTRACT_COLUMNS_FOR_SELECT}
        FROM influencer_metadata
        WHERE is_active = 'active'
        ORDER BY follower_count DESC
        LIMIT $1
        """,
        limit,
    )

    return [InfluencerMetadata(**dict(record)) for record in records]


# ===========================================================================
# RELATED FILES:
#   ../database.py                 — `get_pool()` accessor every query
#                                     above consumes
#   ../models/influencer_metadata.py
#                                  — Pydantic model returned by every
#                                     query
#   ../migrations/versions/001_initial_schema.py
#                                  — schema + indexes the queries above
#                                     ride on (the partial trending
#                                     index in particular)
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                                  — canonical InfluencerDto shape
# ===========================================================================
