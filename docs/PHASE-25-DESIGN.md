# Phase 25 — Multi-provider LLM architecture: design

Status: **DRAFT — design only, no implementation yet.** Approval gate before scaffolding turns into real code.

This document is the source of truth for the 3 decisions that have to be locked before any of 25.1–25.9 ships.

---

## Decision 1 — LiteLLM vs in-house

**Choice: in-house.** Direct HTTPX against `/v1/chat/completions`.

**Reasoning:**

| Factor | LiteLLM | In-house |
|---|---|---|
| Code we own | ~0 lines (just config) | ~200 lines (HTTPX + streaming + cost-extract) |
| Code we *maintain* in production | the whole library (40k+ lines) when bugs surface | ~200 lines we wrote |
| Provider coverage out of the box | ~100 providers | OpenAI-spec only (covers OpenAI, OpenRouter, Together, vLLM, Saikat self-hosted, Ollama, Anthropic-via-router) — 100% of what Phase 25 actually needs |
| Cost-tracking integration | LiteLLM has its own opinions; would need an adapter into our `llm_costs` schema | direct write to `llm_costs` from our client, schema we own |
| Retry / timeout / cost-breaker integration with Phase 19 | indirect (LiteLLM wraps it; we override) | direct (our client respects existing breaker decorators) |
| Risk surface | 40k lines of third-party code in the hot path | code we read line by line |
| Dependency footprint | `litellm` pulls boto3, anthropic, etc. — fat for our needs | `httpx` (already in deps) |

**Rule 8 (CLAUDE.md):** "Simplicity first. If >100 lines of new code, stop and check." A symmetric in-house client modeled on the existing `gemini.py` (which lives inside `ai_client.py` today, ~150 lines of Gemini-specific code) lands at ~200 lines for `openai_compatible.py`. That's the simplicity tier. LiteLLM trades ~200 lines of our code for ~40k lines of someone else's, with no functional advantage for the providers we care about.

**Counterargument:** If Phase 25 ever needs to talk to a non-OpenAI-spec provider (e.g., direct Anthropic Messages API, direct Cohere), in-house means writing another client class. Mitigation: every provider on Phase 25's roadmap (Saikat, OpenRouter, OpenAI, Together, vLLM, Ollama, even Anthropic via OpenRouter) speaks `/v1/chat/completions`. The OpenAI spec has become the universal lingua franca. We'll cross the non-OpenAI-spec bridge if it ever comes; YAGNI for now.

**Symmetry note:** The existing Gemini path uses Google's native API (not `/v1/chat/completions`), so we keep `gemini.py` as its own client. The two clients implement the same `LlmClient` interface — that's the registry's job to dispatch.

---

## Decision 2 — Process names list

Every LLM call site is named by a stable `process` string. This is what the admin endpoint targets when flipping a process from one provider/model to another.

**Audit of current call sites** (`grep -rn "ai_client\.\|_call_gemini" app/`):

| Process name | Call site(s) | Current provider | Tier | Notes |
|---|---|---|---|---|
| `user_chat_main` | `chat.py:564, 762, 868` | Gemini (with OpenRouter fallback for some archetypes) | hot — user-facing | Latency + quality matter most |
| `audio_transcription` | `chat.py:426` | Gemini native (audio modality) | hot — user-facing | Gemini-only for now (audio support varies) |
| `proactive_generation` | `proactive.py:159` | Gemini | background | Cheap candidate → self-hosted |
| `quality_scorer` | `quality_scorer.py:62` | Gemini | background | Cheap candidate → self-hosted |
| `memory_extraction` | `memory.py:84` | Gemini | background | Cheap candidate → self-hosted |
| `memory_consolidation` | (Phase 4.6 — kill-switch keyed `memory_consolidation`) | Gemini | background | Cheap candidate → self-hosted |
| `soul_file_coach` | `coach.py:169` | Gemini | warm | Possibly self-hosted Qwen-14B |
| `nudge_generation` | `nudge.py:86` | Gemini | background | Cheap candidate |
| `character_generator` | `character_generator.py:148, 190, 250, 286` | Gemini | background | 4 sub-prompts; one process name, multiple invocations |
| `wizard_simulation` | `wizard.py:202` | Gemini | background | 50-turn simulation loop |
| `recommendations` | `recommendations.py` | Gemini | background | Re-confirm in 25.3 audit |

**Embeddings** (`embeddings.py`) and **image generation** (Replicate) are out of scope — different request shapes, different cost models. Phase 25 is `/v1/chat/completions`-shaped processes only.

