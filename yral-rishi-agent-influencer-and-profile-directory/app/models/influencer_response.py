# ---------------------------------------------------------------------------
# influencer_response.py — Pydantic WIRE-SHAPE model for the influencer-
# and-profile-directory service's public catalog endpoints.
#
# ⭐ START HERE: ONE typed model — `InfluencerResponse` — mirrors the
# `InfluencerDto` wire contract at
# `interface-contracts/00-api-contract.md:107-119` exactly. It is the
# response Pydantic that `GET /v1/influencers` + `GET /v1/influencers/{id}`
# serialise into the HTTP envelope. Distinct from the INTERNAL persistence
# model `InfluencerMetadata` (in `influencer_metadata.py`) which carries
# 5 extra chat-ai-ported fields + tri-state `is_active` + nullable
# `avatar_url`.
#
# WHY TWO MODELS (per PR #148 round-8 header instruction)
# ------------------------------------------------------------------
# The persistence model `InfluencerMetadata` is a SUPERSET of the wire
# contract — it has fields like `name` / `personality_traits` /
# `initial_greeting` / `suggested_messages` / `metadata` that exist in
# the DB for chat-ai parity but are NOT in `InfluencerDto`. It also has
# `avatar_url: str | None` (nullable per chat-ai) + `is_active:
# Literal["active", "coming_soon", "discontinued"]` (tri-state) which
# both violate the wire contract's shape. The PR #148 round-8 file
# header of `influencer_metadata.py` carries an explicit 🛑 "DO NOT
# SERIALIZE THIS MODEL DIRECTLY TO AN HTTP RESPONSE" block instructing
# Chunk B to add THIS response model.
#
# Conversion happens at the endpoint layer via the `from_persistence`
# classmethod below, NOT inside the repository layer (which intentionally
# stays shape-agnostic about the wire contract).
#
# is_active WIRE VOCABULARY (per Chunk B authority decision 2026-05-25)
# ------------------------------------------------------------------
# Wire vocabulary is the 2-value `Literal["active", "discontinued"]`
# matching `InfluencerDto` verbatim. The persistence layer's 3-value
# vocabulary maps as follows:
#   `active`        → `"active"`  (catalog row, surfaced as active)
#   `coming_soon`   → `"active"`  (pre-launch row that the catalog still
#                                   exposes per coordinator's authority
#                                   guidance "only return active+
#                                   coming_soon influencers; never
#                                   discontinued"; mobile treats them
#                                   like active influencers today.
#                                   Future PR may add a `"coming_soon"`
#                                   wire value if mobile gains a
#                                   distinct UX for it; for now the
#                                   contract pins the 2-value vocabulary
#                                   so we map upward to `"active"`.)
#   `discontinued`  → never surfaced (the repository query filters
#                                   `is_active <> 'discontinued'` so a
#                                   discontinued row never reaches this
#                                   model; the `"discontinued"` Literal
#                                   value is retained for wire-contract
#                                   completeness but is dead code on
#                                   the response path today.)
#
# Net effect: `InfluencerResponse.is_active` is always `"active"` for
# the catalog endpoints today. The `"discontinued"` branch stays in the
# Literal so a future PR that adds an admin endpoint surfacing
# discontinued rows doesn't need to widen the type.
#
# avatar_url COERCION (per Chunk B authority decision 2026-05-25)
# ------------------------------------------------------------------
# The wire contract pins `avatar_url: string` (non-null). The
# persistence model has `avatar_url: str | None` (nullable per chat-ai
# port). The `from_persistence` classmethod coerces NULL → `""` (empty
# string). For Phase-1 parity data this is a no-op — every chat-ai-
# ported influencer has an `avatar_url` value. The empty-string fallback
# is the conservative-on-data-quality-gap behaviour; mobile sees `""`
# and renders its own placeholder, rather than the API leaking
# nullability into the wire shape.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# Literal — typed enumeration of permitted string values for the wire
# `is_active` field. Pinning `Literal["active", "discontinued"]` rather
# than a bare `str` makes a stray vocabulary regression (e.g. a future
# extension that surfaces `coming_soon` on the wire without widening
# the contract) raise ValidationError at the model boundary instead of
# silently propagating into mobile's JSON parsing.
from typing import Literal

# BaseModel — Pydantic 2.x base class every response shape extends.
# ConfigDict — declarative model config; this module uses `extra="forbid"`
# to reject unexpected keys (catches the regression where someone adds
# a field to the persistence model without thinking through whether it
# belongs on the wire — see the class definition below).
from pydantic import BaseModel, ConfigDict

# InfluencerMetadata — the INTERNAL persistence model `from_persistence`
# projects FROM. Importing the persistence-shape type here keeps the
# projection direction explicit + type-checked: the classmethod takes
# the 14-field persistence shape and returns the 9-field wire shape.
from app.models.influencer_metadata import InfluencerMetadata


