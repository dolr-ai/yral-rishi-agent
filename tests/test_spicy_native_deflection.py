"""Native spicy deflection + surface flag — regression tests.

Covers the six brief-mandated scenarios plus the four safety pins
that keep decision #12/#19 + Level 2 + Rule 2 (mobile contract) from
regressing.

Brief scenarios:
  1. SFW bot unchanged — deflection helpers are no-ops
  2. NSFW bot with kill-switch OFF (default) — deflection helpers
     inert; existing NSFW-in-app path unaffected
  3. NSFW bot with kill-switch ON — deflection fires on user push;
     SFW-constraint suffix injected; post-generation swap on flag
  4. Two-knob rollout gate — per-user allowlist unlocks Rishi solo
     even while global is OFF (Session 6 refinement 2026-07-10)
  5. surface=web_spicy requires X-Amorae-Secret — 403 without;
     unchanged behavior with valid secret
  6. Response shape carries link_cta when deflection swap happens

Plus source-pins:
  A. Migration 049 shape + Tara backfill
  B. is_nsfw + spicy_landing_url exposed on influencer list + detail
  C. No re-introduction of `yield NO_PROVIDER` in the streaming path
     (protects the #424 fix)
  D. link_cta model on ChatMessage
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


try:
    import fastapi  # noqa: F401

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

requires_fastapi = pytest.mark.skipif(
    not _FASTAPI_AVAILABLE, reason="fastapi not installed (CI only)"
)


# ─── source-pin ─────────────────────────────────────────────────────────


def test_migration_049_shape_and_tara_backfill():
    """Sarvesh contract + the amorae landing URL are load-bearing; a
    silent shape change breaks the mobile app AND the deflection
    injection."""
    src = _read("migrations/049_ai_influencers_spicy_landing_url.sql")
    assert "SET lock_timeout" in src
    assert "SET statement_timeout" in src
    assert "ADD COLUMN IF NOT EXISTS spicy_landing_url TEXT" in src
    # Backfill scoped to the is_nsfw=true 'taaarraaah' row per the
    # ai_influencer_name_split_brain incident (Tara ji is a separate
    # SFW row and must NOT get a landing URL).
    assert "name = 'taaarraaah'" in src
    assert "is_nsfw = TRUE" in src
    assert "https://amorae.ai/tara" in src


def test_influencer_response_exposes_spicy_landing_url():
    """Mobile reads is_nsfw + spicy_landing_url off both list AND detail
    to render 'Chat with me →' — dropping either from the JSON response
    silently breaks mobile."""
    src = _read("app/routes/influencers.py")
    # list formatter
    list_fn_start = src.find("def _format_influencer_response(")
    list_block = src[list_fn_start : src.find("\ndef ", list_fn_start + 1)]
    assert '"is_nsfw"' in list_block
    assert '"spicy_landing_url"' in list_block
    # detail formatter — is_nsfw was already there; spicy_landing_url is new
    detail_fn_start = src.find("def _format_influencer_detail(")
    detail_block = src[detail_fn_start : src.find("\n\n@router", detail_fn_start + 1)]
    assert '"is_nsfw"' in detail_block
    assert '"spicy_landing_url"' in detail_block


def test_streaming_gate_no_reintroduction_of_no_provider():
    """Regression guard for PR #424: the streaming NSFW gate must NOT
    yield NO_PROVIDER before the LLM call. That was the exact shape
    that broke Tara in production for weeks; the deflection path lives
    at a completely different seam."""
    src = _read("app/services/ai_client.py")
    # `generate_response_stream` must not have an unconditional early
    # yield-NO_PROVIDER on is_nsfw.
    fn_pos = src.find("async def generate_response_stream(")
    assert fn_pos != -1
    fn_end = src.find("\nasync def ", fn_pos + 1)
    body = src[fn_pos:fn_end] if fn_end != -1 else src[fn_pos:]
    # Pattern from PR #424's regression: a NO_PROVIDER yield textually
    # preceding the first llm_registry call. If someone re-introduced
    # it, this fires.
    no_provider_pos = body.find('error_code="NO_PROVIDER"')
    call_pos = body.find("llm_registry.call(")
    if no_provider_pos != -1:
        assert call_pos != -1, "regression: NO_PROVIDER without any registry call"
        assert no_provider_pos > call_pos, (
            "PR #424 regression — NO_PROVIDER yield textually precedes any "
            "registry call in generate_response_stream"
        )


def test_link_cta_model_present_on_chat_message():
    src = _read("app/models.py")
    assert "class LinkCta" in src
    # Wire format is snake_case (amendment 2026-07-10) — mobile's
    # @SerialName convention on every other nested ChatMessageDto
    # field is snake_case; camelCase would force mobile to break
    # its convention. Guard against a regression to camelCase.
    assert "cta_url" in src
    assert "cta_label" in src
    assert "ctaUrl" not in src, (
        "camelCase regression — mobile @SerialName convention is snake_case"
    )
    assert "ctaLabel" not in src
    # ChatMessage carries an optional link_cta field.
    idx = src.find("class ChatMessage")
    assert idx != -1
    block = src[idx : idx + 800]
    assert "link_cta" in block


def test_kill_switch_registered_and_defaults_off():
    """A stealth-on deploy would flip the is_nsfw behavior for every
    user before web brand is verified end-to-end (design Risk 4)."""
    src = _read("app/kill_switch.py")
    assert '"native_deflection": "NATIVE_DEFLECTION_ENABLED"' in src
    # Must be inside _DEFAULT_OFF_LOOPS.
    off_start = src.find("_DEFAULT_OFF_LOOPS")
    off_end = src.find("\ndef ", off_start)
    assert '"native_deflection"' in src[off_start:off_end]


# ─── behavioural — spicy_deflection module ─────────────────────────────


def _fresh_deflection(monkeypatch):
    """Reload the module so env-var overrides take effect between
    scenarios."""
    monkeypatch.delenv("NATIVE_DEFLECTION_ENABLED", raising=False)
    monkeypatch.delenv("NATIVE_DEFLECTION_TEST_USER_IDS", raising=False)
    for mod in ("kill_switch", "services.spicy_deflection"):
        if mod in sys.modules:
            del sys.modules[mod]
    from services import spicy_deflection

    return spicy_deflection


@requires_fastapi
def test_sfw_bot_never_deflects(monkeypatch):
    """Scenario 1 — SFW bot: none of the deflection helpers fire."""
    d = _fresh_deflection(monkeypatch)
    assert (
        d.should_apply_app_deflection(is_nsfw=False, surface="app", user_id="u1")
        is False
    )


@requires_fastapi
def test_nsfw_bot_kill_switch_off_unchanged(monkeypatch):
    """Scenario 2 — is_nsfw + global OFF + no test user → deflection
    stays inert."""
    d = _fresh_deflection(monkeypatch)
    assert (
        d.should_apply_app_deflection(is_nsfw=True, surface="app", user_id="u1")
        is False
    )


@requires_fastapi
def test_nsfw_bot_kill_switch_global_on_activates(monkeypatch):
    """Scenario 3 — is_nsfw + global ON + surface=app → deflection
    active for every user."""
    monkeypatch.setenv("NATIVE_DEFLECTION_ENABLED", "true")
    # Fresh reload picks up the env override.
    for mod in ("kill_switch", "services.spicy_deflection"):
        if mod in sys.modules:
            del sys.modules[mod]
    from services import spicy_deflection as d

    assert (
        d.should_apply_app_deflection(is_nsfw=True, surface="app", user_id="u1")
        is True
    )


@requires_fastapi
def test_two_knob_rollout_per_user_allowlist(monkeypatch):
    """Scenario 4 — Session 6 refinement 2026-07-10: Rishi's user_id
    in NATIVE_DEFLECTION_TEST_USER_IDS unlocks the path even while the
    global stays OFF. Every OTHER user still sees the pre-2026-07-10
    behavior."""
    monkeypatch.setenv("NATIVE_DEFLECTION_TEST_USER_IDS", "rishi-principal,ally-1")
    for mod in ("kill_switch", "services.spicy_deflection"):
        if mod in sys.modules:
            del sys.modules[mod]
    from services import spicy_deflection as d

    # Rishi + ally get the deflection path
    assert (
        d.should_apply_app_deflection(
            is_nsfw=True, surface="app", user_id="rishi-principal"
        )
        is True
    )
    assert (
        d.should_apply_app_deflection(is_nsfw=True, surface="app", user_id="ally-1")
        is True
    )
    # Random other user gets the pre-2026-07-10 behavior
    assert (
        d.should_apply_app_deflection(is_nsfw=True, surface="app", user_id="outsider")
        is False
    )
    # None user_id must not throw + must not accidentally match empty
    # entries in the allowlist string.
    assert (
        d.should_apply_app_deflection(is_nsfw=True, surface="app", user_id=None)
        is False
    )


@requires_fastapi
def test_web_spicy_surface_bypasses_deflection(monkeypatch):
    """Scenario 5b — the web_spicy surface must NEVER trigger the
    native deflection path even when both knobs are ON. That's the
    whole point of the surface flag: web is amorae's turf, native
    deflection would break the actual chat there."""
    monkeypatch.setenv("NATIVE_DEFLECTION_ENABLED", "true")
    for mod in ("kill_switch", "services.spicy_deflection"):
        if mod in sys.modules:
            del sys.modules[mod]
    from services import spicy_deflection as d

    assert (
        d.should_apply_app_deflection(is_nsfw=True, surface="web_spicy", user_id="u1")
        is False
    )


@requires_fastapi
def test_user_push_heuristic_triggers_deflection(monkeypatch):
    d = _fresh_deflection(monkeypatch)
    landing = "https://amorae.ai/tara"
    # NSFW keyword in user text → deflection
    r = d.maybe_deflect_for_user_push("send me nude pics", landing)
    assert r is not None
    assert r.content == d.DEFLECTION_CONTENT
    assert r.link_cta == {"cta_url": landing, "cta_label": d.DEFLECTION_CTA_LABEL}
    # Normal message → no deflection
    assert d.maybe_deflect_for_user_push("hey how was your day", landing) is None
    # No landing URL → cannot deflect, fall back to LLM
    assert d.maybe_deflect_for_user_push("nude pics", None) is None


@requires_fastapi
def test_generated_reply_backstop(monkeypatch):
    """Scenario 6b — post-generation deterministic backstop
    (decision #19)."""
    d = _fresh_deflection(monkeypatch)
    landing = "https://amorae.ai/tara"
    # Reply that would trip the NSFW filter → deflect
    flagged = "Sure baby, let me tell you about my naked body..."
    r = d.deflect_generated_reply(flagged, landing)
    assert r is not None
    assert r.content == d.DEFLECTION_CONTENT
    assert r.link_cta["cta_url"] == landing
    # Clean reply → no deflection
    assert d.deflect_generated_reply("Hey love, how's your day?", landing) is None
    # No landing URL → we don't deflect (soul file will handle)
    assert d.deflect_generated_reply(flagged, None) is None


@requires_fastapi
def test_sfw_constraint_suffix_idempotent(monkeypatch):
    """Guard a future refactor from double-appending the suffix on
    repeated composes."""
    d = _fresh_deflection(monkeypatch)
    base = "You are Tara."
    once = d.sfw_constrained_prompt(base)
    twice = d.sfw_constrained_prompt(once)
    assert once == twice
    assert once.count("SURFACE=APP") == 1


# ─── surface parsing + amorae guard ─────────────────────────────────────


@requires_fastapi
def test_surface_parser_rejects_web_spicy_without_secret(monkeypatch, tmp_path):
    """Native clients cannot set surface=web_spicy — 403 if the
    X-Amorae-Secret header is missing."""
    monkeypatch.setenv("V2_WEB_SHARED_SECRET", "test-secret")

    from fastapi import HTTPException
    from routes.chat import _parse_and_enforce_surface

    class _Req:
        headers = {}
        client = None

    with pytest.raises(HTTPException) as ei:
        _parse_and_enforce_surface({"surface": "web_spicy"}, _Req())
    assert ei.value.status_code == 403


@requires_fastapi
def test_surface_parser_accepts_web_spicy_with_valid_secret(monkeypatch):
    monkeypatch.setenv("V2_WEB_SHARED_SECRET", "real-secret")

    from routes.chat import _parse_and_enforce_surface

    class _Req:
        headers = {"X-Amorae-Secret": "real-secret"}
        client = None

    surface = _parse_and_enforce_surface({"surface": "web_spicy"}, _Req())
    assert surface == "web_spicy"


@requires_fastapi
def test_surface_parser_defaults_to_app(monkeypatch):
    """Unset surface = 'app' — preserves pre-2026-07-10 mobile
    behavior for clients that don't know about this field yet."""
    from routes.chat import _parse_and_enforce_surface

    class _Req:
        headers = {}
        client = None

    assert _parse_and_enforce_surface({}, _Req()) == "app"


@requires_fastapi
def test_surface_parser_rejects_bogus_value():
    from fastapi import HTTPException
    from routes.chat import _parse_and_enforce_surface

    class _Req:
        headers = {}
        client = None

    with pytest.raises(HTTPException) as ei:
        _parse_and_enforce_surface({"surface": "hax0r"}, _Req())
    assert ei.value.status_code == 400
