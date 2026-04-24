# Latency Baseline Capture — the numbers we promise to beat

**Status: empty placeholder** until Phase 0 baseline capture runs.

Per CONSTRAINTS.md row E1, v2 must meet or beat the latency of BOTH Ravi's Rust `yral-ai-chat` AND our Python `yral-chat-ai` at every percentile (p50, p95, p99, p99.9) at every rollout step. To measure, we first capture the baseline.

## What we capture

For each endpoint the mobile client hits:
- p50, p95, p99, p99.9 request-completion latency (end-to-end user-perceived)
- p50, p95, p99 time-to-first-byte latency (for SSE streaming on v2; for the non-streaming baseline, same as completion)
- Error rate (4xx, 5xx) over a 1-week window
- Request volume (to weight the baseline appropriately)

## The endpoints

From `feature-parity-with-existing-chat-services-audit/feature-parity-audit.md`:

Priority endpoints (captured first):
1. `POST /api/v1/chat/conversations/{id}/messages` — THE hot path; users perceive this most
2. `GET /api/v1/chat/conversations` — inbox listing
3. `GET /api/v1/chat/conversations/{id}/messages` — message history
4. `POST /api/v1/chat/conversations/{id}/images` — image generation
5. `WS /api/v1/chat/ws/inbox/{user_id}` — WebSocket inbox

Secondary (captured second):
6. `GET /api/v1/influencers` — list
7. `GET /api/v1/influencers/trending`
8. `POST /api/v1/influencers/create` + the 3-step flow
9. `PATCH /api/v1/influencers/{id}/system-prompt` — creator edit

## How we capture

Two sources:
1. **Sentry performance tab** (`apm.yral.com` AND `sentry.rishi.yral.com`) — has per-endpoint p50/p95/p99 built in. Pull via Sentry API for 7-day window; export CSV.
2. **Langfuse** (once v2's Langfuse is up) — only captures LLM-call latency, not full endpoint. But LLM-call is the dominant factor. Use for validating Sentry numbers.

## Deliverable

`latency-baselines.md` — a committed file in the v2 template repo, read by the CI latency gate on every PR.

Format:
```markdown
# Latency Baselines — v2 must beat these

Captured: 2026-0X-XX through 2026-0X-XX (7-day window)
Source: Sentry (`sentry.rishi.yral.com`) + Langfuse
Scope: production traffic from mobile users

| Endpoint | Method | p50 | p95 | p99 | p99.9 | Error rate | Weekly requests |
|---|---|---|---|---|---|---|---|
| /api/v1/chat/conversations/{id}/messages | POST | 420ms | 980ms | 1.8s | 3.2s | 0.4% | 850k |
| /api/v1/chat/conversations | GET | 35ms | 110ms | 280ms | 510ms | 0.1% | 2.1M |
| ... | ... | ... | ... | ... | ... | ... | ... |
```

## CI latency gate behavior

For every v2 service PR:
1. Run smoke-test load against staging — 500 requests at various traffic mix
2. Compare measured p95 against baseline
3. If v2 p95 > baseline.p95 × 1.0 (i.e., slower) → block merge, comment on PR with the regressed endpoints
4. If v2 p95 ≤ baseline.p95 × 0.8 (i.e., 20% faster or better) → approve with celebration comment

## What this protects

If a "clever optimization" actually slows us down, the PR is blocked. If an architectural choice bakes in a latency problem, we catch it before users see it. If a regression slips into main, the hourly production check pages Rishi.

## When to capture

Phase 0, BEFORE any v2 service is written. Capture happens once, updates quarterly to account for traffic shape changes.
