# ---------------------------------------------------------------------------
# four_layer_composer.py — assembles the 4-layer Soul File for a given
# (influencer_id, user_segment) pair.
#
# ⭐ START HERE: ONE async public function — `compose(influencer_id,
# user_segment)` — returns a `ComposedPromptResponse` with three fields:
#   - `layered_prompt`: the concatenated bodies of L1 + L2 + L3 + L4
#     separated by LAYER_SEPARATOR (from shared-config.yaml).
#   - `version_pin`: 16-char sha256 of the four versions concatenated.
#   - `cache_hit`: False on Day 4 (no in-process cache yet). Day-5+
#     Redis cache promote will flip this when serving from cache.
#
# WHY BYTE-IDENTICAL PREFIX IS THE LOAD-BEARING CONTRACT
# Per `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md` verbatim: "Stable
# prompt prefix for provider-side caching — the composed Soul File
# prefix MUST be byte-identical across turns for the same
# (influencer_id, user_segment) pair. No timestamps, request IDs,
# UUIDs, current-date strings, or random bullet ordering inside the
# cached prefix." Provider-side prompt caching (Anthropic ephemeral
# cache, Gemini context cache, OpenAI prompt cache) keys on the
# byte-prefix; one differing byte = full cache miss = 3-10× TTFT
# regression on cache-eligible turns.
#
# WHY THE ARCHETYPE COLUMN ON L3 IS THE COMPOSER'S BRIDGE TO L2
# The Layer 2 row that composes into the prompt is the one matching
# the AI Influencer's archetype. The directive says "Layer 2 by
# archetype derived from influencer". The directive's spec'd column
# set didn't include an archetype-on-L3 column; this PR adds one
# (see Day-4 design carve-out flagged in PR body). Composer reads
# L3's `archetype` field + uses it as the L2 scope_key.
#
# WHY LAYER_SEPARATOR LIVES IN shared-config.yaml (NOT this file)
# Per C7 — values shared across services live in shared-config.yaml.
# Even though only this service reads it today, the orchestrator may
# want to verify expected separator-shape in its own opaque-bytes
# handling (defensively). Pulling it from shared-config keeps it
# changeable in ONE place; the role-comment in shared-config.yaml
# warns that changing it breaks every cached prompt prefix in flight.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import hashlib
from typing import Final

import yaml

from app.models.soul_file import ComposedPromptResponse, SoulFileLayer, UserSegment
from app.repository.soul_file_repository import (
    LAYER_ARCHETYPE,
    LAYER_GLOBAL,
    LAYER_PER_INFLUENCER,
    LAYER_PER_USER_SEGMENT,
    get_current,
)


# Resolved at module-load from shared-config.yaml so a typo gets caught
# at first import rather than at first request. The value is locked
# per C7 — see the file-header rationale for why changing it is a
# breaking change to every downstream cached prompt prefix.
def _load_layer_separator() -> str:
    """Load LAYER_SEPARATOR from shared-config.yaml at module-load.

    WHAT: reads shared-config.yaml from the service folder root + returns
          the `layer_separator` value.
    WHEN: invoked once at module import.
    WHY:  module-load fail-fast — a malformed shared-config raises at
          import not at first request. Centralised so a future config
          loader (per the template's C7 design note) can replace this
          single function without touching the composer.
    """
    # shared-config.yaml is at the service folder root, two levels up
    # from THIS file (`app/composer/four_layer_composer.py`).
    import pathlib

    config_path = pathlib.Path(__file__).resolve().parent.parent.parent / "shared-config.yaml"
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    separator = (data.get("soul_file_library") or {}).get("layer_separator")
    if not separator:
        raise RuntimeError(
            "shared-config.yaml is missing `soul_file_library.layer_separator`. "
            "This value is LOCKED per CONSTRAINTS C7 + the Day-4 directive — "
            "changing it breaks every cached prompt prefix downstream."
        )
    return str(separator)


LAYER_SEPARATOR: Final[str] = _load_layer_separator()


# Custom exception types — the HTTP route maps these to specific status
# codes (404 / 500). Keeping them in this module lets the composer
# emit them directly without importing from the HTTP layer (which
# would create a circular dep).


class InfluencerSoulFileMissingError(LookupError):
    """Raised when the Layer 3 row for an influencer_id doesn't exist.

    WHAT: signals "no per-influencer Soul File for that ID".
    WHEN: composer-side; mapped to HTTP 404 at the route boundary.
    WHY:  caller-facing condition (unknown influencer) vs data-integrity
          issues (L1/L2/L4 missing). Separating the two lets the HTTP
          layer return 404 (expected) vs 500 (unexpected).
    """


class SoulFileDataIntegrityError(RuntimeError):
    """Raised when L1, L2, or L4 is missing for a known input.

    WHAT: signals "seed data missing — composer cannot finish".
    WHEN: should never fire in steady state — the migration's seeds cover
          all of L1 + L2 (3 archetypes) + L4 (3 segments). If it fires,
          someone retired a seed row without replacement (use
          `repository.create_new_version` instead of `retire_current`).
    WHY:  data-integrity issues are 500 (our fault), not 404 (caller's
          fault). The defensive Day-4 test verifies the composer raises
          this clearly + doesn't silently return an empty prompt.
    """


