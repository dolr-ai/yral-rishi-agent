"""Phase 25.2 — per-process LLM routing + per-provider concurrency cap.

Single source of truth for `process_name → (provider, model, base_url,
api_key_secret_path)`. See docs/PHASE-25-DESIGN.md for the 3 design
decisions (in-house client, process names, per-provider semaphore) and
the 5 resolved open questions.

What lives here (Phase 25.2 + 25.3 scope):
  - PROCESS_NAMES tuple
  - PROVIDERS dict (concurrency cap, base_url, secret path, cost-basis)
  - LLM_DEFAULTS (process → provider + model + timeout) — prod defaults
  - call() — main dispatcher; routes Gemini to gemini.py, others to
    openai_compatible.py via a uniform interface
  - current_config() — read accessor for admin endpoint / dashboard
  - _semaphore() — per-provider lazy semaphore cache
  - Env-var override (Q3): LLM_PROCESS__<UPPER_NAME>=<provider>/<model>

What's NOT here yet (deferred to follow-up PRs):
  - DB-backed overrides + reload_config_from_db (25.4)
  - PATCH /admin/llm-registry endpoint (25.4 — chains immediately)
  - Cost recording to llm_costs table (25.5; pg_dump per Rule 9)
  - Retry-ladder → Google Chat webhook → bounded Gemini fallback (25.x)
  - registry.call_stream() — streaming dispatcher. Chat hot-path
    orchestration (NSFW fallback, multimodal, archetype tuning) stays
    in ai_client.generate_response_stream until 25.3b.
"""

import asyncio
import logging
import os
import time
from collections import deque
from threading import Lock
from typing import Any

from services.llm_types import LlmResponse

logger = logging.getLogger(__name__)


# Brief task 4 (2026-06-26) — per-process primary-provider failure
# counter. Records each time call()'s primary attempt raises and we
# fall back to the secondary provider. Read by the admin dashboard
# tile + paired with a Sentry warning at the same site, so a runpod_vllm
# brown-out shows up in three places (Sentry alert, dashboard, logs)
# instead of being silently masked by the fallback. Gates task 9
# (fallback removal): once these counters are routinely zero AND the
# Sentry alert has fired on a real outage at least once, the soft-
# failure silence pattern is safe to drop.
#
# In-memory + per-replica by design — cross-replica aggregation lives
# in Sentry (the canonical view). This counter is the "right now, on
# this worker" signal for the dashboard. Bounded per key so a runaway
# outage can't grow the deque unbounded.
_PRIMARY_FAILURE_WINDOW_SEC = 60 * 60
_PRIMARY_FAILURE_MAX_PER_KEY = 1000
_PRIMARY_FAILURES: dict[tuple[str, str], deque[float]] = {}
_PRIMARY_FAILURES_LOCK = Lock()


def _record_primary_failure(process: str, primary_provider: str) -> None:
    """Append the current time to the per-(process, primary_provider)
    deque so the dashboard tile can report a 1h count."""
    key = (process, primary_provider)
    with _PRIMARY_FAILURES_LOCK:
        dq = _PRIMARY_FAILURES.get(key)
        if dq is None:
            dq = deque(maxlen=_PRIMARY_FAILURE_MAX_PER_KEY)
            _PRIMARY_FAILURES[key] = dq
        dq.append(time.time())


def primary_failure_counts_last_hour() -> dict[tuple[str, str], int]:
    """Snapshot of primary-failure counts in the last hour, keyed by
    (process, primary_provider). Trims stale entries lazily on read so
    a quiet period naturally drains the counter."""
    cutoff = time.time() - _PRIMARY_FAILURE_WINDOW_SEC
    out: dict[tuple[str, str], int] = {}
    with _PRIMARY_FAILURES_LOCK:
        for key, dq in _PRIMARY_FAILURES.items():
            while dq and dq[0] < cutoff:
                dq.popleft()
            if dq:
                out[key] = len(dq)
    return out


PROCESS_NAMES: tuple[str, ...] = (
    "user_chat_main",
    # 25.3b: NSFW user chat routes through OpenRouter today (different
    # safety policy than Gemini). Separate process so the admin
    # dashboard can route NSFW independently of mainline chat.
    "user_chat_main_nsfw",
    # Phase 21αβ.H12 (2026-06-08) — image/multimodal chat is its own
    # routable process. The 2026-06-08 bug: Rishi flipped user_chat_main
    # to runpod_vllm (text-only Qwen pod), chat messages with images
    # silently failed. Vision-bearing requests now route here; text-only
    # stays on user_chat_main. Routing decision is made at the chat-send
    # boundary, based on whether the built message payload contains
    # image_url / input_image parts. Same pattern as audio_transcription.
    "user_chat_main_multimodal",
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
    # Phase 0 Request Images track B — daily collage theme generation.
    # Fires from both on-demand user path AND nightly pre-gen loop;
    # gemini covers both without a sync/async split. One call per
    # bot per UTC day, tiny prompt (~500 tokens) → negligible cost
    # next to the ~$0.27 batch it feeds.
    "collage_theme_generator",
    # Phase 22.3 — nightly video ideas generation per active AI influencer.
    # Background cron + cold-start one-shot, not user-facing → defaults to
    # internal_vllm for $0 marginal cost (same category as quality_scorer +
    # memory_extraction). Rishi can flip via /admin/llm-routing if quality
    # ever needs gemini.
    "video_idea_generation",
    # Phase 21γ.P34.M1 (2026-06-16) — Discovery Feed bot classification.
    # One LLM call per influencer (backfill + on-create), tags `gender`
    # + `bot_type` from {profile photo, name, system prompt, description}
    # in a single multimodal pass. Vision-enabled runpod_vllm primary;
    # NEVER gemini (in ASYNC_PROCESSES_NEVER_GEMINI below). The only
    # LLM call in the whole Discovery Feed pipeline.
    "influencer_classification",
)
# Phase 23 note: skilled influencers (Kareena with nutrition_coach,
# future english_coach / daily_briefing / travel_advisor / etc.) do
# NOT get their own registry process. The skill content lives in the
# Soul File composition (skill prompt block + user_skill_state plan
# layer), not in routing. User-facing chat with a skilled influencer
# flows through `user_chat_main` exactly like Tara or any non-skilled
# influencer; proactive skill check-ins flow through whatever process
# the proactive caller uses. Per-skill cost tracking, if ever needed,
# is a `skill_slug` tag column on `llm_costs` — not a process split.


