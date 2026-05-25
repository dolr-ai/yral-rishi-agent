# ---------------------------------------------------------------------------
# influencer_metadata.py — Pydantic models for the influencer-and-profile-
# directory service.
#
# ⭐ START HERE: ONE typed model — `InfluencerMetadata` — represents
# the INTERNAL PERSISTENCE shape of an `influencer_metadata` row. It is
# a SUPERSET of the `InfluencerDto` wire contract declared at
# `interface-contracts/00-api-contract.md:107-119`, NOT a 1:1 mirror.
#
# 🛑 DO NOT SERIALIZE THIS MODEL DIRECTLY TO AN HTTP RESPONSE.
# Per A8 + A16 the wire shape is `InfluencerDto`. This model carries
# fields + shapes that violate the DTO contract:
#   - Extra fields ported from chat-ai per A4/A8 in PR #148 round-5
#     (`name`, `personality_traits`, `initial_greeting`,
#     `suggested_messages`, `metadata`) that are NOT in `InfluencerDto`.
#   - `avatar_url: str | None` (nullable here per chat-ai shape) vs
#     `InfluencerDto.avatar_url` which the contract may pin non-null.
#   - `is_active: Literal["active", "coming_soon", "discontinued"]`
#     (tri-state per chat-ai vocabulary) vs the `InfluencerDto.is_active`
#     contract vocabulary; Chunk B's endpoint layer must decide whether
#     `coming_soon` rows are filtered out of public catalog responses
#     or surfaced under a different value (A8 parity requires explicit
#     mapping, not silent passthrough).
#
# Chunk B (endpoints) MUST add a separate response-shape (either a
# `InfluencerMetadataResponse` Pydantic model with only the DTO field
# set, or a per-route `model_dump(include={...})` projection) so the
# HTTP envelope contains the contract shape exactly. NOT done now
# because per A2.1, Chunk B owns endpoint design + the per-endpoint
# filtering policy for `is_active`+`avatar_url` is an endpoint
# decision, not a model decision. Building the response model in this
# PR would be speculative about Chunk B's filtering choices.
#
# WHY `is_active: Literal["active", "coming_soon", "discontinued"]` IN PYDANTIC?
# The DB CHECK constraint (declared in `001_initial_schema.py`) pins the
# vocabulary to those 3 values — chat-ai's tri-state shape ported
# verbatim in PR #148 round-5 per A4/A8 (was 2-value `active|discontinued`
# in earlier rounds before the chat-ai port). Mirroring the constraint
# at the Pydantic layer means a stray fourth value from the DB (or from
# a future in-process construction) raises ValidationError at the model
# boundary instead of silently propagating. Matches the chat-ai-parity
# contract's explicit string-vocabulary shape. The endpoint layer is
# responsible for any user-facing filtering (e.g. hiding `coming_soon`
# rows on a public catalog endpoint); the repository + model surface
# all three values unfiltered.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class InfluencerMetadata(BaseModel):
    """One row from the `influencer_metadata` table — INTERNAL PERSISTENCE shape.

    WHAT: typed representation of an AI Influencer's directory row as it
          lives in Postgres. Field set is a SUPERSET of the
          `InfluencerDto` wire contract (carries chat-ai-ported fields
          like `name` / `personality_traits` / `initial_greeting` /
          `suggested_messages` / `metadata` that the contract does not
          expose, plus tri-state `is_active` + nullable `avatar_url`).
    WHEN: returned by the repository layer for every read. NOT serialised
          directly to HTTP — see the file-header 🛑 block. Chunk B's
          endpoint handlers project this onto the `InfluencerDto` wire
          shape before returning the `ApiResponse<T>` envelope.
    WHY:  one boundary for DB-row → in-process. Repository code stays
          shape-agnostic about the wire contract; per-endpoint
          response shaping (which fields to expose, how to map
          `coming_soon`+nullable `avatar_url` per the InfluencerDto
          contract) lives in Chunk B where the endpoint-specific
          decisions live.
    """

    # `model_config` per Pydantic 2.x — `extra="ignore"` so a stray
    # DB column added in a future migration doesn't trip ValidationError
    # before the model file gets updated.
    model_config = ConfigDict(extra="ignore")

    # AI Influencer UUID — matches `scope_key` on `soul_file_layers`
    # rows where `layer = 3`. The orchestrator's composer joins on this
    # value cross-service.
    id: str

    # `name` — chat-ai-parity unique slug-style identifier (e.g. "tara").
    # Distinct from `display_name`. Round-5 addition per chat-ai schema
    # port; not in InfluencerDto contract (mobile reads display_name) so
    # endpoint serialization MAY exclude this field from the mobile-
    # facing response (Chunk B's endpoint handler decides per the
    # InfluencerDto wire shape).
    name: str

    # Display + presentation fields.
    display_name: str
    bio: str

    # `avatar_url` — NULL allowed per chat-ai's schema (round-5 relax
    # from round-1's NOT NULL). The InfluencerDto contract types it as
    # `string` (non-null) — Chunk B's endpoint handler serialises NULL
    # → empty string for mobile compatibility.
    avatar_url: str | None = None

    # archetype — joins to `soul_file_layers.scope_key WHERE layer=2`.
    # Free-form string at this layer per option (γ) — the soul-file
    # composer's `SoulFileDataIntegrityError` catches mismatches at
    # runtime.
    archetype: str

    # A10 routing flag — `True` triggers OpenRouter routing in the
    # orchestrator's provider matrix (Phase-2 wiring).
    is_nsfw: bool

    # v2-only ranking signal. The directory's `/trending` endpoint
    # orders by this DESC. Chat-ai doesn't track this in `ai_influencers`
    # today; if production analytics surface follower counts later,
    # they backfill here.
    follower_count: int

    # NULL for system-seeded influencers, set for creator-studio-
    # spawned ones (post-cutover feature). ETL maps from chat-ai's
    # `parent_principal_id` column.
    creator_user_id: str | None = None

    # Pinned to the chat-ai tri-state vocabulary per the DB CHECK
    # constraint. Round-5 expanded from round-1's 2-value: chat-ai's
    # data uses 'coming_soon' as a real third state for soft-launch
    # influencers + must port per A4. The mobile InfluencerDto contract
    # declares only 'active' + 'discontinued'; Chunk B's endpoint
    # handler filters out 'coming_soon' rows so mobile never sees that
    # value over the wire (preserves A8 wire-shape parity while keeping
    # A4 data fidelity).
    is_active: Literal["active", "coming_soon", "discontinued"]

    # `personality_traits` — chat-ai-parity JSONB. Structured personality
    # metadata used by the orchestrator's prompt-composition + routing.
    # Round-5 addition per chat-ai schema port. Empty `{}` default at
    # the DB layer means this field is always populated (no NULL).
    personality_traits: dict[str, Any] = Field(default_factory=dict)

    # `initial_greeting` — chat-ai-parity field. The first message the
    # influencer sends when a user starts a new conversation, or NULL
    # for influencers without a scripted greeting. Round-5 addition.
    initial_greeting: str | None = None

    # `suggested_messages` — chat-ai-parity JSONB array of strings.
    # Conversation-starter prompts shown under the message box. Empty
    # `[]` default at the DB layer. Round-5 addition.
    suggested_messages: list[str] = Field(default_factory=list)

    # `metadata` — chat-ai-parity catch-all JSONB column for extensions
    # that don't yet warrant a dedicated column. Empty `{}` default.
    # Round-5 addition. NOTE: Pydantic v2 reserves the `model_` prefix
    # for internal use, so the field name `metadata` collides with
    # BaseModel's `metadata` if any. Pydantic v2 actually only reserves
    # `model_*` (not bare `metadata`); the field name `metadata` is
    # safe here. If a future Pydantic version reserves `metadata`, we
    # alias to `influencer_metadata_extensions` or similar via
    # `Field(alias="metadata")`.
    metadata: dict[str, Any] = Field(default_factory=dict)


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
