"""Skill check-in cadence backoff (chat-quality brief 2026-06-26).

The locked decision: when a user isn't responding to skill check-ins,
slow down — NEVER hard-stop. Each consecutive unanswered check-in
doubles the wait until SKILL_CHECKIN_BACKOFF_CAP_HOURS (~weekly); a
user reply resets the count automatically because the "unanswered"
count is "proactive messages since the user's last reply".

These tests pin both the math (so the ladder can't silently flatten
to a constant) and the wiring (so a refactor can't drop the helper
on the floor and revert to the old fixed cadence)."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

REPO = Path(__file__).resolve().parents[1]

# Behavioural tests need to import services.proactive, which transitively
# pulls in httpx via ai_client. CI has httpx; the local dev box often
# doesn't. Skip gracefully there.
try:
    import httpx  # noqa: F401

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

requires_httpx = pytest.mark.skipif(
    not _HTTPX_AVAILABLE, reason="httpx not installed (CI only)"
)


def _read_proactive_source() -> str:
    return (REPO / "app" / "services" / "proactive.py").read_text()


# ─── behavioural — the math ─────────────────────────────────────────────


@requires_httpx
def test_backoff_zero_or_one_unanswered_returns_base():
    """Either no proactive history (fresh) or one we just sent and the
    user might still reply — stay at base cadence."""
    from services.proactive import _backoff_cadence

    assert _backoff_cadence(6, 0) == 6
    assert _backoff_cadence(6, 1) == 6


@requires_httpx
def test_backoff_doubles_each_round():
    """The brief's explicit ladder for the default 6h cadence:
    6h → 12h → 24h → 48h → 96h, then capped at weekly."""
    from services.proactive import _backoff_cadence

    assert _backoff_cadence(6, 2) == 12
    assert _backoff_cadence(6, 3) == 24
    assert _backoff_cadence(6, 4) == 48
    assert _backoff_cadence(6, 5) == 96


@requires_httpx
def test_backoff_caps_at_weekly_but_never_hard_stops():
    """Locked decision: slow down, never hard-stop. Even after many
    unanswered rounds the cadence must remain finite (= continue to
    fire at the cap), not return infinity or 0."""
    from services.proactive import (
        _backoff_cadence,
        SKILL_CHECKIN_BACKOFF_CAP_HOURS,
    )

    assert SKILL_CHECKIN_BACKOFF_CAP_HOURS == 24 * 7  # ~weekly

    capped = _backoff_cadence(6, 6)
    assert capped == SKILL_CHECKIN_BACKOFF_CAP_HOURS

    # Pathological: a user who has ignored the bot for months still gets
    # check-ins at the weekly cap, NOT a one-shot stop.
    very_high = _backoff_cadence(6, 100)
    assert very_high == SKILL_CHECKIN_BACKOFF_CAP_HOURS
    assert very_high > 0


@requires_httpx
def test_backoff_scales_with_base_cadence():
    """If a skill ships with a non-default cadence (e.g. 12h), the
    doubling pattern still applies relative to that base."""
    from services.proactive import _backoff_cadence

    assert _backoff_cadence(12, 1) == 12
    assert _backoff_cadence(12, 2) == 24
    assert _backoff_cadence(12, 3) == 48


@requires_httpx
def test_backoff_user_reply_resets_to_base():
    """When the user replies, the "since last user reply" SQL returns 0
    for the next check-in. The cadence must drop straight back to base
    — no lingering memory of the prior unanswered streak."""
    from services.proactive import _backoff_cadence

    # Simulate: 4 unanswered → cadence 48h. User replies. Next check-in
    # sees count=0 → back to base.
    assert _backoff_cadence(6, 4) == 48
    assert _backoff_cadence(6, 0) == 6


# ─── wiring — the helper is actually consulted on the cadence path ──────


def test_send_skill_checkin_calls_count_unanswered_and_backoff():
    """A future refactor that drops the count_unanswered call or
    bypasses _backoff_cadence would silently revert to the pre-2026-06-26
    fixed cadence and re-flood non-responding users. Pin both call
    sites at the cadence-advance step."""
    src = _read_proactive_source()
    # Locate the send_skill_checkin function body.
    pos = src.find("async def send_skill_checkin(")
    assert pos > 0, "send_skill_checkin function moved or renamed"
    body = src[pos:]
    end = body.find("\nasync def ")
    body = body[:end] if end > 0 else body

    assert "count_unanswered_proactive" in body, (
        "skill-checkin cadence advance must read the unanswered count "
        "so the backoff ladder has an input"
    )
    assert "_backoff_cadence(" in body, (
        "skill-checkin cadence advance must pipe the count through "
        "_backoff_cadence — bypassing it reverts to the old flat cadence"
    )


def test_backoff_cap_constant_is_at_module_scope():
    """The cap is an operational knob — keep it at module scope so an
    operator (or a hot-edit) can find it without grepping function
    bodies. Same convention as PROACTIVE_CAP_WITHOUT_REPLY."""
    src = _read_proactive_source()
    assert "\nSKILL_CHECKIN_BACKOFF_CAP_HOURS = " in src
