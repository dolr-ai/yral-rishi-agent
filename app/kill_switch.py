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
    "proactive": "ENABLE_PROACTIVE_LOOP",
    "nudge": "ENABLE_NUDGE_LOOP",
    "quality_scorer": "ENABLE_QUALITY_SCORER",
    "memory_extraction": "ENABLE_MEMORY_EXTRACTION",
}


def _env_true(key: str, default: bool = True) -> bool:
    """Treat 'true', '1', 'yes' as on (case-insensitive). Anything else
    is off (including empty string). Default applies when var unset."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


def is_enabled(loop_name: str) -> bool:
    """Both master AND per-loop must be on for the caller to proceed.
    Unknown loop_name = enabled (forward-compat — new caller without
    a registered flag still gets through; add the flag in a follow-up)."""
    if not _env_true(_MASTER_KEY, default=True):
        return False
    per_loop_key = _PER_LOOP_KEYS.get(loop_name)
    if per_loop_key is None:
        return True
    return _env_true(per_loop_key, default=True)


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
                "enabled": _env_true(env_key, default=True),
                "effective": is_enabled(name),
            }
            for name, env_key in _PER_LOOP_KEYS.items()
        },
    }
