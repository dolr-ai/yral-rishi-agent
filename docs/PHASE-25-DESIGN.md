# Phase 25 — Multi-provider LLM architecture: design + 25.1/25.2 implementation

Status: **APPROVED 2026-06-02 by Rishi + 25.1/25.2 IMPLEMENTED in this PR.** Design ratification + scaffolding + actual client + registry code all land together (per Rishi 2026-06-02: "one PR, one decision moment, one merge" beats the separate-design-PR pattern).

This document is the source of truth for the 3 decisions that have to be locked before any of 25.1–25.9 ships.

## ⭐ Approval summary (2026-06-02)

- **Decision 1 (in-house):** ✅ approved as-is.
- **Decision 2 (process names):** ✅ approved with 2 renames — see "Renames 2026-06-02" below.
- **Decision 3 (per-provider concurrency cap):** ✅ approved as-is. Caps: gemini=20, openai/openrouter/together=10, internal_vllm=5, ollama=2.
- **Q1 (image gen in registry):** NO — keep out.
- **Q2 (audio transcription):** YES — route via registry, Gemini-only for now.
- **Q3 (local-dev override):** env vars in the form `LLM_PROCESS__<NAME>=<provider>/<model>`.
- **Q4 (cost-breaker scope):** per-user **cross-provider**, plus a new `cost_basis` column on `llm_costs` (`real` / `synthetic`). `internal_vllm` logs synthetic per-token cost (suggested **$0.00005/1k tokens**) representing compute share, NOT real money. Dashboard separately reports "real $ spent" vs "compute share consumed." See "Cost basis" below.
- **Q5 (self-hosted failure):** new retry ladder — see "Failure handling: retry ladder + Gemini fallback" below.

**ADHD-friendly editability rule (REINFORCED):** every cap, every provider mapping, every concurrency limit, every retry count, every fallback cap MUST be exposed on the Phase 19.6 + 25.9 admin dashboard with two-click Edit affordance. **No knob ships buried in env vars only.** Per [[feedback-adhd-observability-and-security-baseline]] memory.

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
| `ai_influencer_wizard_simulation` | `wizard.py:202` | Gemini | background | 50-turn simulation loop. Renamed 2026-06-02 to disambiguate from any future generic-wizard process |
| `soul_file_recommendations` | `recommendations.py` | Gemini | background | Renamed 2026-06-02 to make ownership explicit — this is the soul-file recommender, not a generic one |

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
| `internal_vllm` | 5 → 10–20 after our own benchmark | See "Self-hosted vLLM" section below. Anshuman's synthetic test hit 0 rate-limit errors at 90 concurrent; we start conservative |
| `ollama` | 2 | Dev-only path; tight default |

### Self-hosted vLLM (`internal_vllm`)

Anshuman shipped the self-hosted endpoint 2026-06-02. Saikat owns the GPU/hosting infra; Anshuman wrote the serving stack. Provider name is **`internal_vllm`** (person-neutral — owners rotate, the stack persists; named after the technology). Credit lives in the GLOSSARY entry + this design doc, not in the key.

| Field | Value |
|---|---|
| Provider key | `internal_vllm` |
| base_url | `https://model.ansuman.yral.com/v1` |
| Default model | `Qwen/Qwen3.6-27B-FP8` |
| Streaming | Supported (`stream=True`) |
| API-key Swarm secret | `INTERNAL_VLLM_API_KEY` (mounted file-first via `/run/secrets/INTERNAL_VLLM_API_KEY`, env fallback per existing pattern) |
| Required extra-body | `{"chat_template_kwargs": {"enable_thinking": false}}` — Qwen 3.6's "thinking mode" produces verbose CoT prefixes we don't want for chat workloads. OFF by default for every process that points at this provider |
| Concurrency cap (initial) | 5 |
| Concurrency cap (post-benchmark target) | 10–20 |
| Anshuman benchmark (synthetic) | 92.57 tok/s decode throughput, 0 rate-limit errors at 90 concurrent |
| Reference gist | https://gist.github.com/ansuman-yral/f20f7b2cd794ed6fec2a50eb75e262ea |