# Provider metadata — concurrency caps + endpoint + secret path + cost-basis.
# All values are ADHD-friendly editable via the future admin dashboard;
# the dict below is the in-code default that ships with the image.
#
# secret_path is the on-container file path. file-first; env-var fallback
# happens in _resolve_api_key.
PROVIDERS: dict[str, dict[str, Any]] = {
    "gemini": {
        "concurrency_cap": 20,
        "base_url": None,  # native API — gemini.py handles the wire format
        "secret_path": "/run/secrets/GEMINI_API_KEY",
        "env_fallback": "GEMINI_API_KEY",
        "cost_basis": "real",
        "cost_per_1k_input_usd": 0.001,
        "cost_per_1k_output_usd": 0.003,
        # 25.3b: capability flags surface in current_config() so the
        # admin dashboard can show "this provider supports
        # chat/streaming/transcribe." Today only Gemini supports audio
        # transcribe; OpenAI-spec providers do not (different endpoint).
        "supports_chat": True,
        "supports_stream": True,
        "supports_transcribe": True,
        # Phase 21αβ.H12 — vision capability flag. Gemini Flash is
        # natively multimodal (image_url parts in messages). Used by
        # both the routing detector in chat-send and the
        # llm_routing_admin capability guard.
        "supports_vision": True,
    },
    "openai": {
        "concurrency_cap": 10,
        "base_url": "https://api.openai.com/v1",
        "secret_path": "/run/secrets/OPENAI_API_KEY",
        "env_fallback": "OPENAI_API_KEY",
        "cost_basis": "real",
        "cost_per_1k_input_usd": 0.005,
        "cost_per_1k_output_usd": 0.015,
        "supports_chat": True,
        "supports_stream": True,
        "supports_transcribe": False,
        # OpenAI GPT-4 family supports vision; the SDK's image_url shape
        # matches what we emit from _build_user_content. (We don't
        # currently route any process here, but the flag stays accurate
        # so a future admin flip won't break the multimodal guard.)
        "supports_vision": True,
    },
    "openrouter": {
        "concurrency_cap": 10,
        "base_url": "https://openrouter.ai/api/v1",
        "secret_path": "/run/secrets/OPENROUTER_API_KEY",
        "env_fallback": "OPENROUTER_API_KEY",
        "cost_basis": "real",
        "cost_per_1k_input_usd": 0.001,
        "cost_per_1k_output_usd": 0.003,
        "supports_chat": True,
        "supports_stream": True,
        "supports_transcribe": False,
        # OpenRouter's Gemini-2.5-flash route is multimodal (same
        # underlying model). True even though we don't currently use it
        # for vision-bearing chat (NSFW path is text-only today).
        "supports_vision": True,
    },
    "internal_vllm": {
        "concurrency_cap": 5,
        "base_url": "https://model.ansuman.yral.com/v1",
        "secret_path": "/run/secrets/INTERNAL_VLLM_API_KEY",
        "env_fallback": "INTERNAL_VLLM_API_KEY",
        "cost_basis": "synthetic",
        # Synthetic per-token cost — compute share, not $ to a vendor.
        # See "Cost basis" section of design doc for the rationale.
        "cost_per_1k_input_usd": 0.00005,
        "cost_per_1k_output_usd": 0.00005,
        "default_extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        "supports_chat": True,
        "supports_stream": True,
        "supports_transcribe": False,
        # Anshuman's Qwen pod is text-only. Routing user_chat_main_multimodal
        # here would silently drop images — the H12 capability guard
        # in llm_routing_admin will refuse such a flip.
        "supports_vision": False,
    },
    "runpod_vllm": {
        # Saikat's vLLM serving. Originally a runpod proxy URL (hence the
        # `runpod_vllm` provider key — kept stable so LLM_DEFAULTS,
        # dashboards, and the leak-guard allow-list don't churn). URL
        # history (vendor moved, provider key stable across moves):
        #   2026-06-10 → saikat-llm-medium-fast.yral.com (dynamic scaling)
        #   2026-06-19 → saikat-llm-mixture-of-experts.yral.com (current,
        #               MoE architecture; Session 6 verified empirically
        #               2026-06-19 via GET /v1/models + vision smoke —
        #               same Qwen/Qwen3.6-35B-A3B-FP8, same auth, ~1.3s
        #               classification call vs ~3.4s on the prior URL).
        # The bearer token rotation flow is unchanged:
        # GitHub Secret RUNPOD_VLLM_API_KEY → rotate-runpod-vllm-key
        # workflow → swarm secret → /run/secrets/RUNPOD_VLLM_API_KEY.
        "concurrency_cap": 5,
        "base_url": "https://saikat-llm-mixture-of-experts.yral.com/v1",
        "secret_path": "/run/secrets/RUNPOD_VLLM_API_KEY",
        "env_fallback": "RUNPOD_VLLM_API_KEY",
        "cost_basis": "synthetic",
        # Synthetic per-token cost — Saikat's pod is a fixed runpod rental;
        # same accounting shape as internal_vllm.
        "cost_per_1k_input_usd": 0.00005,
        "cost_per_1k_output_usd": 0.00005,
        # Match internal_vllm's chat_template_kwargs since this is also a
        # Qwen-family model on vLLM — "thinking" mode produces verbose
        # internal reasoning we don't want to pay tokens for.
        "default_extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        "supports_chat": True,
        "supports_stream": True,
        "supports_transcribe": False,
        # Phase 21γ.P34.M1 (2026-06-16) — Session 6 verified empirically
        # via 5 curls (see memory feedback_empirical_first_then_ping):
        # GET /v1/models returned Qwen/Qwen3.6-35B-A3B-FP8 + multimodal
        # smoke (prompt_tokens=341 with image vs ~100 text-only) both
        # green + 3.4 sec/bot with enable_thinking disabled. Flipping
        # False → True so the H12 capability guard permits the new
        # `influencer_classification` process to send avatar images.
        # The runpod_vllm default_extra_body already disables thinking
        # mode, so classification inherits the 10× latency win.
        "supports_vision": True,
    },
    "hetzner": {
        # Hetzner Inference API (inference.hetzner.com) — the free-for-now
        # experimental OpenAI-spec endpoint (token from experiments.hetzner.com),
        # open-weight models on EU servers (DE/FI). Serves the SAME
        # Qwen/Qwen3.6-35B-A3B-FP8 as runpod_vllm above, so the async processes
        # move over with NO model-string change.
        # SHELVED 2026-08-14: Hetzner free = 10 requests/min per API key (their
        # published limit), far below our async volume (quality_scorer alone is
        # ~200/min), so NOTHING routes here — this provider + the client-side rate
        # limiter are kept DORMANT + ready to flip back on if Hetzner raises its
        # limits or offers a paid tier. Token flow: GitHub Secret
        # HETZNER_INFERENCE_API_KEY → rotate-hetzner-inference-key workflow →
        # swarm secret → /run/secrets/HETZNER_INFERENCE_API_KEY.
        "concurrency_cap": 5,
        # Client-side pacing so we don't blast past Hetzner's free rate limit and
        # eat 429s (it returns 429 + Retry-After: 5). Requests/min, PER REPLICA —
        # the bucket is in-process, so set HETZNER_INFERENCE_RATE_PER_MIN to
        # (Hetzner's account limit ÷ agent replica count) once the real number is
        # known. This default is a conservative starting guess. Absent on other
        # providers = no client-side pacing.
        "rate_limit_per_min": int(
            os.environ.get("HETZNER_INFERENCE_RATE_PER_MIN", "120")
        ),
        "base_url": "https://inference.hetzner.com/api/v1",
        "secret_path": "/run/secrets/HETZNER_INFERENCE_API_KEY",
        "env_fallback": "HETZNER_INFERENCE_API_KEY",
        "cost_basis": "synthetic",
        # Free during the experiment → $0. Kept 'synthetic' (not 'real') so the
        # dashboard cap math doesn't count it as paid vendor spend.
        "cost_per_1k_input_usd": 0.0,
        "cost_per_1k_output_usd": 0.0,
        # Same Qwen family as runpod_vllm — suppress the verbose "thinking"
        # tokens we don't want to pay latency for.
        "default_extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        "supports_chat": True,
        "supports_stream": True,
        "supports_transcribe": False,
        # Vision UNVERIFIED on Hetzner's serving config: the model supports
        # images, but the endpoint may not enable the projector. Start False so
        # the H12 capability guard refuses to route the one vision-dependent
        # async process (influencer_classification) here until an empirical
        # multimodal smoke test flips it True.
        "supports_vision": False,
    },
    "ollama": {
        "concurrency_cap": 2,
        "base_url": "http://ollama:11434/v1",
        "secret_path": "/run/secrets/OLLAMA_API_KEY",
        "env_fallback": "OLLAMA_API_KEY",
        "cost_basis": "synthetic",
        "cost_per_1k_input_usd": 0.00001,
        "cost_per_1k_output_usd": 0.00001,
        "supports_chat": True,
        "supports_stream": True,
        "supports_transcribe": False,
        # Local Ollama models we host are text-only (Qwen / Llama base).
        # Vision-capable local models would need a different image-encoding
        # path; we flip this when/if that's wired up.
        "supports_vision": False,
    },
}