class InfluencerResponse(BaseModel):
    """One row of the public catalog — the wire shape of `InfluencerDto`.

    WHAT: 9-field response Pydantic that the HTTP layer serialises into
          the `GET /v1/influencers` + `GET /v1/influencers/{id}` JSON
          response body. Mirrors `InfluencerDto` (the parity contract)
          field-by-field.
    WHEN: built once per row returned by the repository's catalog read
          methods, via the `from_persistence` classmethod below.
    WHY:  enforces the wire contract at the model layer rather than the
          endpoint layer. A future endpoint addition cannot accidentally
          leak the persistence model's extra fields into the response
          because it has to pass through this model first. The 9-field
          set is locked here and validated at serialisation time.
    """

    # Strict mode — extra keys raise ValidationError. Catches a
    # regression where someone adds a field to `InfluencerMetadata`
    # without thinking through whether it belongs on the wire. The
    # forbidden case is "developer extends persistence model, forgets
    # the response model, deploys; mobile gets an unexpected key in
    # the JSON body that they haven't updated for".
    model_config = ConfigDict(extra="forbid")

    # 9 fields — mirror `InfluencerDto` at
    # `interface-contracts/00-api-contract.md:107-119` verbatim. Field
    # order matches the contract's declared order so JSON-output order
    # (Pydantic preserves declaration order) is contract-aligned.
    id: str
    display_name: str
    bio: str
    avatar_url: str
    archetype: str
    is_nsfw: bool
    follower_count: int
    creator_user_id: str | None = None
    is_active: Literal["active", "discontinued"]

    @classmethod
    def from_persistence(
        cls,
        persistence_row: InfluencerMetadata,
    ) -> "InfluencerResponse":
        """Project an `InfluencerMetadata` persistence row onto the wire shape.

        WHAT: copies the 9 InfluencerDto-compatible fields from the
              persistence row; maps `coming_soon` → `"active"` on
              `is_active`; coerces NULL → `""` on `avatar_url`.
        WHEN: called once per row in the endpoint layer after the
              repository returns persistence-shape data.
        WHY:  enforces the wire contract at a single boundary. The
              endpoint code stays a thin pass-through:
              `[InfluencerResponse.from_persistence(r) for r in rows]`.
              All wire-contract policy decisions (which fields to
              expose, how to map tri-state, how to handle nullable)
              live in THIS classmethod, not scattered across the
              endpoint handlers.

        Note on `is_active` mapping: the catalog endpoints filter
        `is_active <> 'discontinued'` at the repository layer, so this
        classmethod only ever sees `active` or `coming_soon` inputs in
        practice. Both map to the wire's `"active"`. The
        `discontinued` → `"discontinued"` branch exists for type-
        completeness against the wire Literal but is unreachable
        through the catalog read path today.
        """
        wire_is_active: Literal["active", "discontinued"]
        if persistence_row.is_active == "discontinued":
            # Defensive branch — unreachable from the catalog read path
            # but kept for type-completeness so a future admin endpoint
            # that wants to surface discontinued rows can reuse this
            # classmethod without widening it.
            wire_is_active = "discontinued"
        else:
            # Maps both `active` AND `coming_soon` to wire `"active"`.
            wire_is_active = "active"

        # Empty-string fallback when avatar_url is NULL (chat-ai allows
        # NULL; the wire contract pins non-null). Mobile renders its
        # own placeholder for empty strings rather than receiving a
        # nullable response field.
        avatar_url_wire_value = (
            persistence_row.avatar_url
            if persistence_row.avatar_url is not None
            else ""
        )

        return cls(
            id=persistence_row.id,
            display_name=persistence_row.display_name,
            bio=persistence_row.bio,
            avatar_url=avatar_url_wire_value,
            archetype=persistence_row.archetype,
            is_nsfw=persistence_row.is_nsfw,
            follower_count=persistence_row.follower_count,
            creator_user_id=persistence_row.creator_user_id,
            is_active=wire_is_active,
        )


# ===========================================================================
# RELATED FILES:
#   influencer_metadata.py
#                                  — INTERNAL persistence model (14 fields,
#                                    tri-state is_active, nullable
#                                    avatar_url). `from_persistence` above
#                                    projects from there.
#   ../api/influencer_routes.py
#                                  — endpoint handlers that build instances
#                                    of THIS model via `from_persistence`.
#   ../repository/influencer_metadata_repository.py
#                                  — repository layer the endpoints fetch
#                                    persistence rows from.
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                                  — canonical InfluencerDto wire shape this
#                                    model mirrors verbatim.
# ===========================================================================