**Why start at 5 even though 90 worked synthetic:** Real chat workloads have bursty long-tails. Production memory/quality_scorer batches can fire 50+ requests in a 1s window when migrations run. We want the first failure mode to be "wait in the semaphore queue" not "crash Anshuman's GPU box." Once Phase 25.7 (integration test) shows real-world stable load behavior, bump in `llm_process_config` table — no redeploy.

**Implementation note for 25.1:** the `openai_compatible` client needs to thread `extra_body` through to the underlying HTTP request body. Per OpenAI spec, fields outside the standard set are silently accepted by every implementation we've tested (OpenAI ignores unknown keys, OpenRouter passes them through, vLLM consumes `chat_template_kwargs`). One per-provider config knob in the registry handles this without conditional logic in the client.

**Wire-format quirks from Anshuman's reference gist** (folded in 2026-06-02):

1. **Streaming + usage in one call.** Pass `stream=True, stream_options={"include_usage": True}`. The `usage` object (prompt_tokens, completion_tokens) arrives in the **last chunk** alongside `chunk.choices=[]`. The 25.1 streaming client must tolerate "chunk has usage but no content" and "chunk has content but no usage." We currently extract usage from a single non-streaming response; the streaming path needs a different extraction point at end-of-stream.

2. **Per-process timeout, not a global default.** Anshuman uses `timeout=300.0` for long story-writing. Our workload is mixed:
   - `user_chat_main`: 30–60s (fail-fast — user is waiting)
   - `quality_scorer` / `memory_extraction` / `memory_consolidation`: 120–180s (background, tolerates slowness)
   - `wizard_simulation` / `character_generator`: 180–300s (long generation, no user blocking)

   Registry config schema gains a `timeout_sec` field per process. Default 60s if unset.

3. **Thinking-mode disable** — already captured in the table above. Confirmed against the gist.

4. **Re-benchmark in 25.7.** Anshuman's 92.57 tok/s + 0 rate-limit at 90 concurrent was on **synthetic story-writing prompts (~1000 tokens each)**. Our real chat workload is shorter (200–400 token replies) so:
   - Throughput should be similar (decode-bound)
   - TTFT (time-to-first-token) likely **lower** for us (smaller prompts → less prefill)

   Phase 25.7 integration test re-benchmarks against our actual prompt shape before raising the concurrency cap from 5 to 10–20.

**API-key handoff plan:** Rishi will paste `INTERNAL_VLLM_API_KEY` to me SSH-side when Phase 25.7 (integration test) is ready to run, NOT before. Today's PR only encodes the secret name; no key material in the design doc, the registry, or any committed file.

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

## Failure handling — retry ladder + Gemini fallback (Q5 expanded 2026-06-02)

Originally Q5 recommended "no automatic fallback." Rishi expanded this to a bounded retry-then-fallback ladder that preserves the registry's intent (control which provider serves which process) while not letting transient self-hosted failures break user-facing chat.

**Per-request flow when the registered provider call fails:**

1. **Retry on same provider** — N retries, default **3**, exponential backoff with jitter (200ms → 400ms → 800ms ± 50ms jitter). Standard pattern; lives in the openai_compatible client (or gemini client, symmetric).

2. **Webhook alert on retry exhaustion** — POST to a Google Chat webhook so we *see* this happening in real time. Webhook URL lives in Swarm secret `LLM_FALLBACK_ALERT_WEBHOOK`. Payload: `{process, provider, model, error, attempted_at, user_id_if_user_facing}`. Rishi will provide the URL when ready; design encodes the secret name today, the wiring lands in 25.1.

3. **Fallback to Gemini for *that one request*** — drops back to `gemini-2.0-flash` (or whatever the user-chat default is) so the user gets a reply. Per-request, not per-state: the registry config for the process is **not** mutated. Once the registered provider recovers, the next request goes to it normally.

4. **Bounded fallback cap** — a separate "fallback budget" cap, default **$5/day shared across all users**, tracked in `llm_costs` with `cost_basis='real'` and a special `is_fallback=true` flag. Prevents the abuse pattern "deliberately trigger fallback to drain paid Gemini quota." Once the fallback cap is hit, fallback turns OFF for the rest of the UTC day; requests with no fallback either succeed-on-retry or surface a 5xx to the caller.

5. **Auto-resume** — no manual reset needed. Fallback is per-request; once internal_vllm is healthy again, requests stop falling back automatically.

