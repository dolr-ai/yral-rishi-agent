# ---------------------------------------------------------------------------
# base.py — abstract LlmClient interface + the typed return + exception shapes.
#
# ⭐ START HERE: this file defines:
#   1. `LlmResponse` — frozen dataclass every provider returns. Six
#      fields: content, provider, model, prompt_tokens, completion_tokens,
#      latency_milliseconds.
#   2. `LlmClient` — abstract base class. ONE method: `async generate(...)`
#      taking prompt + user_message + temperature + max_tokens.
#   3. `LlmClientTimeoutError` — provider call exceeded its tight 30s
#      budget; `run_turn` maps this to a 504 envelope.
#   4. `LlmClientUpstreamError` — provider returned an HTTP error /
#      surfaced an API-side failure. `run_turn` maps this to a 502
#      envelope so the caller can distinguish "upstream broke" from
#      "we crashed".
#
# WHY ABSTRACT — per A10 ("LLM-agnostic abstraction")
# Every routing path (Tara → OpenRouter, crisis → Claude, NSFW →
# OpenRouter, default → Gemini) calls into the SAME interface. The
# decision of WHICH provider to instantiate lives in `run_turn.py` (or
# Day 6+'s routing matrix); the calling code doesn't change when we
# add a new provider — it just sees `LlmClient.generate(...)`.
#
# WHY THE TWO TYPED EXCEPTIONS — per the Day-5 directive's error-shape
# guidance ("tight timeout: 30s total. Bail with envelope-shaped 504 on
# timeout") + the public-api Day-4C pattern that maps orchestrator
# timeouts to 504 + upstream API errors to 502. Centralising the
# exception types here means a future provider adding a new failure
# mode raises one of the two (or extends the hierarchy in this file
# only — never in callers).
#
# WHY ALL KEYWORD-ONLY ARGS on `generate(...)`
# The four-arg signature (prompt / user_message / temperature /
# max_tokens) is a B1 reading-as-English shape — every callsite
# self-documents which value is which. Positional ordering becomes a
# foot-gun once we have temperature + max_tokens (both numeric +
# easy to swap accidentally). Keyword-only forces clarity at the
# callsite + the WHAT/WHEN/WHY block in `generate(...)` documents the
# contract.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# `abc.ABC` + `abstractmethod` declare the interface contract. Without
# them, a Python class with a stub method runs fine if instantiated —
# we want the runtime error if someone tries to instantiate the bare
# interface.
from abc import ABC, abstractmethod

# `dataclass(frozen=True)` builds an immutable typed record. Six fields,
# no behaviour — pure data. `frozen=True` so callers can't mutate a
# returned response after the provider hands it back (Langfuse trace
# attributes + idempotency caching both rely on the response being a
# stable snapshot).
from dataclasses import dataclass


@dataclass(frozen=True)
class LlmResponse:
    """Typed return shape every LlmClient implementation produces.

    WHAT: six-field immutable record describing one LLM generation.
          `content` is the assistant's reply (what the caller hands to
          MessageResponse); the rest is observability metadata that
          flows into Langfuse (D4) + Sentry tags + the idempotency
          cache without re-running the call.
    WHEN: returned by `LlmClient.generate(...)` once per successful
          chat turn. Never constructed by callers — it's the
          provider's job to build it from the upstream API response.
    WHY:  one typed shape across every provider keeps `run_turn.py`
          provider-agnostic (per A10). A new provider added in Day 6+
          builds the same dataclass + the caller code doesn't change.

    Fields:
      content              — assistant reply text. Goes straight into
                             MessageResponse.content.
      provider             — provider identifier. "gemini" today;
                             "openrouter" / "anthropic" in Day 6+.
                             Used for Sentry + Langfuse tagging.
      model                — provider-specific model id (e.g.
                             "gemini-2.5-flash"). Used for Langfuse
                             + per-model cost attribution.
      prompt_tokens        — input-side token count reported by the
                             provider. Zero if the provider doesn't
                             report it (some streaming-only APIs).
      completion_tokens    — output-side token count reported by the
                             provider. Zero on the same caveat.
      latency_milliseconds — wall-clock duration of the provider
                             call, measured client-side (so it
                             includes network + serialisation, not
                             just the provider's reported time).
    """

    content: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_milliseconds: int


