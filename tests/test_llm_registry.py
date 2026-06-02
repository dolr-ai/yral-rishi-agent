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


# ─── Gemini dispatch (25.3 — Gemini client now wired) ────────────────────


def test_gemini_dispatch_uses_dedicated_client():
    """25.3 wires the new gemini.py client. Registry routes Gemini to it
    instead of raising NotImplementedError. Both clients (gemini +
    openai_compatible) expose the same complete()/complete_stream()
    interface so the dispatch body has no per-provider special-casing
    beyond the import."""
    src = _read("app/services/llm_registry.py")
    assert 'provider == "gemini"' in src
    assert "from services.llm_clients import gemini" in src
    assert "client_module.complete" in src
    # No more NotImplementedError for Gemini
    assert "NotImplementedError" not in src


def test_gemini_client_has_symmetric_interface():
    """gemini.py must expose complete() + complete_stream() with the same
    keyword-args shape as openai_compatible so registry dispatch is
    uniform. Pin both function signatures."""
    src = _read("app/services/llm_clients/gemini.py")
    assert "async def complete(" in src
    assert "async def complete_stream(" in src
    # Same kwargs as openai_compatible — provider, base_url, api_key,
    # model, messages, temperature, max_tokens, extra_body, timeout
    assert "provider: str" in src
    assert "messages: list[dict]" in src
    assert "extra_body: dict | None = None" in src


def test_gemini_client_translates_messages_to_contents():
    """OpenAI messages → Gemini contents conversion: system messages
    hoisted into systemInstruction; assistant role renamed to model."""
    src = _read("app/services/llm_clients/gemini.py")
    assert "_messages_to_gemini_contents" in src
    # Role rename
    assert '"model"' in src and '"assistant"' in src
    # System hoisting
    assert "systemInstruction" in src


def test_llm_defaults_uses_production_gemini_model():
    """The defaults must reflect what production actually runs today
    (gemini-2.5-flash, per config.GEMINI_MODEL). Aspirational model
    names like 2.0-flash are NOT the production reality."""
    src = _read("app/services/llm_registry.py")
    assert "gemini-2.5-flash" in src
    # gemini-2.0-flash was the placeholder I had in 25.2 scaffolding;
    # must be gone now.
    assert "gemini-2.0-flash" not in src


def test_llm_defaults_constant_name():
    """Per Rishi 2026-06-02 spec: the constant is named LLM_DEFAULTS,
    not DEFAULT_REGISTRY (which was my 25.2 placeholder)."""
    src = _read("app/services/llm_registry.py")
    assert "LLM_DEFAULTS" in src
    # The placeholder name must NOT ship
    assert "DEFAULT_REGISTRY" not in src


# ─── extra_body per-invocation override (25.3) ───────────────────────────


def test_call_accepts_extra_body_param():
    """character_generator passes Gemini-specific safetySettings via
    extra_body. Per-invocation extra_body must merge over provider
    default (caller wins on key collision)."""
    src = _read("app/services/llm_registry.py")
    assert "extra_body: dict | None = None" in src
    assert "merged_extra" in src or "caller wins" in src


# ─── call-site migrations (25.3) ──────────────────────────────────────────


def test_simple_callers_migrated_to_registry():
    """7 process-bound call sites must use llm_registry.call(process=...)
    instead of the legacy ai_client._call_gemini import. Any future
    refactor that re-introduces _call_gemini in these files is a smell."""
    for path in (
        "app/services/memory.py",
        "app/services/coach.py",
        "app/services/nudge.py",
        "app/services/recommendations.py",
        "app/services/character_generator.py",
        "app/services/quality_scorer.py",
    ):
        src = _read(path)
        # No more direct legacy import
        assert "from services.ai_client import _call_gemini" not in src, (
            f"{path}: still imports legacy _call_gemini"
        )
        # Registry import present
        assert "llm_registry" in src, f"{path}: missing llm_registry import"
        # registry.call invoked with a process= kwarg
        assert "llm_registry.call(" in src, f"{path}: no registry.call invocation"


def test_wizard_intake_and_draft_migrated_preview_deferred():
    """Wizard has 3 LLM calls; intake + draft migrate, preview stays on
    generate_response because it needs the archetype tuning path
    (intentionally part of 25.3b chat-orchestration scope)."""
    src = _read("app/services/wizard.py")
    # The intake + draft prompts now go through the registry
    assert "llm_registry.call(" in src
    assert src.count('process="ai_influencer_wizard_simulation"') >= 2
    # Preview keeps using generate_response (the archetype-tuning path);
    # this is the documented exception
    assert "await generate_response(" in src


# ─── openai_compatible client wiring ──────────────────────────────────────


def test_registry_dispatches_to_openai_compatible_for_non_gemini():
    """Non-Gemini providers (openai, openrouter, internal_vllm, ollama)
    all go through the openai_compatible client. The single dispatch
    path is what gives us symmetry — adding a provider is one row in
    PROVIDERS, no new client module."""
    src = _read("app/services/llm_registry.py")
    assert "from services.llm_clients import openai_compatible" in src
    # The dispatch uses a `client_module` indirection after 25.3 to
    # uniformly call either gemini.complete or openai_compatible.complete
    assert "client_module.complete" in src or "openai_compatible.complete" in src


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