**ADHD-editability tie-in:** retry count, retry-backoff base, webhook URL, fallback cap $/day are all knobs on the 19.6 + 25.9 admin dashboard. Two-click edit.

**Why not "fall back forever until manually reset":** state-based fallback hides the underlying problem — internal_vllm could be down for hours and we wouldn't notice unless Gemini bill spikes. Per-request fallback + webhook makes the failure loud while keeping users served.

---

## Cost basis — real $ vs synthetic compute share (Q4 expanded 2026-06-02)

The Phase 19 cost breaker exists to protect against **real $** burn. After Phase 25, traffic spreads across providers including `internal_vllm` (free in dollar terms — we own the GPU). A naïve per-user $/day cap that only counts real $ becomes increasingly meaningless as we route more traffic to the self-hosted endpoint.

**Solution:** new column `cost_basis` on `llm_costs` table with values `real` or `synthetic`.

- **Real:** Gemini, OpenAI, OpenRouter, Together — actual per-token pricing from the provider's published rates. Phase 19.2's per-user cap counts these.
- **Synthetic:** internal_vllm — compute-share pricing at a suggested **$0.00005/1k tokens** (configurable per provider in registry). Represents "this user consumed X amount of shared GPU." Phase 19.2's per-user cap counts these too — so a user hammering internal_vllm hits the per-user daily cap eventually, but at a much higher token volume than they would if they were hammering Gemini.

**Dashboard separates the two reports:**

- Tile 1: "Real $ spent today" (sum where cost_basis=real). This is what we pay vendors.
- Tile 2: "Compute share consumed today" (sum where cost_basis=synthetic, denominated in the same $-equivalent so the math composes).
- Tile 3 (existing): "Per-user $/day" cap status across both — the protective cap.

**Why synthetic instead of zero?**

If internal_vllm logs zero cost, the per-user breaker becomes a no-op for self-hosted traffic. A user could chat infinitely against internal_vllm with no protection — that becomes a GPU DoS vector. Synthetic pricing makes the cap mean "you've consumed enough compute resources that we want you to slow down regardless of who paid."

**Why $0.00005/1k tokens specifically?**

Roughly 20x cheaper than Gemini Flash's real rate (~$0.001/1k input tokens). Reflects that we *do* spend on GPU+power; we just don't pay per token. Tunable in registry — adjust as the GPU bill firms up.

---

## Scope of this PR

- ✅ This design doc (`docs/PHASE-25-DESIGN.md`)
- ✅ Empty scaffolding files: `app/services/llm_clients/__init__.py`, `app/services/llm_clients/openai_compatible.py` (signatures only), `app/services/llm_registry.py` (signatures only)
- ❌ No implementation code beyond the type signatures
- ❌ No DB schema change (the `llm_process_config` table comes in 25.2 after this PR merges)
- ❌ No wiring changes to existing call sites (that's 25.3)

**Approval gate:** Rishi reads this design doc → comments / approves the 3 decisions → I push the 25.1 + 25.2 implementation as commits on top of this same PR (or a follow-up PR — Rishi's call).

---

## Open questions — RESOLVED 2026-06-02

| # | Question | Resolution |
|---|---|---|
| Q1 | Image generation in registry? | **NO** — keep on Replicate, out of the registry. Phase 25 is `/v1/chat/completions`-shaped only. |
| Q2 | Audio transcription routing? | **YES via registry**, Gemini-only for now. Process name: `audio_transcription` with `gemini-native` provider. A `/v1/audio/transcriptions` client lands as a Phase 25.x follow-up if/when needed. |
| Q3 | Local-dev override mechanism? | **Env vars**: `LLM_PROCESS__<UPPER_PROCESS_NAME>=<provider>/<model>`. Matches existing config pattern. Example: `LLM_PROCESS__QUALITY_SCORER=openrouter/qwen-14b`. |
| Q4 | Cost breaker per-provider vs cross-provider? | **Cross-provider per-user**, with new `cost_basis` column (`real` / `synthetic`). See "Cost basis" section above for full design. |
| Q5 | Auto-fallback to Gemini? | **YES with bounded retry ladder.** 3 retries on same provider → Google Chat webhook alert → per-request Gemini fallback → fallback budget cap ($5/day shared default) → auto-resume. See "Failure handling: retry ladder" section above. |
