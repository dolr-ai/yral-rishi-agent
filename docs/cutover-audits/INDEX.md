# Overnight cutover-audits index — 2026-06-04 → 2026-06-05

Rishi: read this first. Each row links to the audit's TL;DR + recommendation.

## ⚠️ Escalations (read before the morning go/no-go meeting)

1. **DEV-11 — Latency 50% target NOT met.** chat-send is 9% faster on v2 (parity, not target); inbox-list is **2× SLOWER** (regression vs this morning — likely planner-stat staleness after the re-bootstrap landed +27,318 messages). **Cheap intervention:** run `ANALYZE conversations; ANALYZE messages;` on rishi-5 before the meeting + re-measure. See [`DEV-11-latency-comparison.md`](DEV-11-latency-comparison.md).

2. **DEV-3 — Billing paywall is intentionally client-side.** Architecture matches chat-ai (commit `7881e2e` from 2026-05-26). Acceptable for α (internal cohort). **NOT acceptable for β** — motivated user can bypass mobile gate by hitting v2 API directly. Track as a 21β blocker.

3. **DEV-1 — Push notifications port has 1 yellow item.** `data.type` field is `"chat_message"` in v2 vs `"new_message"` in chat-ai. Mobile expert should confirm what Android routes on; 1-line backend fix either way.

## Tier 1 (must-do)

| ID | Audit | Verdict | File |
|---|---|:---:|---|
| DEV-1 | 21α.C1 push notifications port audit | 🟡 | [DEV-1-push-notifications-port.md](DEV-1-push-notifications-port.md) |
| DEV-3 | 21α.C4 billing paywall verification | 🟡 | [DEV-3-billing-paywall.md](DEV-3-billing-paywall.md) |
| DEV-5 | 21α.B4 Langfuse traces verification | 🟢 | [DEV-5-langfuse-traces.md](DEV-5-langfuse-traces.md) |
| DEV-11 | 21α.B2 latency comparison N=100 | 🔴 | [DEV-11-latency-comparison.md](DEV-11-latency-comparison.md) |

## Tier 2 (done if time allowed — all 5 done)

| ID | Audit | Verdict | File |
|---|---|:---:|---|
| DEV-2 | 21α.C2 image gen via Replicate (live test 9s ✅) | 🟢 | [DEV-2-image-gen-replicate.md](DEV-2-image-gen-replicate.md) |
| DEV-4 | 21α.C5 Google Chat admin webhooks | 🟢 | [DEV-4-google-chat-webhooks.md](DEV-4-google-chat-webhooks.md) |
| DEV-6 | 21α.B5 Redis WS pub/sub | 🟢 | [DEV-6-redis-ws-pubsub.md](DEV-6-redis-ws-pubsub.md) |
| DEV-7 | 21α.S1 gitleaks (4 false-positive findings) | 🟢 | [DEV-7-gitleaks.md](DEV-7-gitleaks.md) · raw: [security/secret-scan-baseline-2026-06-05.md](../security/secret-scan-baseline-2026-06-05.md) |
| DEV-12 | 21α.B6 cost circuit breaker DRAFT PR #289 | 🟢 | [DEV-12-cost-circuit-breaker-draft.md](DEV-12-cost-circuit-breaker-draft.md) |

## Tier 3 (nice-to-have — partial completion)

| ID | Audit | Verdict | File |
|---|---|:---:|---|
| DEV-8 | 21α.S3 JWT extraction comparison | not-run | (skipped — context budget; v2 code already matches `feedback_jwt_signature_validation_with_shadow_rollout` design — should be fine but not formally diffed) |
| DEV-9 | 21α.S4 CORS + log redaction | not-run | (skipped — context budget; the CORS-allow-all line in `main.py:CORSMiddleware` is the obvious finding; redaction not spot-checked) |
| DEV-10 | 21α.S5 pip-audit (14 vulns, 3 pkgs) | 🟡 | [DEV-10-pip-audit.md](DEV-10-pip-audit.md) |

## Verdict summary

- 🟢 GREEN: DEV-2, DEV-4, DEV-5, DEV-6, DEV-7, DEV-12
- 🟡 YELLOW: DEV-1, DEV-3, DEV-10
- 🔴 RED: DEV-11 (latency 50% target, with cheap remediation queued)

**Net:** 6 green / 3 yellow / 1 red across 10 audits. The single red has a clear cheap remediation path. None of the yellows are 21α blockers; all are 21β concerns to track.

## What I did NOT touch (per the "no prod writes" rule)

- No kill-switch flips
- No `docker service update` of the running stack (the PR #284 max_tokens deploy happened pre-audit-queue per Rishi's separate authorization; everything since has been read-only inspection + draft-PR-only changes)
- No ETL loop enables
- No DB schema changes
- No Firebase Remote Config touches

## Cost ledger (LLM calls fired during audits)

- DEV-2 image gen: 1 Replicate Flux Dev call ≈ $0.003
- DEV-11 latency: 200 chat-send + 200 inbox-list + 200 inf-list = ~200 LLM-bound calls on Gemini Flash ≈ $0.02-0.05

**Total: well under $0.50 budget cap.**

## Files of interest

- DRAFT PR #289: `feat/phase-19-2-per-user-cost-breaker-DRAFT` (cost breaker)
- Latency raw script output: [`DEV-11-latency-raw.md`](DEV-11-latency-raw.md)
- gitleaks security baseline: [`../security/secret-scan-baseline-2026-06-05.md`](../security/secret-scan-baseline-2026-06-05.md)
