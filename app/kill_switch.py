"""Emergency kill-switch for background Gemini callers.

Shipped 2026-05-30 after a shared-key Gemini rate-limit incident took
out V2 + chat-ai. Two-tier toggle:

  GEMINI_BACKGROUND_LOOPS_ENABLED (master) — global on/off
  Per-loop:
    ENABLE_PROACTIVE_LOOP
    ENABLE_NUDGE_LOOP
    ENABLE_QUALITY_SCORER
    ENABLE_MEMORY_EXTRACTION

A background Gemini caller is enabled iff master AND per-loop are both
"true". Defaults true so the kill-switch is opt-in-off — flipping the
master to false stops every loop without needing to set per-loop flags.

NOT covered (deliberately):
  - User-facing chat (POST /api/v1/chat/conversations/.../messages)
    is in the request path, not a background loop. The user's chat
    must keep working when the switch is flipped.
  - Wizard / coach / character_generator / recommendations are also
    user-triggered (per-request), not background loops.
  - ETL / integrity / email digest / streak / takeover sweep — these
    don't call Gemini at all.
"""

import os

_MASTER_KEY = "GEMINI_BACKGROUND_LOOPS_ENABLED"

_PER_LOOP_KEYS = {
    # Phase 1 loops (in #235, all gated): the Gemini-calling background
    # loops the incident-2026-05-29 spec required to stop.
    "proactive": "ENABLE_PROACTIVE_LOOP",
    "nudge": "ENABLE_NUDGE_LOOP",
    "quality_scorer": "ENABLE_QUALITY_SCORER",
    "memory_extraction": "ENABLE_MEMORY_EXTRACTION",
    # Phase 2 loops (this PR): the remaining background loops named
    # by the Diagnostic Session. memory_consolidation calls embeddings
    # which used to share the Gemini key; streak / integrity / digest /
    # etl don't call Gemini directly but get env-var symmetry so ops
    # can stop the whole background side with one config block.
    "memory_consolidation": "ENABLE_MEMORY_CONSOLIDATION",
    "streak": "ENABLE_STREAK_LOOP",
    "integrity": "ENABLE_INTEGRITY_LOOP",
    "email_digest": "ENABLE_EMAIL_DIGEST",
    "etl": "ENABLE_ETL_LOOP",
    # Phase 23.6 — scheduled check-in loop for skilled influencers
    # (nutrition_coach etc.). Symmetric with the proactive/nudge gates
    # so ops can stop just the skill side without disturbing legacy
    # 24h proactive or 5-min nudges.
    "skill_proactive": "ENABLE_SKILL_PROACTIVE_LOOP",
    # Phase 22.3 — nightly video-idea generation per active AI
    # influencer (~5 ideas/bot/day via internal_vllm). Cheap, but
    # symmetric env-flag so ops can stop the loop if it misbehaves
    # without touching the rest of the background fleet.
    "video_ideas": "ENABLE_VIDEO_IDEAS_LOOP",
    # Phase 21αβ.H11 — periodic LLM cost alerting (hourly Gemini cost
    # threshold + async error spike). Best-effort observability, no
    # provider calls — gated symmetric with the rest so ops can mute
    # alerting independently of the loops that produce the spend.
    "cost_alerts": "ENABLE_COST_ALERTS",
    # Phase 21γ.P34.M1 — Discovery Feed bot classification loop. One
    # LLM call per bot (backfill + on-create), defaults OFF so the
    # operator opts the backfill sweep in deliberately (avoids
    # surprise burn-through on first deploy).
    "influencer_classification": "ENABLE_INFLUENCER_CLASSIFICATION_LOOP",
    # Phase 21γ.P34.M2c — Stage A scoring + feed:global Redis blob.
    # Pure SELECT background job at 15-min cadence; defaults ON.
    # Disabling the loop drops the M2a endpoint back to its
    # fallback_select path (no mobile-visible failure).
    "feed_ranker": "ENABLE_FEED_RANKER_LOOP",
    # Phase 0 Request Images — nightly 04:00 UTC collage pre-gen for
    # LoRA-enabled active bots. Each pass generates ~$0.27/bot at
    # 6 images/collage; defaults OFF so operators explicitly opt-in
    # (lesson from the 2026-05-29 Gemini burn — new background loops
    # that spend money must never surprise on first deploy).
    "collage_pregen": "ENABLE_COLLAGE_PREGEN_LOOP",
    # Video generation — the loop that polls ComfyUI on the GPU box and
    # finishes generations. Defaults ON. The switch exists purely as a stop
    # button: if generation starts producing bad output or hammering the GPU,
    # ENABLE_VIDEOGEN_LOOP=false stops it in seconds without a redeploy.
    "videogen": "ENABLE_VIDEOGEN_LOOP",
}


def _env_true(key: str, default: bool = True) -> bool:
    """Treat 'true', '1', 'yes' as on (case-insensitive). Anything else
    is off (including empty string). Default applies when var unset."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


# Per-loop defaults. Most loops default ON (opt-in-OFF kill switch).
# A loop that defaults OFF requires the operator to deliberately
# enable it — used for new background processes where Rishi reviews
# sample output before unleashing a full sweep.
_DEFAULT_OFF_LOOPS: frozenset[str] = frozenset(
    {
        # Phase 21γ.P34.M1 — classification loop ships dormant. Rishi
        # reviews `POST /admin/discovery/classify-sample` output for 5
        # bots, then sets ENABLE_INFLUENCER_CLASSIFICATION_LOOP=true to
        # activate the full backfill.
        "influencer_classification",
        # Phase 0 Request Images — pre-gen loop ships dormant so
        # operators explicitly enable it. Spend-per-day is bounded
        # (one batch/bot/day at ~$0.27), but a stealth-on deploy
        # would surprise-charge before Sarvesh's mobile integration
        # is ready.
        "collage_pregen",
    }
)


def is_enabled(loop_name: str) -> bool:
    """Both master AND per-loop must be on for the caller to proceed.
    Unknown loop_name = enabled (forward-compat — new caller without
    a registered flag still gets through; add the flag in a follow-up)."""
    if not _env_true(_MASTER_KEY, default=True):
        return False
    per_loop_key = _PER_LOOP_KEYS.get(loop_name)
    if per_loop_key is None:
        return True
    per_loop_default = loop_name not in _DEFAULT_OFF_LOOPS
    return _env_true(per_loop_key, default=per_loop_default)


def current_state() -> dict:
    """For /admin/dashboard tile + diagnostics. Lists every gate
    enumerated above + its env-var name + current value."""
    return {
        "master": {
            "env": _MASTER_KEY,
            "enabled": _env_true(_MASTER_KEY, default=True),
        },
        "loops": {
            name: {
                "env": env_key,
                "enabled": _env_true(
                    env_key,
                    default=(name not in _DEFAULT_OFF_LOOPS),
                ),
                "effective": is_enabled(name),
                "default": "off" if name in _DEFAULT_OFF_LOOPS else "on",
            }
            for name, env_key in _PER_LOOP_KEYS.items()
        },
    }