# Default routing — each process points at one (provider, model) pair.
# Optional `fallback_provider` + `fallback_model` fields enable in-call
# failover (see `call()`) — primary fails → log warning + Sentry → try
# fallback once → record both outcomes.
#
# Routing policy 2026-06-08 (Rishi):
#   - Sync user-waiting (user_chat_main, audio_transcription, creator
#     tools where a human is on the screen): gemini, no fallback. TTFT
#     matters; gemini wins.
#   - Async background (the 6 in ASYNC_PROCESSES below): runpod_vllm
#     primary (Saikat's pod, Qwen3.6-35B-A3B-FP8) → internal_vllm
#     fallback (Anshuman's pod, Qwen3.6-27B-FP8). NEVER gemini — leak
#     guard in `call()` will alert if it ever happens.
#
# Why no gemini fallback for async: a 4-day audit showed the
# quality_scorer loop quietly burned $22 on gemini due to a DB-override
# cache that didn't load (the bug that motivated this PR). Removing
# gemini from the async chain means even if EVERYTHING else regresses,
# we land on internal_vllm or fail loud — never silent gemini spend.
LLM_DEFAULTS: dict[str, dict[str, Any]] = {
    # ─── Sync user-waiting ──────────────────────────────────────────
    "user_chat_main": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "timeout_sec": 60.0,
    },
    "user_chat_main_nsfw": {
        # NSFW path routes through OpenRouter today (Gemini's content
        # policy is stricter than what we want for NSFW companions).
        "provider": "openrouter",
        "model": "google/gemini-2.5-flash",
        "timeout_sec": 60.0,
    },
    # Phase 21αβ.H12 — vision-bearing chat. Gemini default; NO fallback.
    # Reason: a text-only fallback would silently drop images at the
    # exact moment vision matters. Failing loud (NO_PROVIDER surfaced to
    # mobile) is the right behavior; the operator can flip user_chat_main
    # to a cost-saving text-only provider WITHOUT this process moving.
    "user_chat_main_multimodal": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "timeout_sec": 60.0,
    },
    "audio_transcription": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "timeout_sec": 60.0,
    },
    # ─── Sync creator-facing (creator is on the screen waiting) ─────
    # Kept on gemini per `feedback_llm_defaults_sync_paths_use_gemini`
    # memory. TTFT 4-12s on vLLM made Coach feel broken on 2026-06-03.
    "soul_file_coach": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "timeout_sec": 60.0,
    },
    "character_generator": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "timeout_sec": 180.0,
    },
    "ai_influencer_wizard_simulation": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "timeout_sec": 180.0,
    },
    "soul_file_recommendations": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "timeout_sec": 120.0,
    },
    "collage_theme_generator": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "timeout_sec": 30.0,
    },
    # ─── Async background — Saikat primary + Anshuman fallback ──────
    "proactive_generation": {
        "provider": "runpod_vllm",
        "model": "Qwen/Qwen3.6-35B-A3B-FP8",
        "fallback_provider": "internal_vllm",
        "fallback_model": "Qwen/Qwen3.6-27B-FP8",
        "timeout_sec": 120.0,
    },
    "quality_scorer": {
        "provider": "runpod_vllm",
        "model": "Qwen/Qwen3.6-35B-A3B-FP8",
        "fallback_provider": "internal_vllm",
        "fallback_model": "Qwen/Qwen3.6-27B-FP8",
        "timeout_sec": 120.0,
    },
    "memory_extraction": {
        "provider": "runpod_vllm",
        "model": "Qwen/Qwen3.6-35B-A3B-FP8",
        "fallback_provider": "internal_vllm",
        "fallback_model": "Qwen/Qwen3.6-27B-FP8",
        "timeout_sec": 120.0,
    },
    "memory_consolidation": {
        "provider": "runpod_vllm",
        "model": "Qwen/Qwen3.6-35B-A3B-FP8",
        "fallback_provider": "internal_vllm",
        "fallback_model": "Qwen/Qwen3.6-27B-FP8",
        "timeout_sec": 180.0,
    },
    "nudge_generation": {
        "provider": "runpod_vllm",
        "model": "Qwen/Qwen3.6-35B-A3B-FP8",
        "fallback_provider": "internal_vllm",
        "fallback_model": "Qwen/Qwen3.6-27B-FP8",
        "timeout_sec": 120.0,
    },
    "video_idea_generation": {
        "provider": "runpod_vllm",
        "model": "Qwen/Qwen3.6-35B-A3B-FP8",
        "fallback_provider": "internal_vllm",
        "fallback_model": "Qwen/Qwen3.6-27B-FP8",
        "timeout_sec": 120.0,
    },
    # Phase 21γ.P34.M1 — multimodal classification of AI influencers.
    # NO fallback: internal_vllm is text-only (supports_vision=False),
    # so an automatic fallback would silently drop the avatar image and
    # produce garbage labels. If runpod_vllm is down the job just queues
    # for the next backfill sweep — no rush, this is purely offline.
    "influencer_classification": {
        "provider": "runpod_vllm",
        "model": "Qwen/Qwen3.6-35B-A3B-FP8",
        "timeout_sec": 60.0,
    },
}


