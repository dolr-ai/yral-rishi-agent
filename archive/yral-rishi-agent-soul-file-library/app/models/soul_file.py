# ---------------------------------------------------------------------------
# soul_file.py — Pydantic models for the Soul File Library.
#
# ⭐ START HERE: two models exposed here.
#
#   1. `SoulFileLayer` — one row from `soul_file_layers`. The repository
#      layer returns this from `get_current()` / `list_versions()`.
#      Maps 1:1 to the table columns declared in the Alembic migration.
#
#   2. `ComposedPromptResponse` — the response shape the composer
#      returns + the HTTP route serialises. BYTE-IDENTICAL across
#      turns for the same `(influencer_id, user_segment)` per the
#      service's pre-spawn engineering contract. Matches
#      `interface-contracts/01-internal-rpc-contracts.md` verbatim
#      for the orchestrator → soul-file-library RPC.
#
# WHY NO ORM
# Per the Day-4 directive: "no SQLAlchemy ORM — direct asyncpg +
# Pydantic models keeps the dep tree thin per A2.1." Pydantic
# alone gives us typed parsing + validation at the FastAPI boundary
# + a serialisation contract for the HTTP layer.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# Valid `user_segment` values — keeping the literal type alias in this
# module means the API route + the composer + the repository all reuse
# the same canonical set + a new value would surface as a Pydantic
# 422 at the HTTP boundary.
UserSegment = Literal["new", "paying", "dormant"]


class SoulFileLayer(BaseModel):
    """One row from the `soul_file_layers` table.

    WHAT: typed model of a single Soul File row at one layer + scope.
    WHEN: built by the repository layer from an asyncpg `Record`.
    WHY:  central place for type-safety + serialisation; avoids each
          callsite indexing into raw Record tuples.
    """

    # UUID as string — asyncpg returns UUID as `uuid.UUID`; converting
    # to str at the model boundary keeps the rest of the codebase
    # string-typed (matches MessageDto pattern from Day 2).
    id: str

    # Layer 1, 2, 3, or 4 per E8.
    layer: int = Field(ge=1, le=4)

    # Empty string for L1, archetype name for L2, ai_influencer_id for L3,
    # user_segment for L4. The composer reads + writes this directly.
    scope_key: str

    # L3 rows only: the archetype the composer joins on to find the L2
    # row. NULL on L1/L2/L4 rows.
    archetype: str | None = None

    # The actual Soul File body — concatenated into the composed prompt
    # by the composer. Treat as opaque bytes once composed (per the
    # pre-spawn contract).
    body: str

    # Monotonically-increasing per `(layer, scope_key)`. The partial
    # unique index in the migration enforces exactly-one-current per
    # slot; older versions stay as history via `is_current=False`.
    version: int

    # TRUE for the row the composer reads; FALSE for historic versions
    # kept for rollback (`list_versions(...)`).
    is_current: bool

    # Timestamps are typed as datetime so callers can sort + format
    # without their own parser; serialisation to JSON is handled by
    # Pydantic's default ISO8601 emit.
    created_at: datetime

    # Future Prompt-Coach attribution — creator user_id who edited.
    # NULL on Day-4 seed rows + Day-4.5 data-port rows; populated when
    # the Prompt-Coach service lands.
    created_by: str | None = None


class ComposedPromptResponse(BaseModel):
    """The orchestrator-facing response from `GET /composed-prompt`.

    WHAT: the 3-field response object the orchestrator consumes per
          `interface-contracts/01-internal-rpc-contracts.md`.
    WHEN: returned by `four_layer_composer.compose(...)` + serialised
          by the HTTP route as JSON.
    WHY:  pre-spawn engineering contract: `layered_prompt` is opaque
          bytes the orchestrator passes to the LLM provider unchanged;
          `version_pin` lets a cache layer (Day-5+) invalidate when
          any underlying layer's version bumps; `cache_hit` is the
          honesty flag (Day-4 always False — no in-process cache yet).
    """

    # Concatenation of the 4 layers' bodies, separated by LAYER_SEPARATOR
    # from shared-config.yaml. BYTE-IDENTICAL across turns for the same
    # `(influencer_id, user_segment)` per the pre-spawn contract — no
    # timestamps, UUIDs, dates, or random ordering inside.
    layered_prompt: str

    # sha256(f"{l1.version}:{l2.version}:{l3.version}:{l4.version}")[:16].
    # 16 chars is enough entropy for the cache-invalidation use case +
    # keeps logs / Langfuse trace metadata short. Hex digits only.
    version_pin: str

    # Day-4 always False — no in-process cache yet. Day-5+ Redis cache
    # promote will flip this when serving from cache vs DB. Keeping
    # the field honest now means the orchestrator-side code reading
    # it (Langfuse trace metadata, etc.) doesn't need a Day-5 refactor.
    cache_hit: bool = False


# ===========================================================================
# RELATED FILES:
#   __init__.py                     — package marker
#   ../migrations/versions/001_initial_schema_and_seed.py
#                                  — Alembic migration declaring the schema
#                                    these models mirror
#   ../repository/soul_file_repository.py
#                                  — builds SoulFileLayer instances from
#                                    asyncpg Records
#   ../composer/four_layer_composer.py
#                                  — assembles ComposedPromptResponse from
#                                    the 4 fetched SoulFileLayer rows
#   ../api/composed_prompt_routes.py
#                                  — serialises ComposedPromptResponse as
#                                    the HTTP response body
#   ../../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                                  — the cross-service RPC contract this
#                                    response shape implements
# ===========================================================================
