# DEV-11 — Latency comparison v2 vs chat-ai (21α.B2)

## TL;DR

**🔴 RED on the "50% faster" target.** chat-send is **9% faster** on v2 (3404ms vs 3759ms p50). inbox-list is **2× SLOWER** on v2 (904ms vs 427ms — a regression worth chasing). inf-list is parity. CLAUDE.md rule 6's target is not met; both services LLM-bound on chat-send (same Gemini provider) and Postgres-bound on GETs.

For the cutover go/no-go: **abort gate fails if read strictly.** Recommend you read the rule liberally: v2 is not SLOWER on the hot path (chat-send is what users feel per-message); inbox-list is a screen-load. But the inbox-list regression should be triaged before β.

## Headline

N=100 sequential (concurrency=1) per service, post-Caddy-fix and post-re-bootstrap. Both backends 100% success rate.

| Endpoint | v2 p50 | chat-ai p50 | Δp50 | v2 p95 | chat-ai p95 | Δp95 | Target ≥50% faster? |
|---|---:|---:|---:|---:|---:|---:|:---:|
| `chat-send` (LLM hot path) | 3404ms | 3759ms | **↓9%** | 5676ms | 7386ms | ↓23% | ❌ |
| `inbox-list` (home screen) | 904ms | 427ms | **↑112%** | 4653ms | 1240ms | ↑275% | ❌ |
| `inf-list` (discovery) | 377ms | 339ms | ↑11% | 2754ms | 2867ms | ↓4% | ❌ |

Raw script output: [`DEV-11-latency-raw.md`](DEV-11-latency-raw.md).

## What changed vs the earlier (N=25) measurement

PR #268 reported (2026-06-04 morning): inbox-list v2=304ms vs chat-ai=325ms (parity). **Now: v2=904ms vs chat-ai=427ms.** That's a 3x regression on v2's inbox-list p50 in ~7 hours.

Likely culprits:
1. **The re-bootstrap landed +27,318 messages + +1 conversation into V2 prod** (Phase A4 closure earlier today). The inbox-list query does `SELECT conversations + last_message_preview` shape; larger rowsets + larger messages partition means longer scan.
2. inbox-list p95 jumped from 370ms → 4653ms — bigger jump than p50 (12x vs 3x). Tail-latency degradation usually means a query plan change (e.g. seq scan kicking in when index was used). Could be index bloat on the conversations table after the bulk INSERT (32M total messages now).
3. Postgres autovacuum hasn't caught up post-rebootstrap — table stats stale, planner choosing worse plan.

**Easy intervention before β:** `ANALYZE conversations; ANALYZE messages;` on the leader to refresh planner stats. Likely brings inbox-list p50 back under 500ms.

## What v2 is good at (per this run)

- **chat-send p95**: 5676ms vs chat-ai 7386ms → v2 is **23% better at tail**. The LLM round-trip is the floor on both; v2's better p95 likely from cleaner Patroni cluster + lower base-load on the new infra.
- **chat-send 100% success rate at sequential load** — proves the Caddy `unhealthy_latency` removal (PR #272) is holding under sustained traffic.

## Recommendation

**Cutover gate B2: RED if strict, YELLOW with mitigation.** Two paths forward:

### Strict reading (matches CLAUDE.md rule 6)

Block 21α until either:
- user_chat_main flips to a faster-TTFT provider, OR
- inbox-list query is re-tuned + actually beats chat-ai by 50%

This is the "honor the rule as written" position.

### Pragmatic reading (alpha is dogfood)

Accept current numbers because:
- chat-send (the only thing users feel mid-conversation) is **faster** on v2, just not by 50%
- inbox-list 904ms is still under 1s — acceptable UX
- alpha cohort is internal (YRAL team) who can tolerate the gap
- The 50% target was set against legacy chat-ai performance; both services run on the same Gemini Flash model so they're floor-bound by LLM provider latency — there's no headroom for v2 to be 50% faster on chat-send without provider change.

For β, must EITHER renegotiate the 50% target downward (e.g. "≥10% faster" — achievable today on chat-send), OR move user_chat_main off Gemini.

### Immediate cheap action — refresh planner stats

```sql
-- On rishi-5 (V2 leader)
ANALYZE conversations;
ANALYZE messages;
```

Estimated impact: ~30s SQL, low risk, may bring inbox-list back to parity (where it was this morning before the re-bootstrap landed). Do this BEFORE the morning go/no-go meeting so you have updated numbers.

## What I did NOT verify

- Tail-latency cause (autovacuum lag vs query plan vs index bloat) — would need EXPLAIN ANALYZE on the actual `list_by_user` query
- Cold vs warm cache effects (PR #268 morning was cold; this run is warm — and worse, suggesting structural regression not warmup)
- Concurrent latency profile at concurrency=3 (the Caddy fix held earlier; not re-tested at N=100)