# Processes that MUST NEVER hit gemini. If `_check_async_gemini_leak`
# observes the resolved provider == "gemini" for any of these, it logs
# error + fires Sentry. Used in `call()` post-config-resolve so DB
# overrides, env overrides, AND code defaults are all covered.
ASYNC_PROCESSES_NEVER_GEMINI: frozenset[str] = frozenset(
    {
        "proactive_generation",
        "quality_scorer",
        "memory_extraction",
        "memory_consolidation",
        "nudge_generation",
        "video_idea_generation",
        # Phase 21γ.P34.M1 — Discovery Feed classification. Excluded from
        # gemini even though it's "synthetic offline" — the cost rule that
        # motivated this set says any new background LLM defaults to vLLM
        # unless there's a sync user waiting (there isn't here).
        "influencer_classification",
    }
)


# Per-provider asyncio.Semaphore. Lazy-init on first use to avoid pinning
# to a specific event-loop at import time.
_semaphores: dict[str, asyncio.Semaphore] = {}


def _semaphore(provider: str) -> asyncio.Semaphore:
    if provider not in _semaphores:
        cap = PROVIDERS.get(provider, {}).get("concurrency_cap", 10)
        _semaphores[provider] = asyncio.Semaphore(cap)
    return _semaphores[provider]


# ── Client-side rate limiting (2026-08-14) ───────────────────────────────
# Hetzner's free tier is fair-use rate-limited: it returns 429 + Retry-After: 5
# once we exceed the cap. The concurrency semaphore above bounds PARALLELISM but
# not the request RATE, so we were hammering the limit (~82% of async calls
# 429'd). Two mechanisms pace us to their limit instead of hammering it:
#   1. a per-provider token bucket callers await before dispatching (below), and
#   2. a 429-aware retry that honours Retry-After (in call()).
_MAX_RATE_RETRIES = 2
_DEFAULT_RETRY_AFTER_SEC = 5.0
_MAX_RETRY_AFTER_SEC = 15.0


class _RateLimiter:
    """Async token bucket — paces acquisitions to <= per_min per minute with a
    small burst. acquire() waits up to max_wait for a token and returns False if
    it can't get one in time, so the caller fails fast instead of dispatching a
    request that would just time out under load."""

    def __init__(self, per_min: float) -> None:
        self._rate = per_min / 60.0  # tokens per second
        self._capacity = max(1.0, per_min / 12.0)  # ~5s burst
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, max_wait: float) -> bool:
        deadline = time.monotonic() + max_wait
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._last) * self._rate
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                wait = (1.0 - self._tokens) / self._rate
            if time.monotonic() + wait > deadline:
                return False
            await asyncio.sleep(wait)


_rate_limiters: dict[str, _RateLimiter] = {}


def _rate_limiter(provider: str) -> _RateLimiter | None:
    """Per-provider token bucket, or None if the provider sets no rate limit."""
    per_min = (PROVIDERS.get(provider) or {}).get("rate_limit_per_min")
    if not per_min:
        return None
    if provider not in _rate_limiters:
        _rate_limiters[provider] = _RateLimiter(float(per_min))
    return _rate_limiters[provider]


def _retry_after_seconds(exc: BaseException) -> float | None:
    """If exc is an HTTP 429, return the Retry-After delay in seconds (from the
    header, default 5, capped 15); else None (not retryable at this layer)."""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        resp = getattr(exc, "response", None)
        if resp is not None and getattr(resp, "status_code", None) == 429:
            raw = resp.headers.get("retry-after") if hasattr(resp, "headers") else None
            try:
                return (
                    min(float(raw), _MAX_RETRY_AFTER_SEC)
                    if raw
                    else _DEFAULT_RETRY_AFTER_SEC
                )
            except (TypeError, ValueError):
                return _DEFAULT_RETRY_AFTER_SEC
    return None


def _resolve_api_key(provider: str) -> str:
    """File-first secret resolution: /run/secrets/<NAME> then env var.
    Matches the pattern in redis_config.get_redis_url."""
    meta = PROVIDERS.get(provider) or {}
    path = meta.get("secret_path")
    if path and os.path.exists(path):
        try:
            with open(path) as f:
                val = f.read().strip()
            if val:
                return val
        except OSError:
            pass
    env_name = meta.get("env_fallback")
    if env_name:
        val = os.environ.get(env_name)
        if val:
            return val
    raise RuntimeError(f"llm_registry: no API key configured for provider={provider}")


