# Phase 1.17 — v2 vs chat-ai latency comparison

**Date:** 2026-06-04
**Script:** `scripts/latency_comparison_phase_1_17.py`
**v2 image:** `76858dfa` (PR #267, current main)
**chat-ai:** baseline production
**Method:** 25 samples per endpoint per backend, back-to-back. Same JWT scheme on both (verify_signature=false). Same shared influencer (Sodha Iqbal Kasam UUID exists on both).

---

## CLAUDE.md target

> 50% faster than chat-ai on user-facing endpoints.

## Headline — target NOT met today (sequential, concurrency=1)

| Endpoint | v2 p50 | chat-ai p50 | Δp50 | v2 p95 | chat-ai p95 | Δp95 | Target ≥50% faster? |
|---|---:|---:|---:|---:|---:|---:|:---:|
| `chat-send` (LLM hot path) | 3038ms | 2718ms | ↑12% | 4206ms | 5070ms | ↓17% | ❌ |
| `inbox-list` (home screen) | 304ms | 325ms | ↓6% | 370ms | 410ms | ↓10% | ❌ |
| `inf-list` (discovery) | 411ms | 356ms | ↑15% | 509ms | 454ms | ↑12% | ❌ |

Numbers above are sequential (concurrency=1) — see **Section 2** for the concurrent-load finding which is a separate, more serious issue.

## Why no 50% gap

The chat-send hot path is **LLM-bound**: ~2.5-3s is the Gemini round-trip itself, and both v2 and chat-ai route user-facing chat to Gemini today (see `app/services/llm_registry.py:LLM_DEFAULTS["user_chat_main"]`). Until/unless we move user_chat_main to a faster-TTFT provider, latency parity is the expected ceiling. Internal_vllm is the candidate but its under-load TTFT disqualified it for user_chat_main earlier (per the Phase 25.4 notes).

The two GET endpoints (`inbox-list`, `inf-list`) are Postgres-backed on both sides — also no structural reason v2 would dramatically outpace chat-ai. The 12-15% delta on `inf-list` against v2 likely comes from extra columns we now select (post-23.5 added `skill_slug`, plus the existing `metadata` + `personality_traits` JSON blobs).

---

## Section 2 — Concurrent-load failure on v2 chat-send (BLOCKER)

Same script with **concurrency=3** (3 different users hitting chat-send in parallel) — N=25:

| Endpoint | v2 | chat-ai |
|---|---:|---:|
| chat-send success rate | **9 / 25** | 25 / 25 |
| chat-send status mix   | `{200: 9, 503: 16}` | `{200: 25}` |
| inbox-list             | 25 / 25 | 25 / 25 |
| inf-list               | 25 / 25 | 25 / 25 |

**64% of chat-send requests returned 503 with empty body** under 3-parallel-user load. A separate 20-call burst test reproduced: 9/20 = 45% 503. Both `POST /api/v1/chat/conversations` (the create) and `POST .../messages` (the send) failed with the same empty-body 503 — the signature of a Caddy upstream-unavailable response, NOT an app-level error.

Service logs during the burst showed only asyncio "Task was destroyed but it is pending!" warnings (background tasks cancelled when the client closes), no exceptions tied to the 503 path. Rate limiter is innocent — it returns 429, not 503.

### Hypothesis (NOT yet investigated)

Three candidates, ordered by likelihood:

1. **Caddy upstream pool / health-mark behavior** — Caddy on rishi-1/2 may mark an upstream replica as unhealthy when it sees N consecutive failures within a window. Under 3 concurrent slow LLM calls (each ~3s), the dial-attempt pool can be saturated briefly. Each 503 has empty body — the canonical Caddy "no healthy upstream" response.
2. **uvicorn single-worker per replica** — if both replicas (`.1` on rishi-5 and `.2`) are running uvicorn with `--workers 1`, then concurrent LLM-bound requests within a worker block subsequent requests' event-loop ticks long enough that Caddy times out the dial.
3. **HTTP/2 stream cap on Caddy↔upstream** — if Caddy uses HTTP/1.1 per-connection serially to the upstream and the connection pool is too small, in-flight LLM requests stall later TCP-level dials.

### Why this matters BEFORE cutover

Production mobile traffic is not load-tested by 1 user at a time. Even modest real-traffic concurrency (which is normal — multiple users typing simultaneously) will hit the same upstream-pool path. **This MUST be diagnosed and fixed before cutover** — a 64% 503 rate at concurrency=3 means visible production breakage immediately.

### Recommended diagnostic plan

1. Inspect Caddy site config on rishi-1/2 for `reverse_proxy { lb_policy ... health_uri ... }` and pool sizing.
2. Check uvicorn invocation in the agent's Dockerfile / CMD — `--workers N` vs single-worker async.
3. Repeat the burst test from a rishi-host (bypass Caddy) directly against the service port — if no 503s there, problem is Caddy ↔ upstream pool.
4. Compare to chat-ai's Caddy config — same fronting, but chat-ai succeeded 25/25 at concurrency=3.

---

## Per-endpoint detail (sequential baseline, concurrency=1, N=25)

### `chat-send` — POST `/api/v1/chat/conversations/{id}/messages`

| Stat | v2 | chat-ai |
|---|---:|---:|
| n (success) | 25 | 25 |
| p50 ms | 3038 | 2718 |
| p95 ms | 4206 | 5070 |
| p99 ms | 4544 | 6513 |
| mean ms | 3148 | 3047 |
| status mix | `{200: 25}` | `{200: 25}` |

### `inbox-list` — GET `/api/v1/chat/conversations`

| Stat | v2 | chat-ai |
|---|---:|---:|
| n (success) | 25 | 25 |
| p50 ms | 304 | 325 |
| p95 ms | 370 | 410 |
| p99 ms | 1264 | 1173 |
| mean ms | 365 | 385 |

### `inf-list` — GET `/api/v1/influencers`

| Stat | v2 | chat-ai |
|---|---:|---:|
| n (success) | 25 | 25 |
| p50 ms | 411 | 356 |
| p95 ms | 509 | 454 |
| p99 ms | 1274 | 814 |
| mean ms | 462 | 397 |

---

## Two distinct findings for the cutover decision

1. **Sequential latency = parity, not 50% faster.** Achievable IF we move `user_chat_main` to a lower-TTFT provider (internal_vllm pending its under-load fix). Both GETs are essentially equal — no obvious win pre-cutover from these endpoints.
2. **Concurrent chat-send fails 64% at modest load.** Blocker. Has to be root-caused before cutover OR mobile traffic will see a wall of 503s on real-world bursts.

## Reproducibility

```bash
# Sequential baseline
python3 scripts/latency_comparison_phase_1_17.py --n 25 --concurrency 1 \
    --report docs/PHASE-1-17-LATENCY-COMPARISON-$(date -u +%Y-%m-%d)-sequential.md

# Concurrent stress (surfaces the 503 issue)
python3 scripts/latency_comparison_phase_1_17.py --n 25 --concurrency 3 \
    --report docs/PHASE-1-17-LATENCY-COMPARISON-$(date -u +%Y-%m-%d)-concurrent.md
```

Requires `httpx`. JWT scheme matches `scripts/eval_v2_vs_chat_ai.py` (verify_signature=false on both services).
