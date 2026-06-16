"""Routing policy tests — 2026-06-08 cost-leak fix.

Pin the contract Rishi asked for:
- Async background processes: runpod_vllm primary + internal_vllm fallback,
  NEVER gemini in the chain.
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


def test_async_processes_primary_is_runpod_vllm():
    """All async background processes must default to Saikat's pod
    (runpod_vllm) at the code-default layer. Even if DB overrides
    fail to load (the 2026-06-08 bug), the code default must NOT be
    gemini — otherwise the cost-leak that motivated this PR reappears."""
    for p in ASYNC_PROCESSES_NEVER_GEMINI:
        d = LLM_DEFAULTS[p]
        assert d["provider"] == "runpod_vllm", (
            f"{p}: async-process code default must be runpod_vllm; got {d['provider']!r}"
        )


def test_async_processes_have_internal_vllm_fallback():
    """Anshuman's pod (internal_vllm) is the documented fallback per
    Rishi 2026-06-08. Never gemini in the chain.

    Exception: `influencer_classification` (Phase 21γ.P34.M1) is
    vision-bearing — `internal_vllm` is text-only, so an automatic
    fallback would silently drop the avatar image and produce
    garbage labels. Mirrors the rationale for
    `user_chat_main_multimodal` having no fallback."""
    no_fallback_by_design = {"influencer_classification"}
    for p in ASYNC_PROCESSES_NEVER_GEMINI:
        d = LLM_DEFAULTS[p]
        if p in no_fallback_by_design:
            assert "fallback_provider" not in d, (
                f"{p}: documented as no-fallback (vision-bearing); "
                f"got {d.get('fallback_provider')!r}"
            )
            continue
        assert d.get("fallback_provider") == "internal_vllm", (
            f"{p}: fallback_provider must be internal_vllm; "
            f"got {d.get('fallback_provider')!r}"
        )
        assert d.get("fallback_model"), (
            f"{p}: fallback_model required when fallback_provider is set"
        )


def test_no_async_process_chains_to_gemini():
    """Hard guard: even if a future contributor adds a 3-link chain, the
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
        # Vision-bearing; no fallback (see test_async_processes_have_internal_vllm_fallback
        # exception list).
        "influencer_classification",
    }
    assert ASYNC_PROCESSES_NEVER_GEMINI == expected, (
        "ASYNC_PROCESSES_NEVER_GEMINI drifted from the canonical set. "
        "If you're adding a new async process, add it to the set + add a "
        "fallback chain in LLM_DEFAULTS in the same PR."
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


def test_leak_guard_silent_on_async_to_runpod_vllm(caplog):
    """Async → runpod_vllm is the intended state. Silent."""
    caplog.set_level(logging.ERROR, logger="services.llm_registry")
    _check_async_gemini_leak("quality_scorer", "runpod_vllm")
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("ASYNC PROCESS" in m for m in msgs)


def test_leak_guard_silent_on_async_to_internal_vllm(caplog):
    """Async → internal_vllm is the fallback state. Also intended; silent."""
    caplog.set_level(logging.ERROR, logger="services.llm_registry")
    _check_async_gemini_leak("quality_scorer", "internal_vllm")
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("ASYNC PROCESS" in m for m in msgs)


# ─── Provider registry sanity ─────────────────────────────────────────


def test_both_vllm_providers_registered():
    """Routing depends on both runpod_vllm (Saikat) and internal_vllm
    (Anshuman) being registered as providers. If either is removed
    from PROVIDERS, the fallback chain breaks silently. Pin both."""
    for p in ("runpod_vllm", "internal_vllm"):
        assert p in PROVIDERS, f"provider {p!r} must be registered"
        meta = PROVIDERS[p]
        assert meta.get("supports_chat") is True
        assert meta.get("base_url"), f"{p}: must have base_url set"