# 25.4: in-memory cache of DB-backed overrides. Populated by
# reload_config_from_db() on startup + after every admin PATCH. None
# means "not yet loaded" — treat as empty; the lookup will fall through
# to env override + LLM_DEFAULTS.
_db_overrides: dict[str, dict[str, Any]] | None = None


def _process_config(process: str) -> dict[str, Any]:
    """Resolve the effective config for one process. Order:
    1. DB override (25.4: llm_process_config table, hot-edited via
       PATCH /admin/llm-routing).
    2. Env override (Q3): LLM_PROCESS__<UPPER_NAME>=<provider>/<model>.
    3. LLM_DEFAULTS.
    """
    if process not in LLM_DEFAULTS:
        raise KeyError(
            f"llm_registry: unknown process '{process}'. Known: {sorted(PROCESS_NAMES)}"
        )
    cfg = dict(LLM_DEFAULTS[process])

    # 1. DB override (highest priority — admin-pinned)
    if _db_overrides and process in _db_overrides:
        cfg.update(_db_overrides[process])

    # 2. Env override (lower priority than DB so admin can pin past env)
    env_key = f"LLM_PROCESS__{process.upper()}"
    override = os.environ.get(env_key)
    if override and "/" in override:
        # Only honor env if no DB pin exists — DB wins.
        if not (_db_overrides and process in _db_overrides):
            provider, _, model = override.partition("/")
            cfg["provider"] = provider.strip()
            cfg["model"] = model.strip()
            logger.info("llm_registry: process=%s overridden via %s", process, env_key)

    return cfg


async def reload_config_from_db(pool) -> int:
    """Pull overrides from llm_process_config table into the in-memory
    cache. Returns the number of overrides loaded. Called from app
    startup + after every PATCH /admin/llm-routing.

    Safe if the table doesn't exist — logs a warning and leaves the
    cache empty (registry falls back to env + LLM_DEFAULTS). This lets
    25.4 code deploy before the migration is applied per Rule 9
    (Rishi pg_dumps + applies migration manually)."""
    global _db_overrides
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT process, provider, model, timeout_sec FROM llm_process_config"
            )
    except Exception as e:
        logger.warning(
            "llm_registry: reload_config_from_db skipped (%s); using env + defaults", e
        )
        _db_overrides = {}
        return 0

    overrides: dict[str, dict[str, Any]] = {}
    for row in rows:
        process = row["process"]
        if process not in LLM_DEFAULTS:
            logger.warning(
                "llm_registry: DB row for unknown process %r — ignored", process
            )
            continue
        cfg: dict[str, Any] = {"provider": row["provider"], "model": row["model"]}
        if row["timeout_sec"] is not None:
            cfg["timeout_sec"] = float(row["timeout_sec"])
        overrides[process] = cfg

    _db_overrides = overrides
    logger.info("llm_registry: loaded %d DB overrides", len(overrides))
    return len(overrides)


async def _broadcast_invalidate(reason: str) -> None:
    """After a DB write, ask the Redis pub/sub layer to broadcast a
    cache-invalidation to ALL replicas. Failure here is non-fatal: the
    local replica's cache was already refreshed via
    `reload_config_from_db()` above; only OTHER replicas miss the
    invalidation, which is the bug we accept-as-degraded when Redis
    is unreachable. Log + move on."""
    try:
        from services import llm_routing_pubsub

        await llm_routing_pubsub.publish_invalidate(reason=reason)
    except Exception as e:
        logger.warning("llm_registry: broadcast invalidate failed: %s", e)


async def upsert_override(
    pool,
    *,
    process: str,
    provider: str,
    model: str,
    timeout_sec: float | None,
    updated_by: str,
) -> None:
    """Write an override row + refresh the in-memory cache + broadcast
    the change to other replicas. Caller is the admin PATCH endpoint;
    auth has already been checked there."""
    if process not in LLM_DEFAULTS:
        raise KeyError(f"llm_registry.upsert_override: unknown process '{process}'")
    if provider not in PROVIDERS:
        raise KeyError(f"llm_registry.upsert_override: unknown provider '{provider}'")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO llm_process_config (process, provider, model, timeout_sec, updated_at, updated_by)
            VALUES ($1, $2, $3, $4, NOW(), $5)
            ON CONFLICT (process) DO UPDATE
              SET provider = EXCLUDED.provider,
                  model = EXCLUDED.model,
                  timeout_sec = EXCLUDED.timeout_sec,
                  updated_at = NOW(),
                  updated_by = EXCLUDED.updated_by
            """,
            process,
            provider,
            model,
            timeout_sec,
            updated_by,
        )
    await reload_config_from_db(pool)
    await _broadcast_invalidate(reason=f"upsert:{process}")


async def delete_override(pool, *, process: str, updated_by: str) -> bool:
    """Remove an override — process falls back to env + LLM_DEFAULTS.
    Returns True if a row was deleted. Broadcasts invalidation to all
    replicas after the local cache is refreshed."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM llm_process_config WHERE process = $1", process
        )
    await reload_config_from_db(pool)
    # asyncpg returns "DELETE N" — parse the count
    deleted = result.split(" ")[-1] if isinstance(result, str) else "0"
    if deleted != "0":
        await _broadcast_invalidate(reason=f"delete:{process}")
    return deleted != "0"


def current_config(process: str) -> dict[str, Any]:
    """Public read accessor for admin endpoint + dashboard.

    Returns the resolved process config merged with provider metadata
    (cost-basis, concurrency cap). Does NOT include the API key."""
    cfg = _process_config(process)
    provider_meta = PROVIDERS.get(cfg["provider"], {})
    return {
        "process": process,
        "provider": cfg["provider"],
        "model": cfg["model"],
        "timeout_sec": cfg.get("timeout_sec", 60.0),
        "base_url": provider_meta.get("base_url"),
        "cost_basis": provider_meta.get("cost_basis"),
        "concurrency_cap": provider_meta.get("concurrency_cap"),
    }


