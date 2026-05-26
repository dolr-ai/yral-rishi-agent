# ---------------------------------------------------------------------------
# gemini.py — Google Gemini provider for the LlmClient abstraction.
#
# ⭐ START HERE: `GeminiClient` is a concrete `LlmClient` implementation
# that talks to Google's Gemini API via the `google-generativeai`
# SDK. Today's Day-5 default-tier model is `gemini-2.5-flash` (fast +
# cheap; the agent definition's "default → Gemini Flash" path).
#
# WHAT THE CLIENT DOES
#   1. Configures the SDK once (per-process API key registration).
#   2. On `.generate(...)`, builds the request with `system_instruction =
#      prompt` and one user turn (= `user_message`).
#   3. Wraps the call in `asyncio.wait_for(..., timeout=30s)` so a hung
#      upstream can't blow E1's latency budget.
#   4. Times the call client-side + emits a Langfuse generation span
#      per D4 (provider, model, prompt_tokens, completion_tokens,
#      latency_milliseconds, temperature attributes).
#   5. Returns a typed LlmResponse on success.
#   6. Maps `asyncio.TimeoutError` → `LlmClientTimeoutError` (504 in
#      `run_turn.py`) and SDK / API errors → `LlmClientUpstreamError`
#      (502 in `run_turn.py`).
#
# WHY THE google-generativeai SDK (NOT raw httpx)
# Per A2.1 (minimum-viable): the official SDK handles auth + retries +
# error parsing. Using httpx directly would duplicate that logic for
# zero gain. The SDK's `GenerativeModel.generate_content_async` is
# what we'd call from raw httpx anyway, just less robustly.
#
# WHY 30s TIMEOUT (NOT longer)
# Per the Day-5 directive verbatim: "tight timeout: 30s total. Bail
# with envelope-shaped 504 on timeout (mirror what Day-4C public-api
# does for orchestrator timeouts)." E1 demands v2 ≥50% faster than
# chat-ai; chat-ai's 95th percentile is well under 30s on Gemini-Flash
# turns. A 30s ceiling catches the pathological hangs without false-
# tripping the happy path.
#
# WHY LANGFUSE TRACING INSIDE THE PROVIDER (NOT IN run_turn.py)
# Per D4 + the directive's piece-2 attribute list: every LLM call gets
# its own generation span. Provider-side instrumentation means every
# LLM provider (today + Day 6+) gets the same trace shape without
# `run_turn.py` having to know which provider it's wrapping. A future
# routing matrix that calls multiple providers per turn would get N
# spans naturally.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib `asyncio.TimeoutError` + `wait_for` enforce the 30s budget +
# emit the typed exception `run_turn.py` catches.
import asyncio

# stdlib `time.perf_counter` measures wall-clock latency for the
# Langfuse trace attribute + the LlmResponse field. `perf_counter` is
# the monotonic clock — immune to wall-clock jumps that would corrupt
# duration math.
import time

# stdlib logger — emits structured fields the H6 PII-allowlist redactor
# in `app/logging.py` knows about (provider / model / token counts /
# latency_milliseconds / temperature). NEVER logs prompt text or user
# message content.
import logging

# `Final` declares module-level constants the type-checker treats as
# immutable. Used for the model id + timeout value so a future tuning
# bump is grep-discoverable.
from typing import Final

# `google-generativeai` is the official Google SDK for Gemini. Added
# to pyproject.toml as a runtime dep this PR.
import google.generativeai as gemini_sdk

# Internal — the abstract interface + typed response + the two
# exception shapes `run_turn.py` catches.
from app.llm_client.base import (
    LlmClient,
    LlmClientTimeoutError,
    LlmClientUpstreamError,
    LlmResponse,
)

# `get_langfuse()` returns the singleton client (or None when tracing
# is disabled locally per D4's feature-flag). The provider emits a
# trace generation span on every call when the client is present;
# no-ops cleanly when it's None.
from app.langfuse_middleware import get_langfuse


# Provider tag stamped on the LlmResponse + Langfuse trace attribute.
# `gemini` (lowercase, no underscores) matches the agent-definition
# vocabulary + the routing-matrix memo. Stays a constant because it's
# the wire identifier of THIS provider; a different provider's file
# has its own _PROVIDER_TAG (e.g. `openrouter` in Day 6+'s file).
_PROVIDER_TAG: Final[str] = "gemini"