async def compose(
    influencer_id: str,
    user_segment: UserSegment,
) -> ComposedPromptResponse:
    """Build the 4-layer Soul File prompt for `(influencer_id, user_segment)`.

    WHAT: fetches the 4 current rows (L1 global / L3 by influencer /
          L2 by archetype derived from L3 / L4 by user_segment),
          concatenates the bodies in order separated by LAYER_SEPARATOR,
          computes the version_pin, returns the response model.
    WHEN: called by the HTTP route handler in
          `app/api/composed_prompt_routes.py`; orchestrator calls
          that route per chat turn (Day-5+).
    WHY:  the byte-stable prompt prefix this returns is what
          provider-side prompt caching keys on. Cache hit on the
          prefix is what makes the 50%-faster-than-Python-chat-ai
          target reachable on prefix-heavy turns per E1.

    Raises:
        InfluencerSoulFileMissingError: L3 row for influencer_id absent.
        SoulFileDataIntegrityError: L1, L2-by-archetype, or L4 missing.
    """
    # -----------------------------------------------------------------------
    # Step 1 — fetch L3 first (it carries the archetype the L2 lookup needs).
    # If L3 is missing, the caller asked about an unknown influencer; emit
    # the LookupError-shaped exception the HTTP route turns into 404.
    # -----------------------------------------------------------------------
    layer_3 = await get_current(LAYER_PER_INFLUENCER, influencer_id)
    if layer_3 is None:
        raise InfluencerSoulFileMissingError(
            f"No current Layer 3 row for influencer_id={influencer_id!r}; "
            "Day-4 ships with no Layer 3 seed — populate via the Day-4.5 "
            "data port from chat-ai (F11) before this route returns 200."
        )

    if not layer_3.archetype:
        raise SoulFileDataIntegrityError(
            f"Layer 3 row for influencer_id={influencer_id!r} has NULL "
            "archetype. Each L3 row MUST carry the archetype the composer "
            "uses to find the matching L2 row. Fix the data port (Day 4.5)."
        )

    # -----------------------------------------------------------------------
    # Step 2 — fetch L1 (global), L2 (by L3's archetype), L4 (by segment).
    # Any of these missing is a data-integrity issue — the migration seeds
    # all three. Raise the 500-shaped exception so the HTTP route emits a
    # clear error rather than silently returning an empty prompt prefix.
    # -----------------------------------------------------------------------
    layer_1 = await get_current(LAYER_GLOBAL, "")
    if layer_1 is None:
        raise SoulFileDataIntegrityError(
            "Layer 1 (global) row missing. Re-run `alembic upgrade head` "
            "or restore the row manually — every composed prompt requires it."
        )

    layer_2 = await get_current(LAYER_ARCHETYPE, layer_3.archetype)
    if layer_2 is None:
        raise SoulFileDataIntegrityError(
            f"Layer 2 row for archetype={layer_3.archetype!r} missing. "
            f"Known archetypes from the Day-4 seed: companion / therapist / "
            f"coach. Either the data port populated L3 with an archetype not "
            f"in the L2 seed, or someone retired the L2 row."
        )

    layer_4 = await get_current(LAYER_PER_USER_SEGMENT, user_segment)
    if layer_4 is None:
        raise SoulFileDataIntegrityError(
            f"Layer 4 row for user_segment={user_segment!r} missing. The "
            f"Day-4 seed covers all of 'new' / 'paying' / 'dormant' — "
            f"someone retired this slot."
        )

    # -----------------------------------------------------------------------
    # Step 3 — concatenate bodies in L1 → L2 → L3 → L4 order with the
    # locked LAYER_SEPARATOR. No timestamps, UUIDs, dates, or per-call
    # random in the output string — that's the load-bearing byte-identity
    # contract.
    # -----------------------------------------------------------------------
    layered_prompt = LAYER_SEPARATOR.join([
        layer_1.body,
        layer_2.body,
        layer_3.body,
        layer_4.body,
    ])

    # -----------------------------------------------------------------------
    # Step 4 — version_pin = sha256(versions concat)[:16]. 16 chars is
    # plenty of entropy for the cache-invalidation use case + keeps logs
    # short. version_pin lives in the response field, NOT in the prompt
    # string — putting it inside the prompt would break byte-identity
    # whenever any layer bumps.
    # -----------------------------------------------------------------------
    version_pin = _compute_version_pin(layer_1, layer_2, layer_3, layer_4)

    return ComposedPromptResponse(
        layered_prompt=layered_prompt,
        version_pin=version_pin,
        cache_hit=False,
    )


def _compute_version_pin(
    l1: SoulFileLayer,
    l2: SoulFileLayer,
    l3: SoulFileLayer,
    l4: SoulFileLayer,
) -> str:
    """Return the 16-char sha256 prefix of the 4 layers' version concat.

    WHAT: sha256 over `"{l1.version}:{l2.version}:{l3.version}:{l4.version}"`,
          hex-encoded, first 16 chars.
    WHEN: called inside `compose(...)` once per turn.
    WHY:  hashes the version bumps that should invalidate any downstream
          cache — if any of the 4 layers' versions changes, the pin
          changes. 16 chars = 64 bits of entropy = plenty for the
          "did any layer change?" question without bulking up Langfuse
          trace metadata.
    """
    version_string = f"{l1.version}:{l2.version}:{l3.version}:{l4.version}"
    return hashlib.sha256(version_string.encode("utf-8")).hexdigest()[:16]


# ===========================================================================
# RELATED FILES:
#   __init__.py                     — package marker
#   ../repository/soul_file_repository.py
#                                  — get_current() the composer reads L1/L2/L3/L4 from
#   ../models/soul_file.py          — ComposedPromptResponse + SoulFileLayer models
#   ../api/composed_prompt_routes.py
#                                  — HTTP layer that catches the exceptions
#                                    raised here + maps to 404 / 500
#   ../../shared-config.yaml        — LAYER_SEPARATOR value loaded at
#                                    module import (LOCKED — changing it
#                                    breaks every cached prompt prefix)
#   ../../PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md
#                                  — the byte-identity engineering contract
#                                    this composer implements
#   ../../tests/test_composer.py    — byte-identity × 5 reps test that
#                                    proves the contract isn't drifting
# ===========================================================================