def has_image_content(messages: list[dict]) -> bool:
    """Phase 21αβ.H12 — detect whether a chat-send payload carries image
    content. Used at the routing boundary to decide between user_chat_main
    (text-only) and user_chat_main_multimodal (vision).

    Canonical image shapes we recognize (mirrors _build_user_content in
    services/ai_client.py):
      - {"type": "image_url", "image_url": {"url": "..."}}  ← OpenAI-style
      - {"type": "input_image", ...}                         ← Responses API
      - {"inlineData": {"mimeType": "...", "data": "..."}}   ← Gemini-native

    All three appear in some path of the message-building code or in
    historical chat-ai DTOs; recognizing all three keeps the detector
    robust as the builders evolve."""
    if not messages:
        return False
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype in ("image_url", "input_image"):
                return True
            # Gemini-native shape doesn't use a `type` key; it nests
            # `inlineData` directly. Match either nested form.
            if "inlineData" in part or "inline_data" in part:
                return True
    return False


def _classify_outcome(exception: BaseException) -> str:
    """Phase 25.5b — map an exception to an outcome enum value for
    llm_costs.outcome. The set is the dashboard rejection-rate axis:
    rate_limit / server_error / timeout / parse_error / blocked / other.
    Tighten as new failure modes show up."""
    import asyncio
    import json

    import httpx

    from services.llm_types import LlmBlockedError

    if isinstance(exception, LlmBlockedError):
        return "blocked"
    if isinstance(exception, asyncio.TimeoutError) or isinstance(
        exception, httpx.TimeoutException
    ):
        return "timeout"
    if isinstance(exception, httpx.HTTPStatusError):
        status = getattr(getattr(exception, "response", None), "status_code", 0)
        if status == 429:
            return "rate_limit"
        if status and status >= 500:
            return "server_error"
        return "other"
    if isinstance(exception, (ValueError, json.JSONDecodeError)):
        return "parse_error"
    if isinstance(exception, httpx.RequestError):
        return "server_error"
    return "other"


async def _record_outcome(
    process: str,
    *,
    provider: str,
    model: str,
    outcome: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: float | None = None,
    error_message: str | None = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Phase 25.5 + 25.5b — write one row to llm_costs for every
    call attempt: success rows carry cost_usd; failure rows carry
    cost_usd=0 + error_message + outcome.

    Cost math: (input_tokens / 1000) * input_rate + (output_tokens / 1000)
    * output_rate. Rates come from PROVIDERS at write time so historical
    rows stay correct even if rates change later. cost_basis ('real' /
    'synthetic') is read from the same provider entry.

    Best-effort: if the table doesn't exist or any other DB error, log +
    continue. The actual LLM dispatch already returned (success or raised
    to the caller); failing to record cost MUST NOT break the request
    path."""
    try:
        from database import get_pool

        provider_meta = PROVIDERS.get(provider) or {}
        in_rate = float(provider_meta.get("cost_per_1k_input_usd") or 0)
        out_rate = float(provider_meta.get("cost_per_1k_output_usd") or 0)
        cost_basis = provider_meta.get("cost_basis") or "real"
        # Only successful calls have non-zero token counts on a vendor
        # call we paid for. Failure rows pass tokens=0 → cost=0.
        cost_usd = (input_tokens / 1000.0) * in_rate + (
            output_tokens / 1000.0
        ) * out_rate
        # Truncate error_message at 500 chars per Rishi's spec
        err = (error_message or None) and str(error_message)[:500]

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO llm_costs (process, provider, model,
                    input_tokens, output_tokens, cost_usd, cost_basis,
                    user_id, conversation_id, request_id, latency_ms,
                    outcome, error_message)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                """,
                process,
                provider,
                model,
                int(input_tokens),
                int(output_tokens),
                cost_usd,
                cost_basis,
                user_id,
                conversation_id,
                request_id,
                float(latency_ms) if latency_ms is not None else None,
                outcome,
                err,
            )
    except Exception as e:
        logger.warning("llm_registry: _record_outcome skipped (%s)", e)

    # 2026-06-11 PR-5 — Sentry on timeout for user-facing sync processes.
    # Plan §4 item E: the 4 server_error 110s soul_file_coach rows from
    # 2026-06-09 fired no Sentry alert. The leak-guard
    # (_check_async_gemini_leak) only fires for processes in
    # ASYNC_PROCESSES_NEVER_GEMINI; soul_file_coach is correctly NOT
    # there because it's a sync creator-waiting path. That left
    # user-facing timeouts silent.
    #
    # Fires Sentry ONLY for outcome=='timeout' on the explicit allow-list
    # below — every other failure mode is already covered by the dispatch
    # path's own logger.exception + Sentry breadcrumbs. Capture is
    # best-effort: if sentry_sdk import or capture fails, we swallow.
    if outcome == "timeout" and process in _USER_FACING_SYNC_PROCESSES:
        try:
            import sentry_sdk as _sentry

            _sentry.capture_message(
                f"LLM timeout on user-facing process {process!r} "
                f"(provider={provider}, latency_ms={latency_ms})",
                level="error",
            )
        except Exception:
            # Never let Sentry-side failure break the dispatch path.
            pass


# 2026-06-11 PR-5 — user-facing SYNC processes where a timeout is
# directly visible to a creator/end-user. Async-background processes
# stay out of this set (their failure-loud guard is the leak-guard
# alerting in _check_async_gemini_leak). Today only soul_file_coach
# is on the list per the plan's narrow scope; add other user-facing
# sync processes here if/when their timeouts need the same alerting.
_USER_FACING_SYNC_PROCESSES: frozenset[str] = frozenset(
    {
        "soul_file_coach",
    }
)


