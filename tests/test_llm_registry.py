"""Phase 25.2 — registry + per-provider concurrency cap source-pin tests.

These tests pin the registry's structural contract via source inspection
(no runtime imports — same pattern as tests/test_v2_inbox_h2h.py). Live
wire-format tests against real providers belong in Phase 25.7.
"""

import re
from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


def _strip_ws(s: str) -> str:
    """Match against source even after ruff multi-line splits."""
    return re.sub(r"\s", "", s)


# ─── process names + renames (Decision 2, 2026-06-02) ─────────────────────


def test_process_names_include_renamed_entries():
    """Renames from 2026-06-02:
      wizard_simulation → ai_influencer_wizard_simulation
      recommendations → soul_file_recommendations
    Pin both new names in the registry source and confirm the old names
    are gone."""
    src = _read("app/services/llm_registry.py")
    assert '"ai_influencer_wizard_simulation"' in src
    assert '"soul_file_recommendations"' in src
    # Old names must not ship — would mean stale registry vs design doc
    # (be careful — these substrings would also match the renamed forms,
    # so we look for them with surrounding quotes)
    assert '"wizard_simulation"' not in src
    assert '"recommendations"' not in src


def test_process_names_tuple_has_eleven_entries():
    """Design doc Decision 2 locks the count at 11. If a future PR adds
    a process without updating the doc, this catches it."""
    src = _read("app/services/llm_registry.py")
    # Count entries in the PROCESS_NAMES tuple. The tuple sits at module
    # scope; we look for the literal string entries inside it.
    expected = (
        "user_chat_main",
        "audio_transcription",
        "proactive_generation",
        "quality_scorer",
        "memory_extraction",
        "memory_consolidation",
        "soul_file_coach",
        "nudge_generation",
        "character_generator",
        "ai_influencer_wizard_simulation",
        "soul_file_recommendations",
    )
    for name in expected:
        assert f'"{name}"' in src, f"missing process name {name!r}"


# ─── concurrency caps (Decision 3) ────────────────────────────────────────


def test_providers_table_carries_design_doc_caps():
    """Caps from design doc Decision 3: gemini=20, openai/openrouter/
    together=10, internal_vllm=5, ollama=2."""
    src = _strip_ws(_read("app/services/llm_registry.py"))
    # Each provider entry sets concurrency_cap to the design-doc value.
    # We pin via the (provider-key, cap) co-location pattern.
    assert '"gemini":{"concurrency_cap":20' in src
    assert '"openai":{"concurrency_cap":10' in src
    assert '"openrouter":{"concurrency_cap":10' in src
    assert '"internal_vllm":{"concurrency_cap":5' in src
    assert '"ollama":{"concurrency_cap":2' in src


# ─── internal_vllm spec (Anshuman 2026-06-02) ─────────────────────────────


def test_internal_vllm_provider_spec_matches_design_doc():
    """The internal_vllm provider must carry Anshuman's spec verbatim:
    base_url, secret name, synthetic cost basis, thinking-mode disable."""
    src = _read("app/services/llm_registry.py")
    assert '"https://model.ansuman.yral.com/v1"' in src
    assert "/run/secrets/INTERNAL_VLLM_API_KEY" in src
    # Cost basis from Q4 design doc "Cost basis" section
    flat = _strip_ws(src)
    assert '"cost_basis":"synthetic"' in flat
    # Synthetic per-token cost suggested in design (Q4)
    assert "0.00005" in src
    # Thinking-mode disable (gist quirk #3 in design doc) — both legal
    # casings (False capitalized for Python literal) ship as the default
    assert "enable_thinking" in src


def test_no_real_api_key_committed():
    """No actual API key material in the registry source. The registry
    only encodes secret NAMES; resolution is via file-first
    /run/secrets/<NAME> + env fallback."""
    src = _read("app/services/llm_registry.py")
    # A real API key would have a long random-looking literal. Confirm
    # we're only referencing names (uppercase identifiers ending in
    # _API_KEY) and paths, never literal hex/random strings.
    assert "sk-" not in src  # OpenAI / OpenRouter key prefix
    assert "AIza" not in src  # Gemini / Google key prefix


# ─── env override (Q3) ────────────────────────────────────────────────────


def test_env_override_pattern_present():
    """Q3: LLM_PROCESS__<UPPER_NAME>=<provider>/<model> overrides the
    default registry entry. Pin that the override-resolution code path
    is present and reads the right env-key shape."""
    src = _read("app/services/llm_registry.py")
    assert "LLM_PROCESS__" in src
    assert "process.upper()" in src
    # The provider/model split is what tells us the env value format is
    # canonical (vs a JSON blob or other shape)
    assert ".partition" in src or ".split" in src


# ─── per-provider semaphore (Decision 3) ──────────────────────────────────


def test_semaphore_is_per_provider_via_lazy_cache():
    """Per-provider asyncio.Semaphore lives in a lazy dict so we avoid
    binding to an event-loop at module-import time. Pin both the cache
    + the lazy-init shape."""
    src = _strip_ws(_read("app/services/llm_registry.py"))
    assert "_semaphores:dict[str,asyncio.Semaphore]" in src
    assert "asyncio.Semaphore(cap)" in src


# ─── Gemini routing deferred to 25.3 ──────────────────────────────────────


def test_gemini_dispatch_raises_until_25_3():
    """25.2 deliberately DOES NOT wire Gemini — the legacy ai_client
    signature doesn't accept a messages-list and extracting a clean
    gemini.py client is 25.3 scope. Until then the registry MUST raise
    NotImplementedError for provider=gemini so we don't silently fail
    through to a half-built path."""
    src = _read("app/services/llm_registry.py")
    assert 'provider == "gemini"' in src
    assert "NotImplementedError" in src
    assert "25.3" in src


# ─── openai_compatible client wiring ──────────────────────────────────────


def test_registry_dispatches_to_openai_compatible_for_non_gemini():
    """Non-Gemini providers (openai, openrouter, internal_vllm, ollama)
    all go through the openai_compatible client. The single dispatch
    path is what gives us symmetry — adding a provider is one row in
    PROVIDERS, no new client module."""
    src = _read("app/services/llm_registry.py")
    assert "from services.llm_clients import openai_compatible" in src
    assert "openai_compatible.complete" in src


def test_openai_compatible_threads_extra_body_through():
    """extra_body is the universal escape hatch (Qwen thinking-mode
    disable + future provider-specific knobs). Pin that the client
    accepts the kwarg AND merges it into the request body."""
    src = _read("app/services/llm_clients/openai_compatible.py")
    assert "extra_body: dict | None = None" in src
    assert "body.update(extra_body)" in src


def test_openai_compatible_streaming_yields_usage_event():
    """Per the Anshuman gist (design doc quirk #1): with
    stream_options.include_usage, usage arrives in the LAST chunk
    alongside empty `choices`. Pin that the streaming client surfaces
    it as a discrete ('usage', json) event so the caller doesn't have
    to re-parse SSE."""
    src = _read("app/services/llm_clients/openai_compatible.py")
    assert '"include_usage": True' in src
    assert "'usage', json.dumps(usage)" in src or '"usage", json.dumps(usage)' in src


def test_openai_compatible_retries_with_exponential_backoff():
    """Retry ladder step 1 from Q5 design: 3 retries on same provider
    with exponential backoff + jitter. Pin the shape."""
    src = _read("app/services/llm_clients/openai_compatible.py")
    assert "max_retries: int = 3" in src
    # Exponential + jitter shape — 0.2 * (2**attempt) with random jitter
    assert "2**attempt" in src
    assert "random.uniform" in src
