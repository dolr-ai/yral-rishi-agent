# ---------------------------------------------------------------------------
# llm_client/__init__.py — package marker for the LLM client layer.
#
# ⭐ START HERE: this package is the only place in the orchestrator that
# talks to a real LLM provider (Gemini today; OpenRouter + Anthropic land
# in Day 6+ per the agent definition + the `reference_yral_chat_v2_llm_
# routing_tara` memory). Everything outside this package depends ONLY on
# the abstract `LlmClient` interface — so a future routing matrix
# (Tara → OpenRouter; crisis → Claude;
#  influencer.is_nsfw=TRUE / NSFW → OpenRouter) can be added
# without changing any caller code.
#
# WHY THE PACKAGE EXISTS (per A10 — LLM-agnostic abstraction)
# CONSTRAINTS A10 says verbatim: "LLM-agnostic abstraction — use an
# `llm_client` interface that ALL routing paths consume." The interface
# lives in `base.py`; concrete providers live in their own files
# (`gemini.py` today; `openrouter.py` + `anthropic.py` in Day 6+).
#
# Re-exports below let callers do:
#   `from app.llm_client import LlmClient, LlmResponse, GeminiClient`
# instead of importing from the deep `base.py` + `gemini.py` paths.
# Smaller import surface = easier to swap implementations later without
# touching every callsite.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# Abstract interface + the typed return shape every provider produces.
# Imported by run_turn.py + tests; never instantiated directly (it's an
# ABC).
from app.llm_client.base import (
    LlmClient,
    LlmClientTimeoutError,
    LlmClientUpstreamError,
    LlmResponse,
)

# Concrete Gemini provider — the only one wired today. Day 6+ ships
# OpenRouter + Anthropic alongside; the routing decision happens above
# this layer in `run_turn.py`.
from app.llm_client.gemini import GeminiClient

# `get_settings()` for the default-client lifespan init below. Loaded
# lazily inside the init function so import-order issues don't bite
# (config.py imports cheaply but the settings parse is keyed on env
# vars present at first-call time).
from app.config import get_settings


# Module-level singleton — the lifespan-managed default LLM client.
# Today wired to GeminiClient per the Day-5 default-tier path. Day 6+
# routing matrix may keep one default-client OR maintain a small
# `{provider_name: LlmClient}` dict here; today one client is enough.
_default_client: "LlmClient | None" = None


def init_default_llm_client() -> None:
    """Build the module-level default LLM client at lifespan startup.

    WHAT: instantiates the Gemini provider with the API key from
          settings + stores it in `_default_client`.
    WHEN: called from `app/main.py`'s lifespan startup hook AFTER
          init_redis + init_soul_file_client. Idempotent — no-op if
          already set (helpful for tests that inject a fake via
          monkeypatch).
    WHY:  one-shot startup avoids the per-request init cost + keeps
          the SDK's auth registration call out of the hot path. The
          `GeminiClient.__init__` is cheap (no network) but the
          process-wide `gemini_sdk.configure(...)` is the kind of
          side-effect we'd rather run once on a known thread than at
          first-handler-invocation time.

    Raises:
      RuntimeError when `enable_run_turn_real_llm=True` AND
      `gemini_api_key=""` — fail-closed on a half-configured env
      that would otherwise crash on first LLM call. Local dev
      without the real-LLM flag keeps the client at None
      (the handler routes to the stub path instead).
    """
    global _default_client

    if _default_client is not None:
        return

    settings = get_settings()
    if not settings.enable_run_turn_real_llm:
        return

    if not settings.gemini_api_key:
        raise RuntimeError(
            "enable_run_turn_real_llm=True but gemini_api_key is empty. "
            "Set GEMINI_API_KEY in .env.local (per D8 + secrets.yaml) "
            "or flip enable_run_turn_real_llm=False to use the stub path."
        )

    _default_client = GeminiClient(
        api_key=settings.gemini_api_key,
        model_id=settings.gemini_model_id,
        call_timeout_seconds=settings.gemini_call_timeout_seconds,
    )


def close_default_llm_client() -> None:
    """Tear down the module-level default LLM client at lifespan shutdown.

    WHAT: clears `_default_client = None`. The SDK manages its own
          HTTP pool internally; no explicit aclose to call.
    WHEN: called from `app/main.py`'s lifespan shutdown hook.
    WHY:  symmetric init/close + GC the client reference so a process
          restart starts from a clean slate.
    """
    global _default_client
    _default_client = None


def get_default_llm_client() -> "LlmClient":
    """Return the initialised default LLM client singleton.

    WHAT: hands out the module-level `_default_client`. Raises if init
          hasn't run (i.e. `enable_run_turn_real_llm=False`).
    WHEN: called from `run_turn.py` per chat turn when the real-LLM
          path is enabled.
    WHY:  central accessor + clear failure mode when a caller hits
          the real-LLM path with the flag off (operator-side mis-
          configuration surface).
    """
    if _default_client is None:
        raise RuntimeError(
            "default LLM client is not initialised — set "
            "enable_run_turn_real_llm=True + GEMINI_API_KEY then restart "
            "(see init_default_llm_client docstring)."
        )
    return _default_client


__all__ = [
    "GeminiClient",
    "LlmClient",
    "LlmClientTimeoutError",
    "LlmClientUpstreamError",
    "LlmResponse",
    "close_default_llm_client",
    "get_default_llm_client",
    "init_default_llm_client",
]


# ===========================================================================
# RELATED FILES:
#   base.py        — abstract LlmClient interface + LlmResponse dataclass
#                    + the two typed exceptions run_turn maps to envelopes
#   gemini.py      — concrete Gemini provider (today's only one)
#   ../run_turn.py — consumer; instantiates GeminiClient + calls generate()
#   ../../tests/test_llm_client_gemini.py
#                  — mocked-API unit tests + env-gated integration test
#   ../../../yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                  — A10 (LLM-agnostic abstraction) + D4 (Langfuse traces)
#                  + D8 (GEMINI_API_KEY as secret)
# ===========================================================================