async def _record_cost(
    process: str,
    result: LlmResponse,
    *,
    user_id: str | None = None,
    conversation_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Back-compat thin wrapper around _record_outcome for the
    success path. Existing call sites keep using this name."""
    await _record_outcome(
        process,
        provider=result.provider,
        model=result.model,
        outcome="success",
        input_tokens=int(result.input_tokens),
        output_tokens=int(result.output_tokens),
        latency_ms=float(result.latency_ms),
        user_id=user_id,
        conversation_id=conversation_id,
        request_id=request_id,
    )


def _check_async_gemini_leak(process: str, provider: str) -> None:
    """ASYNC PROCESS → GEMINI guard. The 2026-06-08 audit revealed that
    quality_scorer was silently spending ~$22/4 days on gemini because a
    DB-override cache failed to load. This function makes the SAME class
    of failure loud — if any of the async processes resolves to gemini,
    log at error level + fire a Sentry alert. Does NOT block the call
    (operator may have intentionally routed there as last resort); the
    job here is observability, not enforcement."""
    if process not in ASYNC_PROCESSES_NEVER_GEMINI:
        return
    if provider != "gemini":
        return
    logger.error(
        "ASYNC PROCESS HIT GEMINI: process=%s provider=%s — routing leak "
        "detected. Check llm_process_config DB overrides + LLM_DEFAULTS. "
        "Allowing call to proceed for now (failing-loud, not failing-closed).",
        process,
        provider,
    )
    try:
        import sentry_sdk

        sentry_sdk.capture_message(
            f"LLM routing leak: async process {process!r} resolved to gemini",
            level="error",
        )
    except Exception:
        # Never let Sentry-side failures break the call path.
        pass


async def _do_complete(
    *,
    process: str,
    provider: str,
    model: str,
    timeout_sec: float,
    messages: list[dict],
    temperature: float | None,
    max_tokens: int | None,
    extra_body: dict | None,
    user_id: str | None,
    conversation_id: str | None,
    request_id: str | None,
) -> LlmResponse:
    """One dispatch attempt against one provider. Records cost on success
    and outcome on failure. Exceptions propagate so the caller (the
    fallback layer in `call()`) can decide whether to retry."""
    import time as _time

    provider_meta = PROVIDERS[provider]

    if provider_meta.get("supports_chat") is False:
        raise RuntimeError(
            f"llm_registry._do_complete: provider={provider!r} does not support chat (process={process!r})"
        )

    # Phase 21α.B6 — cost circuit breaker check. Runs BEFORE the
    # provider semaphore + the actual call. In SHADOW mode logs but
    # doesn't block; in ENFORCE mode raises CostCircuitBreakerOpen
    # which the FastAPI exception handler (app/main.py) translates
    # to 503 + Retry-After. FAIL OPEN: `cost_breaker.check()` swallows
    # every exception internally + returns allowed=True on any error.
    # See app/services/cost_breaker.py docstring + design doc
    # docs/b6-cost-circuit-breaker-design-2026-06-16.md.
    from services import cost_breaker as _cb

    _cb_result = await _cb.check(user_id=user_id, process=process, provider=provider)
    if not _cb_result.allowed:
        # Read the retry-after value from config (default 3600 if
        # config missing / malformed). One extra config read on the
        # blocked path is fine — blocks are rare by definition.
        _cb_cfg = await _cb.get_config()
        _retry_after = _cb._parse_int(_cb_cfg.get("b6_response_retry_after_sec"), 3600)
        _cb.raise_if_blocked(_cb_result, retry_after_sec=_retry_after)

    merged_extra = dict(provider_meta.get("default_extra_body") or {})
    if extra_body:
        merged_extra.update(extra_body)

    # Client-side rate limiting — wait for a token before dispatching so we stay
    # under the provider's published rate rather than hammering it into 429s. If
    # we can't get one within the request timeout, fail fast + record it (waiting
    # longer would just time out anyway).
    limiter = _rate_limiter(provider)
    if limiter is not None and not await limiter.acquire(
        max_wait=min(timeout_sec, 30.0)
    ):
        await _record_outcome(
            process,
            provider=provider,
            model=model,
            outcome="rate_limit",
            error_message=f"local rate limiter: no {provider} token within timeout",
        )
        raise asyncio.TimeoutError(
            f"local rate limiter: no {provider} token available within timeout"
        )

    sem = _semaphore(provider)
    async with sem:
        if provider == "gemini":
            from services.llm_clients import gemini as gemini_client

            client_module = gemini_client
        else:
            from services.llm_clients import openai_compatible

            client_module = openai_compatible

        _started = _time.monotonic()
        try:
            result = await client_module.complete(
                provider=provider,
                base_url=provider_meta.get("base_url"),
                api_key=_resolve_api_key(provider),
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=merged_extra or None,
                timeout=timeout_sec,
            )
        except Exception as exc:
            await _record_outcome(
                process,
                provider=provider,
                model=model,
                outcome=_classify_outcome(exc),
                latency_ms=(_time.monotonic() - _started) * 1000.0,
                error_message=str(exc),
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )
            raise
        await _record_cost(
            process,
            result,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
        )
        return result


async def call(
    *,
    process: str,
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra_body: dict | None = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
    request_id: str | None = None,
) -> LlmResponse:
    """Main dispatch — process name → provider's client.complete(...).

    Wraps the call in the per-provider concurrency semaphore so we never
    exceed the provider's rate-limit budget. Cost recording happens in
    the caller (25.5); this function returns the same LlmResponse shape
    the existing ai_client.generate_response returns.

    `extra_body` is the per-invocation escape hatch for provider-specific
    knobs (Gemini's `safetySettings`, vLLM's `chat_template_kwargs`).
    Caller-supplied extras override provider defaults on key collision.

    Fallback (2026-06-08): if the resolved config has a `fallback_provider`,
    a primary-provider failure triggers exactly ONE retry against the
    fallback. Both attempts get rows in llm_costs (primary as failure,
    fallback as success/failure). Sentry warning fires when a fallback
    is activated so we notice systemic primary outages even if the
    fallback covers the user.
    """
    cfg = _process_config(process)
    provider = cfg["provider"]
    model = cfg["model"]
    timeout_sec = float(cfg.get("timeout_sec") or 60.0)

    # Leak guard — async process resolving to gemini is a routing bug.
    _check_async_gemini_leak(process, provider)

    # 429-aware retry: on a rate-limit response honour Retry-After (Hetzner sends
    # Retry-After: 5) and retry a bounded number of times before giving up to the
    # fallback (if any). Keeps a transient rate-limit blip from failing an offline
    # job outright. Any non-429 error breaks out immediately.
    primary_exc: Exception | None = None
    for _attempt in range(_MAX_RATE_RETRIES + 1):
        try:
            return await _do_complete(
                process=process,
                provider=provider,
                model=model,
                timeout_sec=timeout_sec,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )
        except Exception as exc:
            primary_exc = exc
            retry_after = _retry_after_seconds(exc)
            if retry_after is not None and _attempt < _MAX_RATE_RETRIES:
                logger.info(
                    "llm_registry: %s 429 on process=%s; honouring Retry-After "
                    "%.1fs (attempt %d/%d)",
                    provider,
                    process,
                    retry_after,
                    _attempt + 1,
                    _MAX_RATE_RETRIES,
                )
                await asyncio.sleep(retry_after)
                continue
            break

    # Primary exhausted (after any 429 retries) → fallback, if configured.
    fallback_provider = cfg.get("fallback_provider")
    fallback_model = cfg.get("fallback_model") or model
    if not fallback_provider or fallback_provider not in PROVIDERS:
        raise primary_exc

    # Fallback path. Leak guard runs again so a misconfigured
    # async-process → gemini fallback still alerts.
    _check_async_gemini_leak(process, fallback_provider)

    # Brief task 4 (2026-06-26) — record + alert before the fallback attempt.
    # The counter feeds the admin dashboard tile; the Sentry warning carries
    # structured tags so a Sentry alert rule (>10 events / 5 min across any
    # process) can page on a systemic primary brown-out.
    _record_primary_failure(process, provider)
    logger.warning(
        "llm_registry: primary %s failed for process=%s; trying fallback %s. "
        "Primary error: %s",
        provider,
        process,
        fallback_provider,
        primary_exc,
    )
    try:
        import sentry_sdk

        error_type = _classify_outcome(primary_exc)
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("process", process)
            scope.set_tag("primary_provider", provider)
            scope.set_tag("fallback_provider", fallback_provider)
            scope.set_tag("error_type", error_type)
            # Full str(exc) can be long (stack-like traces from httpx);
            # 200 chars is enough to triage and keeps the Sentry event small.
            scope.set_extra("error_summary", str(primary_exc)[:200])
            sentry_sdk.capture_message(
                f"LLM fallback activated: {process} "
                f"{provider}→{fallback_provider} (error_type={error_type})",
                level="warning",
            )
    except Exception:
        # Never let Sentry-side failure break the dispatch path.
        pass

    return await _do_complete(
        process=process,
        provider=fallback_provider,
        model=fallback_model,
        timeout_sec=timeout_sec,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_body=extra_body,
        user_id=user_id,
        conversation_id=conversation_id,
        request_id=request_id,
    )


async def call_stream(
    *,
    process: str,
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra_body: dict | None = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
    request_id: str | None = None,
):
    """Streaming counterpart to call(). Yields (kind, value) tuples per
    the client.complete_stream contract — kind in {"delta", "usage", "done"}.

    Per-provider semaphore is acquired for the LIFETIME of the stream
    (until the consumer fully drains it) — that's stricter than call()
    but matches how SSE streams hold a slot in real terms.

    Phase 25.5: tallies tokens from the 'usage' yield (Anshuman gist
    quirk — usage arrives in the LAST chunk), then writes a cost row
    after the stream completes. Best-effort, never breaks the stream."""
    import json
    import time

    cfg = _process_config(process)
    provider = cfg["provider"]
    provider_meta = PROVIDERS[provider]

    if not provider_meta.get("supports_stream", False):
        raise RuntimeError(
            f"llm_registry.call_stream: provider={provider!r} does not support streaming"
        )

    merged_extra = dict(provider_meta.get("default_extra_body") or {})
    if extra_body:
        merged_extra.update(extra_body)

    started = time.monotonic()
    input_tokens = 0
    output_tokens = 0
    text_buffer = ""

    sem = _semaphore(provider)
    async with sem:
        if provider == "gemini":
            from services.llm_clients import gemini as gemini_client

            client_module = gemini_client
        else:
            from services.llm_clients import openai_compatible

            client_module = openai_compatible

        try:
            async for chunk in client_module.complete_stream(
                provider=provider,
                base_url=provider_meta.get("base_url"),
                api_key=_resolve_api_key(provider),
                model=cfg["model"],
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=merged_extra or None,
                timeout=cfg.get("timeout_sec", 60.0),
            ):
                kind, value = chunk
                if kind == "delta":
                    text_buffer += value
                elif kind == "usage":
                    try:
                        usage = json.loads(value)
                        input_tokens = int(
                            usage.get("prompt_tokens")
                            or usage.get("promptTokenCount")
                            or 0
                        )
                        output_tokens = int(
                            usage.get("completion_tokens")
                            or usage.get("candidatesTokenCount")
                            or 0
                        )
                    except (ValueError, TypeError):
                        pass
                yield chunk
        except Exception as exc:
            await _record_outcome(
                process,
                provider=provider,
                model=cfg["model"],
                outcome=_classify_outcome(exc),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=(time.monotonic() - started) * 1000.0,
                error_message=str(exc),
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )
            raise

        # Stream finished — record cost. Synthesize a minimal LlmResponse
        # for _record_cost (it only reads provider/model/tokens/latency).
        await _record_cost(
            process,
            LlmResponse(
                content=text_buffer,
                provider=provider,
                model=cfg["model"],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=(time.monotonic() - started) * 1000.0,
            ),
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
        )


async def call_transcribe(
    *,
    process: str,
    audio_url: str,
    user_id: str | None = None,
    conversation_id: str | None = None,
    request_id: str | None = None,
) -> LlmResponse:
    """Audio modality dispatcher. Today only Gemini supports transcription
    (different endpoint shape from /chat/completions). The registry
    `supports_transcribe` capability flag gates this — non-Gemini
    providers raise RuntimeError instead of silently no-op'ing."""
    cfg = _process_config(process)
    provider = cfg["provider"]
    provider_meta = PROVIDERS[provider]

    if not provider_meta.get("supports_transcribe", False):
        raise RuntimeError(
            f"llm_registry.call_transcribe: provider={provider!r} does not support audio transcription"
        )

    sem = _semaphore(provider)
    import time as _time

    _started = _time.monotonic()
    async with sem:
        if provider == "gemini":
            from services.llm_clients import gemini as gemini_client

            try:
                result = await gemini_client.transcribe(
                    provider=provider,
                    base_url=provider_meta.get("base_url"),
                    api_key=_resolve_api_key(provider),
                    model=cfg["model"],
                    audio_url=audio_url,
                    timeout=cfg.get("timeout_sec", 60.0),
                )
            except Exception as exc:
                await _record_outcome(
                    process,
                    provider=provider,
                    model=cfg["model"],
                    outcome=_classify_outcome(exc),
                    latency_ms=(_time.monotonic() - _started) * 1000.0,
                    error_message=str(exc),
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=request_id,
                )
                raise
            await _record_cost(
                process,
                result,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )
            return result
        # Future: OpenAI Whisper-via-/v1/audio/transcriptions lands here.
        raise NotImplementedError(
            f"call_transcribe for provider={provider!r} not implemented"
        )
