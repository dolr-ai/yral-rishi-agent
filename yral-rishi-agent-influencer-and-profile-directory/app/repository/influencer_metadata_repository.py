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
# All 3 queries SELECT the 14 columns the `InfluencerMetadata` Pydantic
# model carries (the 9 InfluencerDto-contract columns + the 5 chat-ai-
# port additions: `name`, `personality_traits`, `initial_greeting`,
# `suggested_messages`, `metadata`). The DB also stores 3 audit
# columns (`source`, `created_at`, `updated_at`) NOT in the projection
# — those aren't part of the Pydantic model + don't need to leave the
# DB for today's read paths. Future endpoints that want them (e.g.
# Last-Modified caching headers) extend the model + the SELECT list
# together.
#
# JSONB columns (`personality_traits`, `suggested_messages`, `metadata`)
# are normalised via the `_record_to_model` helper below to handle
# asyncpg's two return shapes (raw JSON string vs decoded Python
# object) consistently.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import json
from typing import Any, Final

import asyncpg

from app.database import get_pool
from app.models.influencer_metadata import InfluencerMetadata


# The full set of columns the repository projects on every read.
# Round-5 expanded from round-1's 9-column InfluencerDto-only set to
# include the 5 round-5 chat-ai-port additions (`name`,
# `personality_traits`, `initial_greeting`, `suggested_messages`,
# `metadata`). Audit columns (`source`, `created_at`, `updated_at`)
# remain excluded from the projection — they exist in the schema but
# aren't surfaced via the `InfluencerMetadata` Pydantic model today.
# Held as a constant so all 3 queries project the same set + an
# accidental drift (e.g. one query forgetting `creator_user_id`) is
# impossible.
_CONTRACT_COLUMNS_FOR_SELECT: Final[str] = (
    "id, name, display_name, bio, avatar_url, archetype, is_nsfw, "
    "follower_count, creator_user_id, is_active, personality_traits, "
    "initial_greeting, suggested_messages, metadata"
)


# JSONB columns in the schema. asyncpg may return JSONB values as
# raw JSON strings (no codec registered, the default) OR as Python
# objects (jsonb codec registered). The repository normalises both
# forms via `_parse_jsonb_value` before handing rows to the Pydantic
# model — the model's `dict[str, Any]` / `list[str]` type hints
# would otherwise raise ValidationError on a string-shaped value.
# Round-5 addition; same shape as user-memory-service's
# `_parse_media_urls` helper in `app/api/conversation_routes.py`.
_JSONB_COLUMN_NAMES: Final[frozenset[str]] = frozenset({
    "personality_traits",
    "suggested_messages",
    "metadata",
})


