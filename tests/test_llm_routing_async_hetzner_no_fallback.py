"""Routing policy tests.

Pins the contract Rishi asked for:
- Async background processes (the 6 text ones): `hetzner` primary, NO
  fallback (Rishi 2026-08-14 — moved off Saikat's runpod_vllm; offline jobs
  fail + retry rather than fanning out). NEVER gemini in the chain.
- `influencer_classification` stays on runpod_vllm (Saikat) for now — it
  sends avatar images and Hetzner vision isn't verified yet.
- Sync user-facing processes: gemini primary, no fallback (TTFT matters).
- Leak guard: any async process resolving to gemini fires an error log
  + Sentry capture_message (called but does not block the request).

These tests are source-level + signature-level — no live LLM calls.
"""

from __future__ import annotations

import logging

from services.llm_registry import (
    ASYNC_PROCESSES_NEVER_GEMINI,
    LLM_DEFAULTS,
    PROVIDERS,
    _check_async_gemini_leak,
)


# ─── Routing-policy contract ─────────────────────────────────────────

# The 6 text async processes moved to Hetzner. influencer_classification is
# vision-bearing and stays on Saikat's pod until Hetzner vision is verified.
HETZNER_ASYNC = {
    "proactive_generation",
    "quality_scorer",
    "memory_extraction",
    "memory_consolidation",
    "nudge_generation",
    "video_idea_generation",
}


def test_text_async_processes_default_to_hetzner_no_fallback():
    """Rishi 2026-08-14: the 6 text async background processes default to
    Hetzner's free inference API with NO fallback. Same model as Saikat's
    pod (Qwen/Qwen3.6-35B-A3B-FP8) so behaviour is host-only. No fallback
    is deliberate: these are offline jobs, so if Hetzner is down they fail
    + retry on the next sweep rather than fanning out to other providers."""
    for p in HETZNER_ASYNC:
        d = LLM_DEFAULTS[p]
        assert d["provider"] == "hetzner", (
            f"{p}: async-process code default must be hetzner; got {d['provider']!r}"
        )
        assert "fallback_provider" not in d, (
            f"{p}: no fallback by design (Rishi 2026-08-14); "
            f"got {d.get('fallback_provider')!r}"
        )


def test_influencer_classification_stays_on_runpod_vllm():
    """The one vision-bearing async process keeps routing to Saikat's pod
    (runpod_vllm, supports_vision=True) until Hetzner vision is verified —
    Hetzner's `hetzner` provider ships supports_vision=False, so the H12
    capability guard would refuse the flip anyway. No fallback (a text-only
    fallback would silently drop the avatar image)."""
    d = LLM_DEFAULTS["influencer_classification"]
    assert d["provider"] == "runpod_vllm", (
        f"influencer_classification stays on runpod_vllm (vision) until "
        f"Hetzner vision is verified; got {d['provider']!r}"
    )
    assert "fallback_provider" not in d


def test_no_async_process_chains_to_gemini():
    """Hard guard: even if a future contributor adds a fallback chain, the
    async-process chain must never end at gemini."""
    for p in ASYNC_PROCESSES_NEVER_GEMINI:
        d = LLM_DEFAULTS[p]
        assert d["provider"] != "gemini", f"{p}: primary must not be gemini"
        assert d.get("fallback_provider") != "gemini", (
            f"{p}: fallback must not be gemini"
        )


def test_sync_user_facing_processes_stay_on_gemini_with_no_fallback():
    """Sync user-waiting processes need TTFT — gemini wins. No fallback
    so we never silently degrade to a slower provider mid-session."""
    sync_user_facing = {
        "user_chat_main",
        "audio_transcription",
        "soul_file_coach",
        "character_generator",
        "ai_influencer_wizard_simulation",
        "soul_file_recommendations",
    }
    for p in sync_user_facing:
        d = LLM_DEFAULTS[p]
        assert d["provider"] == "gemini", (
            f"{p}: sync user-facing must stay on gemini; got {d['provider']!r}"
        )
        assert "fallback_provider" not in d, (
            f"{p}: sync user-facing must have NO fallback; got {d.get('fallback_provider')!r}"
        )


