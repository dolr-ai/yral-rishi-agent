# ---------------------------------------------------------------------------
# influencer_metadata.py — Pydantic models for the influencer-and-profile-
# directory service.
#
# ⭐ START HERE: ONE typed model — `InfluencerMetadata` — mirrors the
# `influencer_metadata` table row shape AND the `InfluencerDto` declared
# at `interface-contracts/00-api-contract.md:107-119` so the repository
# can return rows that the endpoint layer serialises directly with no
# translation step.
#
# WHY THIS MODEL DOUBLES AS DB-ROW + HTTP-RESPONSE
# Per A8 + D2 — chat-ai's `InfluencerDto` field names are mirrored
# verbatim in the DB schema. The Pydantic model therefore matches both
# the table row + the wire shape. If a future API extension adds a
# response-only field (e.g. computed-at-serialisation), split into a
# `InfluencerMetadataRow` (DB) + `InfluencerResponse` (HTTP) pair. Per
# A2.1, ONE model is sufficient today.
#
# WHY `is_active: Literal["active", "discontinued"]` IN PYDANTIC?
# The DB CHECK constraint (declared in `001_initial_schema.py`) pins the
# vocabulary to those 2 values. Mirroring the constraint at the Pydantic
# layer means a stray third value from the DB (or from a future
# in-process construction) raises ValidationError at the model boundary
# instead of silently propagating. Matches the chat-ai-parity contract's
# explicit string-vocabulary shape.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class InfluencerMetadata(BaseModel):
    """One row from the `influencer_metadata` table.

    WHAT: typed representation of an AI Influencer's directory entry.
          Field names + types match the `InfluencerDto` shape declared
          in the parity contract verbatim.
    WHEN: returned by the repository layer for every read; serialised
          by the endpoint layer (Chunk B) into the `ApiResponse<T>`
          envelope.
    WHY:  one boundary for both DB-row → in-process AND in-process →
          HTTP response. No translation drift, no per-endpoint
          translator code.
    """

    # `model_config` per Pydantic 2.x — `extra="ignore"` so a stray
    # DB column added in a future migration doesn't trip ValidationError
    # before the model file gets updated.
    model_config = ConfigDict(extra="ignore")

    # AI Influencer UUID — matches `scope_key` on `soul_file_layers`
    # rows where `layer = 3`. The orchestrator's composer joins on this
    # value cross-service.
    id: str

    # Display + presentation fields.
    display_name: str
    bio: str
    avatar_url: str

    # archetype — joins to `soul_file_layers.scope_key WHERE layer=2`.
    # Free-form string at this layer per option (γ) — the soul-file
    # composer's `SoulFileDataIntegrityError` catches mismatches at
    # runtime.
    archetype: str

    # A10 routing flag — `True` triggers OpenRouter routing in the
    # orchestrator's provider matrix (Phase-2 wiring).
    is_nsfw: bool

    # Chat-ai-parity ranking signal. The directory's `/trending`
    # endpoint orders by this DESC.
    follower_count: int

    # NULL for system-seeded influencers, set for creator-studio-
    # spawned ones (post-cutover feature).
    creator_user_id: str | None = None

    # Pinned to the 2-value vocabulary per the DB CHECK constraint.
    # The third chat-ai value `'inactive'` is mapped to `'discontinued'`
    # by the PR-D2 ETL script (documented rule in the mapping doc).
    is_active: Literal["active", "discontinued"]


# ===========================================================================
# RELATED FILES:
#   ../migrations/versions/001_initial_schema.py
#                                  — DB schema this model mirrors
#   ../repository/influencer_metadata_repository.py
#                                  — repository that builds + returns
#                                    these instances
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                                  — canonical InfluencerDto shape
# ===========================================================================