**Naming rule** (B1): snake_case, English-readable, process-not-method (we name what it *does for users*, not which function it's called from). Adding a new LLM call site = adding one row to the registry + one config row in `llm_process_config`.

---

## Decision 3 — Concurrency cap design

**Choice: per-provider `asyncio.Semaphore`, sized by registry config, acquired at the client boundary (just before HTTP send).**

**Why per-provider, not per-process or global:**

- **Global** breaks when one provider rate-limits us (Gemini incident 2026-05-30 — shared key hit 429s; if our limiter is global we'd starve unrelated providers too).
- **Per-process** doesn't model the real constraint — the real constraint is *the provider's rate limit*, which is shared across every process pointing at that provider. If `quality_scorer` + `memory_extraction` both point at Gemini, they share Gemini's quota whether we like it or not.
- **Per-provider** matches the actual rate-limit boundary. Each provider gets a semaphore sized to "max in-flight requests we're willing to have against this provider." Cheap to enforce, cheap to reason about.

**Default sizes** (configurable per provider in registry):

| Provider | Default semaphore | Reasoning |
|---|---|---|
| `gemini` | 20 | Per the 2026-05-30 incident retro: 20 concurrent is well under the shared-key burst limit |
| `openai` | 10 | Conservative; tier-1 OpenAI usually allows much more, but cost-breaker matters more than throughput |
| `openrouter` | 10 | Same |
| `together` | 10 | Same |
| `saikat_selfhosted` | 5 | Single GPU box; lower default until benchmarked |
| `vllm_local` | 5 | Same |
| `ollama` | 2 | Dev-only path; tight default |

**Implementation sketch:**

```python
# app/services/llm_registry.py
_provider_semaphores: dict[str, asyncio.Semaphore] = {}

def _semaphore(provider: str) -> asyncio.Semaphore:
    if provider not in _provider_semaphores:
        cap = _config_for_provider(provider).get("concurrency_cap", 10)
        _provider_semaphores[provider] = asyncio.Semaphore(cap)
    return _provider_semaphores[provider]

async def call(*, process: str, messages: list[dict], **kwargs) -> LlmResponse:
    cfg = _config_for_process(process)
    sem = _semaphore(cfg["provider"])
    async with sem:
        client = _client_for(cfg["provider"])
        return await client.complete(model=cfg["model"], messages=messages, **kwargs)
```

**Interaction with Phase 19 cost-breaker:**

- Semaphore is an **in-flight-count cap**, not a cost cap. Phase 19's per-user daily cost breaker stays where it is.
- Hot-edit path: admin changes `gemini` semaphore from 20 → 5. Existing in-flight requests finish; new ones block until count drops. No restart needed if we make the semaphore size mutable (option) or accept "next-launch" semantics (simpler — start with this).

**Observability:** semaphore.acquire wait time per process → Sentry breadcrumb + dashboard tile. If `quality_scorer` is regularly waiting >1s for a Gemini semaphore slot, that's the signal to either bump the cap or move `quality_scorer` to a cheaper provider.

---

## Scope of this PR

- ✅ This design doc (`docs/PHASE-25-DESIGN.md`)
- ✅ Empty scaffolding files: `app/services/llm_clients/__init__.py`, `app/services/llm_clients/openai_compatible.py` (signatures only), `app/services/llm_registry.py` (signatures only)
- ❌ No implementation code beyond the type signatures
- ❌ No DB schema change (the `llm_process_config` table comes in 25.2 after this PR merges)
- ❌ No wiring changes to existing call sites (that's 25.3)

**Approval gate:** Rishi reads this design doc → comments / approves the 3 decisions → I push the 25.1 + 25.2 implementation as commits on top of this same PR (or a follow-up PR — Rishi's call).

---

## Open questions (need Rishi's call before 25.1 implementation)

1. **Image generation** stays on Replicate — confirm or include in registry? Today Replicate is a separate code path; for symmetry it could become a "process" with provider=replicate. **Recommendation: leave outside the registry for now**, Phase 25 is `/v1/chat/completions`-shaped only. Image generation is a Phase 22+ concern.

2. **Audio transcription** stays Gemini-only — confirm? OpenAI Whisper-via-OpenAI-API is `/v1/audio/transcriptions`, a different endpoint shape. **Recommendation: leave Gemini-only**, route through registry as `audio_transcription` process with `gemini-native` provider, no openai_compatible fallback for now. Phase 25.x follow-up can add a `/v1/audio/transcriptions` client when needed.

3. **Where do new `llm_process_config` overrides live during local dev?** Default config is in `llm_registry.py` as a Python dict. Production overrides land in Postgres. For local dev, do we want env-var overrides (`LLM_PROCESS__QUALITY_SCORER=openrouter/qwen-14b`) or a JSON file? **Recommendation: env vars** (matches existing config pattern), keep it simple.

4. **Cost breaker per-provider vs cross-provider** — Phase 19.2 today caps per-user daily $ on Gemini. After Phase 25 the user could spread spend across providers. **Recommendation: keep per-user cap cross-provider** (total $ regardless of which provider served), add per-provider visibility on the dashboard.

5. **Gemini fallback when OpenAI-compatible fails** — if `quality_scorer` is configured for `openrouter/qwen-14b` and the call fails, do we fall back to Gemini? **Recommendation: NO automatic fallback** in 25.1. The whole point of Phase 25 is to control which provider serves which process; silent fallback to Gemini defeats that. Failures should surface as failures so the dashboard alerts.