def test_user_chat_main_nsfw_stays_on_openrouter():
    """NSFW path is special — gemini content policy rejects, openrouter
    routes around. Don't accidentally chain a fallback that lands on
    gemini either."""
    d = LLM_DEFAULTS["user_chat_main_nsfw"]
    assert d["provider"] == "openrouter"
    assert d.get("fallback_provider") != "gemini"


def test_async_never_gemini_set_covers_known_async_processes():
    """If a new async process is added to LLM_DEFAULTS but not added to
    ASYNC_PROCESSES_NEVER_GEMINI, the leak guard won't catch it. This
    test sentinels the membership."""
    expected = {
        "proactive_generation",
        "quality_scorer",
        "memory_extraction",
        "memory_consolidation",
        "nudge_generation",
        "video_idea_generation",
        # Phase 21γ.P34.M1 — Discovery Feed bot classification.
        "influencer_classification",
    }
    assert ASYNC_PROCESSES_NEVER_GEMINI == expected, (
        "ASYNC_PROCESSES_NEVER_GEMINI drifted from the canonical set. "
        "If you're adding a new async process, add it to the set in the "
        "same PR so the leak guard covers it."
    )


# ─── Leak-guard behaviour ────────────────────────────────────────────


def test_leak_guard_fires_on_async_process_to_gemini(caplog):
    """If quality_scorer (or any async process) ever resolves to gemini
    at runtime, the leak guard must log at ERROR level. This catches
    the 2026-06-08 class of bug — DB cache fails to load, code default
    falls through to gemini, money silently bleeds."""
    caplog.set_level(logging.ERROR, logger="services.llm_registry")
    _check_async_gemini_leak("quality_scorer", "gemini")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("ASYNC PROCESS HIT GEMINI" in m for m in msgs), (
        "leak guard must fire on async process + gemini"
    )
    assert any("quality_scorer" in m for m in msgs), (
        "leak guard message must name the offending process"
    )


def test_leak_guard_silent_on_sync_user_facing_to_gemini(caplog):
    """Legit user_chat_main → gemini is the WHOLE POINT of gemini —
    do not noise-alert on it. False positives kill alerting credibility."""
    caplog.set_level(logging.ERROR, logger="services.llm_registry")
    _check_async_gemini_leak("user_chat_main", "gemini")
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("ASYNC PROCESS" in m for m in msgs), (
        "leak guard must stay silent for legit user-facing gemini calls"
    )


def test_leak_guard_silent_on_async_to_hetzner(caplog):
    """Async → hetzner is the intended state now. Silent."""
    caplog.set_level(logging.ERROR, logger="services.llm_registry")
    _check_async_gemini_leak("quality_scorer", "hetzner")
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("ASYNC PROCESS" in m for m in msgs)


def test_leak_guard_silent_on_async_to_runpod_vllm(caplog):
    """Async → runpod_vllm (influencer_classification) is intended. Silent."""
    caplog.set_level(logging.ERROR, logger="services.llm_registry")
    _check_async_gemini_leak("influencer_classification", "runpod_vllm")
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("ASYNC PROCESS" in m for m in msgs)


# ─── Provider registry sanity ─────────────────────────────────────────


def test_async_providers_registered():
    """Async routing depends on hetzner (the 6 text processes) and
    runpod_vllm (influencer_classification) being registered. If either is
    removed from PROVIDERS, routing breaks silently. Pin both."""
    for p in ("hetzner", "runpod_vllm"):
        assert p in PROVIDERS, f"provider {p!r} must be registered"
        meta = PROVIDERS[p]
        assert meta.get("supports_chat") is True
        assert meta.get("base_url"), f"{p}: must have base_url set"
