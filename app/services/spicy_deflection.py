"""Native SFW-constrain + deflect for is_nsfw bots on the app surface.

Design references (docs/spicy-chat-gate-design-2026-06-28.md):
  * §4.1  — Native path: SFW-constrain + deflect (the behavior reversal)
  * §5.3  — sample deflection copy ("I can't go there with you here 🙈 —
            but I'm a lot freer over here 🔥" + "chat with me privately")
  * §5.4  — link_cta / cta_url / cta_label on ChatMessage (Sarvesh contract,
            amendment 2026-07-10 — snake_case on the wire to match every
            other nested ChatMessageDto field's @SerialName convention)
  * §11   — decision #12: launch scope Tara only, keep is_nsfw-driven
  * §19   — decision #19: prompt-driven primary + content-safety filter
            as a deterministic backstop on the app surface

The behavior we are REVERSING (Rishi 2026-06-28): today `is_nsfw=true` bots
skip the safety filter in the app (`content_safety.py:112`) and reply with
full adult content. Under this module + `NATIVE_DEFLECTION_ENABLED=true`:

  * The safety filter runs on both directions on the APP surface.
  * The system prompt gets an SFW-constraint suffix so the LLM's own
    behavior stays clothed-and-flirty.
  * When the user "clearly pushes for explicit" (heuristic) OR the LLM
    draft trips the filter, we swap in a deflection reply carrying a
    `link_cta` pointing at the bot's `spicy_landing_url`.

Web-spicy path (`surface="web_spicy"`) is unaffected — that surface is
routed to `user_chat_main_nsfw` unchanged, which is the whole point of
the surface flag.

Kept prompt-driven per decision #19; classifier upgrade is a fast-follow.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from services import content_safety


# ─── copy (design §5.3 — Rishi Motorola-tests before enabling) ─────────


# Injected onto the system_instructions for is_nsfw bots on the app surface
# only. Deliberately short + character-agnostic so it lifts every persona
# without rewriting their soul file. The design's "warmer on the inside"
# tease is what the model produces naturally when it declines — this
# constraint is what keeps it from going explicit in the first place.
SFW_CONSTRAINT_SUFFIX = (
    "\n\n[SURFACE=APP — stay SFW here]\n"
    "You are chatting inside the YRAL mobile app. On THIS surface, keep it "
    "SFW: flirt lightly, stay clothed, do not describe explicit sexual acts, "
    "genitals, or nudity. Do NOT roleplay explicit scenarios. If the user "
    "asks for explicit content, stay in character, decline warmly ("
    'the "warmer on the inside" tease), and tell them you\'re a lot freer '
    "on your private page — but don't paste any URLs yourself; the app "
    "renders the link separately. Everywhere else, stay yourself."
)


# The deflection message that replaces the assistant reply. Kept generic
# (no persona-specific pronouns) so this one string works for every
# is_nsfw bot; individual soul files can override once the concept is
# validated on Rishi's Motorola.
DEFLECTION_CONTENT = (
    "I can't go there with you here 🙈 — but I'm a lot freer over here 🔥"
)

# Label rendered on the tappable card mobile draws from `link_cta`.
DEFLECTION_CTA_LABEL = "chat with me privately"


# ─── heuristic: "clearly pushing for explicit" ──────────────────────────


# Same keyword list content_safety uses for the SFW-side NSFW filter.
# Reusing keeps the two paths consistent — if we ever tighten the list,
# both surfaces pick it up.
_PUSH_TRIGGERS = tuple(
    re.compile(rf"\b{re.escape(k)}\b", re.I) for k in content_safety.NSFW_KEYWORDS
)


def user_is_pushing_for_explicit(user_text: str | None) -> bool:
    """Prompt-driven primary; this heuristic is the deterministic
    secondary. Fires on the user's own message so we can deflect BEFORE
    running the LLM (saving the round-trip AND preventing the leaked
    tokens on the streaming path)."""
    if not user_text:
        return False
    return any(p.search(user_text) for p in _PUSH_TRIGGERS)


# ─── deflection result ─────────────────────────────────────────────────


@dataclass
class Deflection:
    """Route-facing payload for a deflection swap. `content` replaces
    the persisted assistant text; `link_cta` rides the ChatMessage on
    the wire so mobile renders the CTA card (design §5.4)."""

    content: str
    link_cta: dict


def _build(landing_url: str) -> Deflection:
    return Deflection(
        content=DEFLECTION_CONTENT,
        link_cta={"cta_url": landing_url, "cta_label": DEFLECTION_CTA_LABEL},
    )


def should_deflect_reply(reply_text: str | None) -> bool:
    """Post-generation backstop (decision #19). Run the same NSFW filter
    that content_safety runs on inbound — if the drafted reply would
    have been flagged, swap in the deflection before it reaches the
    app."""
    if not reply_text:
        return False
    result = content_safety.check_message(reply_text, is_nsfw_influencer=False)
    return bool(result.blocked and result.reason == "nsfw_content")


def maybe_deflect_for_user_push(
    user_text: str | None, landing_url: str | None
) -> Deflection | None:
    """Pre-generation deflection. Returns a Deflection to send in place
    of calling the LLM when the user is clearly pushing AND we have a
    landing URL to point at. If landing_url is None (bot has no spicy
    landing configured), we let the LLM handle it — the SFW-constraint
    prompt is the fallback."""
    if not landing_url:
        return None
    if not user_is_pushing_for_explicit(user_text):
        return None
    return _build(landing_url)


def deflect_generated_reply(
    reply_text: str | None, landing_url: str | None
) -> Deflection | None:
    """Post-generation deflection. Same logic path as the pre-generation
    branch, driven off the LLM's drafted reply. Returns None when the
    reply passes the filter (happy path) OR when the bot has no
    landing URL to inject."""
    if not landing_url:
        return None
    if not should_deflect_reply(reply_text):
        return None
    return _build(landing_url)


# ─── system-prompt injection ────────────────────────────────────────────


def sfw_constrained_prompt(base_prompt: str | None) -> str:
    """Append the SFW-constraint suffix to the influencer's system
    prompt. Idempotent: repeated calls do not stack the suffix (guards
    a future refactor that might accidentally call this twice on the
    same soul-file compose pipeline)."""
    base = base_prompt or ""
    if SFW_CONSTRAINT_SUFFIX.strip() in base:
        return base
    return base + SFW_CONSTRAINT_SUFFIX


# ─── two-knob rollout gate (Session 6 refinement 2026-07-10) ────────────


_TEST_USER_IDS_ENV = "NATIVE_DEFLECTION_TEST_USER_IDS"


def _test_user_allowlist() -> frozenset[str]:
    """Read the comma-separated per-user allowlist fresh on each call so
    admin-dashboard hot-edits take effect without a redeploy. Empty +
    unset both parse to the empty set."""
    raw = os.environ.get(_TEST_USER_IDS_ENV, "")
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def deflection_enabled_for(user_id: str | None) -> bool:
    """Rishi's two-knob rollout gate:

        deflection = NATIVE_DEFLECTION_ENABLED  # global
                     OR user_id in NATIVE_DEFLECTION_TEST_USER_IDS

    Global stays OFF while Rishi solo-Motorola-tests via the per-user
    allowlist; then he adds a small cohort; only then does he flip the
    global. Both knobs are hot-editable from the admin dashboard so
    the rollout doesn't need a redeploy.
    """
    from kill_switch import is_enabled  # local import — no boot-time cycle

    if is_enabled("native_deflection"):
        return True
    if not user_id:
        return False
    return user_id in _test_user_allowlist()


def rollout_state() -> dict:
    """Admin-dashboard snapshot: both knob values + the parsed allowlist
    size (not the actual IDs — those are PII-adjacent)."""
    from kill_switch import is_enabled

    return {
        "global_enabled": is_enabled("native_deflection"),
        "test_user_count": len(_test_user_allowlist()),
        "test_user_env": _TEST_USER_IDS_ENV,
    }


# ─── surface enforcement helper ─────────────────────────────────────────


def should_apply_app_deflection(
    *,
    is_nsfw: bool,
    surface: str,
    user_id: str | None,
) -> bool:
    """The three-way gate the route uses to decide whether ANY of the
    above logic fires. Combines the surface constraint (app only, per
    design §4.1) + the is_nsfw invariant + the two-knob rollout gate.
    Extracted here so the truth table lives in one place."""
    if not is_nsfw:
        return False
    if surface != "app":
        return False
    return deflection_enabled_for(user_id)