def _parse_jsonb_value(raw: Any) -> Any:
    """Normalise an asyncpg JSONB return value to a Python object.

    WHAT: parses `raw` as JSON if it's a string; returns it unchanged
          if it's already a Python object (asyncpg JSONB codec
          configurations return dicts/lists directly).
    WHEN: per JSONB column per row in the repository's `_record_to_model`
          conversion.
    WHY:  asyncpg's JSONB handling differs between configurations. A
          single normalisation function prevents "got str, expected
          dict" Pydantic ValidationErrors from silently surfacing as
          500s on the catalog endpoint.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _record_to_model(record: asyncpg.Record) -> InfluencerMetadata:
    """Convert one asyncpg Record into an `InfluencerMetadata` instance.

    WHAT: copies the record's fields into a dict, normalises JSONB
          columns via `_parse_jsonb_value`, hands the dict to
          `InfluencerMetadata(**...)`.
    WHEN: per row returned by any repository read method.
    WHY:  centralises the JSONB-normalisation + Pydantic-construction
          step so the 3 read methods share identical record-to-model
          semantics. A regression that forgot to parse JSONB for one
          method but not another would silently 500 in one endpoint.
    """
    record_dict = dict(record)
    for jsonb_column_name in _JSONB_COLUMN_NAMES:
        if jsonb_column_name in record_dict:
            record_dict[jsonb_column_name] = _parse_jsonb_value(
                record_dict[jsonb_column_name]
            )
    return InfluencerMetadata(**record_dict)


async def get_by_id(influencer_id: str) -> InfluencerMetadata | None:
    """Return the catalog-visible influencer matching `id = $1`, or None.

    WHAT: SELECT against `influencer_metadata` keyed on the primary key,
          filtered to `is_active <> 'discontinued'`. Returns ANY non-
          discontinued row (`active` or `coming_soon`).
    WHEN: called once per `GET /v1/influencers/{id}` request.
    WHY:  by-id lookup is the by-far-most-frequent read path (every
          orchestrator chat turn that lands on a real influencer
          eventually triggers one via public-api). PK index makes this
          O(1) on row count.

          The `is_active <> 'discontinued'` filter enforces the catalog
          authority (per Chunk B coordinator routing 2026-05-25): the
          public catalog never surfaces discontinued rows. Filtering at
          this layer instead of the endpoint layer makes "discontinued
          rows are invisible to the catalog" a property of the data
          access surface itself — adding a new catalog endpoint cannot
          accidentally surface them, because the SQL would never
          return them.

    Returns:
        The `InfluencerMetadata` model if a non-discontinued row exists;
        `None` if the influencer doesn't exist OR is discontinued. The
        endpoint layer (Chunk B) maps both cases to HTTP 404 with the
        documented `not_found` error code per the parity contract — the
        404 is intentionally indistinguishable between "no such id" and
        "discontinued" so an external probe can't enumerate which ids
        the catalog has soft-deleted vs never had.
    """
    pool = get_pool()

    record = await pool.fetchrow(
        f"""
        SELECT {_CONTRACT_COLUMNS_FOR_SELECT}
        FROM influencer_metadata
        WHERE id = $1 AND is_active <> 'discontinued'
        """,
        influencer_id,
    )

    if record is None:
        return None

    return _record_to_model(record)


async def list_paginated(
    limit: int,
    offset: int,
) -> list[InfluencerMetadata]:
    """Return up-to-`limit` catalog-visible influencers, ordered by `id` ASC.

    WHAT: paginated SELECT filtered to `is_active <> 'discontinued'`,
          returning rows with `is_active IN ('active', 'coming_soon')`.
          Ordered by `id` ASC. Discontinued rows never appear.
    WHEN: called once per `GET /v1/influencers?limit=N&offset=M`
          request.
    WHY:  flat list-RPC matching the proposed DEP-013 contract shape
          (offset/limit plain ints). Ordering by `id ASC` gives
          deterministic pagination — without it, two requests with the
          same `(limit, offset)` could return different pages if the
          underlying row order shifts (Postgres makes no ordering
          guarantee on a bare SELECT). The PK index covers the ORDER BY
          so this is an index-ordered scan, not a sort.

          The `is_active <> 'discontinued'` filter enforces the catalog
          authority (per Chunk B coordinator routing 2026-05-25): the
          public catalog never surfaces discontinued rows. Filtering at
          the SQL layer (not the endpoint layer) means pagination bounds
          are correct — `limit=20, offset=0` returns up to 20
          catalog-visible rows, not 20-minus-N-discontinued rows. The
          partial trending index doesn't cover this scan but the PK
          index does (sequential id scan); the predicate is evaluated
          per row.

    Args:
        limit: max rows to return; endpoint validates 1 ≤ limit ≤ 100
               per the mobile contract bounds (Chunk B).
        offset: number of rows to skip; endpoint validates offset ≥ 0
                (Chunk B).

    Returns:
        A list of `InfluencerMetadata` instances, length 0..limit, none
        of which are discontinued. Empty list when `offset` exceeds the
        catalog-visible row count (mobile derives "no more pages" from
        `len(items) < limit`).
    """
    pool = get_pool()

    records = await pool.fetch(
        f"""
        SELECT {_CONTRACT_COLUMNS_FOR_SELECT}
        FROM influencer_metadata
        WHERE is_active <> 'discontinued'
        ORDER BY id ASC
        LIMIT $1 OFFSET $2
        """,
        limit,
        offset,
    )

    return [_record_to_model(record) for record in records]


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

    return [_record_to_model(record) for record in records]


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