class LlmClientTimeoutError(Exception):
    """The provider call exceeded its timeout budget.

    WHAT: typed exception raised by an LlmClient.generate() call when
          the upstream provider took longer than the configured
          timeout (30s today per the Day-5 directive).
    WHEN: raised inside the provider's try/asyncio.wait_for block;
          caught by `run_turn.py` + mapped to a 504 envelope so the
          caller (public-api) can distinguish "upstream slow" from
          "we crashed".
    WHY:  E1 says v2 must be 50%-faster than chat-ai on user-
          interactive endpoints; a hung LLM call would blow that
          budget. Tight timeout + typed surfacing means the caller
          can retry against a different provider (Day 6+ routing)
          instead of waiting on a dead request.
    """

    pass


class LlmClientUpstreamError(Exception):
    """The provider returned an error response (HTTP 5xx, quota, auth).

    WHAT: typed exception raised when the upstream API returns a
          non-success status or surfaces a structured error
          (rate-limit, auth-fail, quota-exceeded, model unavailable).
    WHEN: raised inside the provider after parsing the upstream
          response; caught by `run_turn.py` + mapped to a 502
          envelope.
    WHY:  separates "they're broken" (502) from "we're broken" (500)
          from "they're slow" (504). Three distinct operator
          responses; three distinct envelope codes; one exception
          per shape keeps the dispatch table flat (A2.1).
    """

    pass


class LlmClient(ABC):
    """Abstract LLM client every provider implements.

    WHAT: declares ONE async method, `generate(...)`, taking a four-
          argument keyword-only signature + returning an LlmResponse.
    WHEN: subclassed by concrete providers (GeminiClient today;
          OpenRouterClient + AnthropicClient in Day 6+). Consumers in
          `run_turn.py` accept this type and call `.generate(...)`
          without caring which provider sits behind it.
    WHY:  A10 — LLM-agnostic abstraction. ALL routing paths share
          ONE interface so the routing decision is a one-line swap
          (`client = GeminiClient(...)` vs `client = AnthropicClient(...)`)
          rather than a refactor.

    Subclass contract:
      - Build an LlmResponse with all six fields populated.
      - Honour the 30s timeout budget (raise LlmClientTimeoutError on
        breach).
      - Surface upstream API errors as LlmClientUpstreamError.
      - Emit a Langfuse trace span per D4 with provider + model +
        token counts + latency + temperature as attributes.
    """

    @abstractmethod
    async def generate(
        self,
        *,
        prompt: str,
        user_message: str,
        temperature: float,
        max_tokens: int,
    ) -> LlmResponse:
        """Generate an assistant reply against the provider's API.

        WHAT: sends `prompt` (system instruction / soul-file layered
              prompt) + `user_message` (the latest user turn) to the
              upstream LLM with the requested sampling parameters;
              returns the typed LlmResponse on success.
        WHEN: called once per chat turn from `run_turn.py` AFTER the
              soul-file lookup populates `prompt`.
        WHY:  one method covers every provider's "give me a reply"
              surface. Streaming (`/v2/turn-stream`) gets its own
              method when it lands (separate path per A16); today
              everyone reads non-streamed.

        Args:
          prompt        — system / instruction prompt. The soul-file
                          library's 4-layer composed prompt today
                          (Day 5); Day 8+ adds user-memory context
                          before this becomes the LLM input.
          user_message  — the user's latest chat-turn text.
          temperature   — sampling temperature; provider clamps to its
                          accepted range internally.
          max_tokens    — output-side token cap. Provider may emit
                          fewer; never more.

        Returns:
          LlmResponse with all six fields populated.

        Raises:
          LlmClientTimeoutError — provider exceeded the 30s budget.
          LlmClientUpstreamError — provider returned a non-success
                                   response or surfaced an API error.
        """
        ...


# ===========================================================================
# RELATED FILES:
#   __init__.py    — re-exports LlmClient + LlmResponse + the exceptions
#   gemini.py      — concrete provider implementing this interface
#   ../run_turn.py — consumer; the only place that catches the two
#                    typed exceptions + maps them to envelopes
#   ../../tests/test_llm_client_gemini.py
#                  — exercises the contract (mock + env-gated integration)
#   ../../../yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                  — A10 (LLM-agnostic abstraction) + E1 (50%-faster
#                    latency budget; 30s timeout enforces it)
# ===========================================================================