# Model id + timeout-seconds are NOT constants anymore — per C7 ("model
# names, timeouts, thresholds, all configurable"). Settings carries
# the env-overridable defaults; constructor below accepts them as
# explicit kwargs so tests + Day 6+ routing matrix can vary them per
# instance without touching this module.


_log = logging.getLogger("app.llm_client.gemini")


class GeminiClient(LlmClient):
    """Gemini provider implementing the LlmClient abstraction.

    WHAT: constructs a `google.generativeai` SDK client at init time +
          calls `generate_content_async` on each `.generate(...)`
          invocation. Wraps the call in a 30s timeout + emits a
          Langfuse generation span per D4.
    WHEN: instantiated once at module-import or first-use in
          `app/run_turn.py`; same instance serves every concurrent
          request (the SDK is async-safe).
    WHY:  Day-5 default path. Day 6+ routing matrix adds OpenRouter +
          Anthropic alongside; this client stays unchanged.

    Lifecycle:
      __init__       — registers the API key + builds the SDK model
                       wrapper. Cheap (no network).
      generate       — async call + trace + return LlmResponse.

    No close()/aclose() — the SDK manages its own internal HTTP
    pool; per A2.1 we don't wrap that in a lifecycle helper unless
    a leak is observed.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str,
        call_timeout_seconds: float,
    ) -> None:
        """Wire the SDK with the API key + build the model wrapper.

        WHAT: calls `gemini_sdk.configure(api_key=...)` (process-wide
              auth registration) + builds a `GenerativeModel(model_id)`
              instance the `.generate(...)` method dispatches against.
              Stashes the per-call timeout for `.generate(...)` to read.
        WHEN: called once at lifespan startup OR first-use lazy init
              by run_turn.py.
        WHY:  process-wide configure is the SDK's documented init
              pattern. Per C7 ("model names, timeouts, thresholds, all
              configurable") the model id + timeout are explicit
              constructor args; Settings is the lifespan-init source
              + tests can pass distinct values per instance.

        Args:
          api_key              — Gemini API key (per D8, secrets.yaml).
          model_id             — provider model id (e.g.
                                 `gemini-2.5-flash`). Settings.gemini_model_id
                                 is the lifespan source.
          call_timeout_seconds — provider-side per-call timeout.
                                 Settings.gemini_call_timeout_seconds
                                 is the lifespan source.
        """
        if not api_key:
            raise ValueError(
                "GeminiClient requires a non-empty api_key. Source it from "
                "settings.gemini_api_key (declared in secrets.yaml per D8)."
            )

        # `configure` is process-global. Idempotent if called twice
        # with the same key; the SDK is documented to tolerate it.
        gemini_sdk.configure(api_key=api_key)

        # Build the model wrapper. `system_instruction` is passed
        # per-call inside generate(...) so a single client instance
        # can serve different soul-file prompts across turns + users.
        self._model = gemini_sdk.GenerativeModel(model_name=model_id)
        self._model_id: Final[str] = model_id
        self._call_timeout_seconds: Final[float] = call_timeout_seconds

        _log.info(
            "gemini_client_initialised",
            extra={
                "model": model_id,
                "provider": _PROVIDER_TAG,
                "call_timeout_seconds": call_timeout_seconds,
            },
        )

    async def generate(
        self,
        *,
        prompt: str,
        user_message: str,
        temperature: float,
        max_tokens: int,
    ) -> LlmResponse:
        """Call Gemini + return the typed response.

        WHAT: builds the SDK request (system_instruction = `prompt`;
              one user turn = `user_message`), wraps the
              `generate_content_async` call in 30s `asyncio.wait_for`,
              measures latency client-side, emits a Langfuse span,
              returns LlmResponse.
        WHEN: called once per chat turn from `run_turn.py` AFTER the
              soul-file lookup populates `prompt`.
        WHY:  one network round-trip per chat turn. Day 6+ routing
              swaps the concrete client; the calling code stays.

        See LlmClient.generate() docstring for the full contract.
        """
        # Build the generation config from the per-call sampling args.
        # SDK clamps temperature internally; max_tokens caps the
        # output side. Both pass through as documented.
        generation_config = gemini_sdk.types.GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        # Per-call kwargs assembly. `system_instruction` is the soul-
        # file layered prompt; `contents` is one user turn. We do NOT
        # carry prior turns today — user-memory integration (Day 8+)
        # is the place that adds them.
        call_arguments = {
            "contents": [{"role": "user", "parts": [user_message]}],
            "generation_config": generation_config,
        }

        # The model wrapper accepts `system_instruction` via a separate
        # constructor arg in newer SDK versions; for portability we
        # pass it on the per-call path via the safer GenerativeModel
        # `system_instruction` constructor in a fresh wrapper. This
        # avoids the SDK-version sensitivity around per-call system
        # instruction handling without leaking SDK plumbing into
        # callers.
        per_call_model = gemini_sdk.GenerativeModel(
            model_name=self._model_id,
            system_instruction=prompt,
        )

        # Client-side latency timer. perf_counter is monotonic; safe
        # for duration math under wall-clock skew.
        started_at = time.perf_counter()

        try:
            sdk_response = await asyncio.wait_for(
                per_call_model.generate_content_async(**call_arguments),
                timeout=self._call_timeout_seconds,
            )
        except asyncio.TimeoutError as timeout_error:
            # Client-side timeout exceeded. Map to the typed exception
            # `run_turn.py` catches + 504-envelopes.
            latency_milliseconds = int(
                (time.perf_counter() - started_at) * 1000
            )
            _log.warning(
                "gemini_call_timed_out",
                extra={
                    "provider": _PROVIDER_TAG,
                    "model": self._model_id,
                    "timeout_seconds": self._call_timeout_seconds,
                    "latency_milliseconds": latency_milliseconds,
                },
            )
            self._record_failure_span(
                temperature=temperature,
                max_tokens=max_tokens,
                latency_milliseconds=latency_milliseconds,
                failure_kind="timeout",
            )
            raise LlmClientTimeoutError(
                f"Gemini call exceeded {self._call_timeout_seconds:.0f}s budget"
            ) from timeout_error
        except Exception as upstream_error:
            # Any other SDK / API failure becomes LlmClientUpstreamError.
            # The SDK raises a variety of typed errors (quota, auth,
            # invalid args); we don't pattern-match here per A2.1 —
            # one exception class covers the whole "they're broken"
            # surface for the caller. Operator-side detail is in the
            # log + Langfuse span + Sentry trace.
            latency_milliseconds = int(
                (time.perf_counter() - started_at) * 1000
            )
            _log.error(
                "gemini_call_failed",
                extra={
                    "provider": _PROVIDER_TAG,
                    "model": self._model_id,
                    "latency_milliseconds": latency_milliseconds,
                    "error_type": type(upstream_error).__name__,
                },
            )
            self._record_failure_span(
                temperature=temperature,
                max_tokens=max_tokens,
                latency_milliseconds=latency_milliseconds,
                failure_kind="upstream_error",
            )
            raise LlmClientUpstreamError(
                f"Gemini call failed: {type(upstream_error).__name__}"
            ) from upstream_error

        latency_milliseconds = int(
            (time.perf_counter() - started_at) * 1000
        )

        # Parse the SDK response. `.text` is the assistant reply;
        # `.usage_metadata` carries the token counts when the provider
        # populates them (Gemini does; older models or streaming-only
        # paths may not).
        #
        # Codex PR-#109 round-2 CONCERN — wrap parsing in its own
        # try/except → LlmClientUpstreamError. Gemini can raise on
        # `.text` access for blocked or empty candidate responses
        # (safety-filter blocks, quota-cap'd no-output, etc.). Without
        # this guard those land as a generic 500 in the orchestrator
        # response path; with it, run_turn.py maps them to a 502
        # `llm_upstream_error` envelope (same shape as quota / auth /
        # 5xx failures upstream).
        try:
            content = sdk_response.text or ""
            usage = getattr(sdk_response, "usage_metadata", None)
            prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
            completion_tokens = int(
                getattr(usage, "candidates_token_count", 0) or 0
            )
        except Exception as parse_error:
            _log.error(
                "gemini_response_parse_failed",
                extra={
                    "provider": _PROVIDER_TAG,
                    "model": self._model_id,
                    "latency_milliseconds": latency_milliseconds,
                    "error_type": type(parse_error).__name__,
                },
            )
            self._record_failure_span(
                temperature=temperature,
                max_tokens=max_tokens,
                latency_milliseconds=latency_milliseconds,
                failure_kind="response_parse_error",
            )
            raise LlmClientUpstreamError(
                f"Gemini response parse failed: {type(parse_error).__name__} "
                "(common cause: safety-blocked candidate response)"
            ) from parse_error

        # Emit the Langfuse generation span per D4 + the directive's
        # piece-2 attribute list. No-ops when langfuse_client is None
        # (laptop dev with LANGFUSE_TRACING_ENABLED=false).
        self._record_success_span(
            temperature=temperature,
            max_tokens=max_tokens,
            latency_milliseconds=latency_milliseconds,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        _log.info(
            "gemini_call_succeeded",
            extra={
                "provider": _PROVIDER_TAG,
                "model": self._model_id,
                "latency_milliseconds": latency_milliseconds,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        )

        return LlmResponse(
            content=content,
            provider=_PROVIDER_TAG,
            model=self._model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_milliseconds=latency_milliseconds,
        )

    def _record_success_span(
        self,
        *,
        temperature: float,
        max_tokens: int,
        latency_milliseconds: int,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """Emit a Langfuse generation span for a successful call.

        WHAT: builds a trace + generation span via the Langfuse client
              singleton; attributes match the Day-5 directive's
              piece-2 list verbatim.
        WHEN: called from `generate(...)` after a successful SDK
              response is parsed.
        WHY:  D4 — every LLM call traces. Span name is the directive-
              specified `llm.gemini.generate` so the Langfuse UI
              groups Gemini calls together.
        """
        langfuse_client = get_langfuse()
        if langfuse_client is None:
            return

        # `Langfuse.trace(...)` followed by `.generation(...)` is the
        # documented Langfuse SDK pattern for an LLM call observation.
        # Trace name groups generations at the request level; the
        # generation span carries the provider-specific attributes.
        trace = langfuse_client.trace(name="llm.gemini.generate")
        trace.generation(
            name="llm.gemini.generate",
            model=self._model_id,
            metadata={
                "provider": _PROVIDER_TAG,
                "model": self._model_id,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "latency_milliseconds": latency_milliseconds,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )

    def _record_failure_span(
        self,
        *,
        temperature: float,
        max_tokens: int,
        latency_milliseconds: int,
        failure_kind: str,
    ) -> None:
        """Emit a Langfuse generation span for a failed call.

        WHAT: same trace + generation pattern as the success path,
              with `failure_kind` (timeout / upstream_error) added to
              the metadata so dashboard filters can split the two.
        WHEN: called from `generate(...)` in the timeout + upstream-
              error except branches BEFORE re-raising.
        WHY:  D4 — failures need traces too. A 504-rate / 502-rate
              dashboard is the operator-side signal that the upstream
              is degraded; without failure spans the rate would be
              invisible until users complain.
        """
        langfuse_client = get_langfuse()
        if langfuse_client is None:
            return

        trace = langfuse_client.trace(name="llm.gemini.generate")
        trace.generation(
            name="llm.gemini.generate",
            model=self._model_id,
            metadata={
                "provider": _PROVIDER_TAG,
                "model": self._model_id,
                "latency_milliseconds": latency_milliseconds,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "failure_kind": failure_kind,
            },
        )


# ===========================================================================
# RELATED FILES:
#   base.py                       — abstract LlmClient + LlmResponse +
#                                    the two typed exception shapes
#   __init__.py                   — re-exports GeminiClient
#   ../run_turn.py                — consumer; catches the two exceptions
#                                    + maps them to 504 / 502 envelopes
#   ../langfuse_middleware.py     — get_langfuse() singleton this file
#                                    emits spans against (D4)
#   ../config.py                  — Settings.gemini_api_key (declared
#                                    in secrets.yaml per D8)
#   ../../secrets.yaml            — GEMINI_API_KEY declaration
#   ../../pyproject.toml          — google-generativeai runtime dep
#   ../../tests/test_llm_client_gemini.py
#                                 — mock + env-gated integration tests
# ===========================================================================
