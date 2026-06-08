# Overnight cutover-audits index — 2026-06-04 → 2026-06-05

Rishi: read this first. Each row links to the audit's TL;DR + recommendation.

**12 of 12 audits closed** (10 tier-1/2 + 2 add-ons: DEV-11b root-cause, DEV-12-prep PR review notes).

## ⚠️ Escalations (read before the morning go/no-go meeting)

1. **DEV-11 + DEV-11b — Latency target NOT met. Root cause: stale planner stats.** `pg_stat_user_tables` shows `conversations.n_live_tup=2` (reality: 286k) and all `last_analyze` columns NULL — stats reset during yesterday's WAL-G failover. **Action:** authorize `ANALYZE conversations; ANALYZE messages; ANALYZE ai_influencers;` (≤1 min total, ACCESS SHARE lock, very low risk) then re-measure. Expected inbox-list p50 drops 904ms → ~300-400ms (at/under chat-ai parity). Full EXPLAIN ANALYZE in [`DEV-11b-latency-root-cause.md`](DEV-11b-latency-root-cause.md). chat-send 50% target stays unachievable without provider change — Gemini round-trip is the floor on both backends.

2. **DEV-3 — Billing paywall is intentionally client-side.** Architecture matches chat-ai (commit `7881e2e` from 2026-05-26). Acceptable for α (internal cohort). **NOT acceptable for β** — motivated user can bypass mobile gate by hitting v2 API directly. Track as a 21β blocker.

3. **DEV-1 — Push notifications port has 1 yellow item.** `data.type` field is `"chat_message"` in v2 vs `"new_message"` in chat-ai. Mobile expert should confirm what Android routes on; 1-line backend fix either way.

## Pre-drafted: PR #289 review

[`DEV-12-PR-289-prep.md`](DEV-12-PR-289-prep.md) has a starting-position answer for each of the 4 review questions in PR #289 (cost circuit breaker DRAFT). 5-min read; walk into the review with positions, not blanks.

## Tier 1 (must-do)

| ID | Audit | Verdict | File |
|---|---|:---:|---|
| DEV-1 | 21α.C1 push notifications port audit | 🟡 | [DEV-1-push-notifications-port.md](DEV-1-push-notifications-port.md) |
| DEV-3 | 21α.C4 billing paywall verification | 🟡 | [DEV-3-billing-paywall.md](DEV-3-billing-paywall.md) |
| DEV-5 | 21α.B4 Langfuse traces verification | 🟢 | [DEV-5-langfuse-traces.md](DEV-5-langfuse-traces.md) |
| DEV-11 | 21α.B2 latency comparison N=100 | 🔴 | [DEV-11-latency-comparison.md](DEV-11-latency-comparison.md) |

## Tier 2 (all 5 done)

| ID | Audit | Verdict | File |
|---|---|:---:|---|
| DEV-2 | 21α.C2 image gen via Replicate (live test 9s ✅) | 🟢 | [DEV-2-image-gen-replicate.md](DEV-2-image-gen-replicate.md) |
| DEV-4 | 21α.C5 Google Chat admin webhooks | 🟢 | [DEV-4-google-chat-webhooks.md](DEV-4-google-chat-webhooks.md) |
| DEV-6 | 21α.B5 Redis WS pub/sub | 🟢 | [DEV-6-redis-ws-pubsub.md](DEV-6-redis-ws-pubsub.md) |
| DEV-7 | 21α.S1 gitleaks (4 false-positive findings) | 🟢 | [DEV-7-gitleaks.md](DEV-7-gitleaks.md) · raw: [security/secret-scan-baseline-2026-06-05.md](../security/secret-scan-baseline-2026-06-05.md) |
| DEV-12 | 21α.B6 cost circuit breaker DRAFT PR #289 | 🟢 | [DEV-12-cost-circuit-breaker-draft.md](DEV-12-cost-circuit-breaker-draft.md) |

## Tier 3 (all 3 closed in add-on pack)

| ID | Audit | Verdict | File |
|---|---|:---:|---|
| DEV-8 | 21α.S3 JWT extraction comparison | 🟢 | [DEV-8-jwt-comparison.md](DEV-8-jwt-comparison.md) |
| DEV-9 | 21α.S4 CORS + log redaction | 🟢 | [DEV-9-cors-log-redaction.md](DEV-9-cors-log-redaction.md) |
| DEV-10 | 21α.S5 pip-audit (14 vulns, 3 pkgs) | 🟡 | [DEV-10-pip-audit.md](DEV-10-pip-audit.md) |

## Add-on pack (deeper-dive supporting docs)

| ID | Audit | Verdict | File |
|---|---|:---:|---|
| DEV-11b | latency root cause (EXPLAIN ANALYZE + pg_stat_user_tables) | 🔴→🟢 with ANALYZE | [DEV-11b-latency-root-cause.md](DEV-11b-latency-root-cause.md) |
| DEV-12-prep | pre-drafted answers to PR #289's 4 questions | n/a | [DEV-12-PR-289-prep.md](DEV-12-PR-289-prep.md) |

## Morning-of pack (2026-06-08)

| ID | Audit | Verdict | File |
|---|---|:---:|---|
| G | ETL backlog measurement (chat-ai vs v2 row counts) | 🟡 | [G-etl-backlog-measurement.md](G-etl-backlog-measurement.md) |
| H | push-notif `data.type` fix — local commit, NOT pushed | held | branch `fix/push-notif-data-type-parity-with-chat-ai` (1 commit, awaits mobile confirm) |

## Verdict summary

- 🟢 GREEN: DEV-2, DEV-4, DEV-5, DEV-6, DEV-7, DEV-8, DEV-9, DEV-12 (8)
- 🟡 YELLOW: DEV-1, DEV-3, DEV-10 (3)
- 🔴 RED: DEV-11 (1, with DEV-11b root-cause + cheap fix queued)

**Net:** 8 green / 3 yellow / 1 red across 12 audits. The single red has its root cause diagnosed (stale stats) + a clear cheap fix (ANALYZE).

## What I did NOT touch (per "no prod writes" rule)

Zero kill-switch flips, zero `docker service update` of the running stack, zero ETL enables, zero schema changes, zero Firebase touches, zero ANALYZE.

## Cost ledger (LLM calls fired during audits)

- DEV-2 image gen: 1 Replicate Flux Dev call ≈ $0.003
- DEV-11 latency: ~200 Gemini Flash calls ≈ $0.02-0.05
- **Total: well under $0.50 budget cap.**

## Files of interest

- DRAFT PR #289: `feat/phase-19-2-per-user-cost-breaker-DRAFT` (cost breaker) — review questions pre-drafted in DEV-12-PR-289-prep.md
- Latency raw script output: [`DEV-11-latency-raw.md`](DEV-11-latency-raw.md)
- gitleaks security baseline: [`../security/secret-scan-baseline-2026-06-05.md`](../security/secret-scan-baseline-2026-06-05.md)

## Tomorrow's first-thing-in-the-morning checklist

1. Run `ANALYZE conversations; ANALYZE messages; ANALYZE ai_influencers; ANALYZE bot_quality_scores; ANALYZE coach_conversations; ANALYZE coach_messages; ANALYZE user_memories; ANALYZE user_skill_state;` on rishi-5 (V2 leader). ≤2 min wall, ACCESS SHARE lock only.
2. Re-run `python3 scripts/latency_comparison_phase_1_17.py --n 100 --concurrency 1` and confirm inbox-list back near or under chat-ai parity. ~3-4 min.
3. Mobile expert confirms `data.type` value for Android push routing → decide on the DEV-1 1-line fix. **H branch `fix/push-notif-data-type-parity-with-chat-ai` is pre-committed locally** (one commit, not pushed); if answer is "new_message" → `git push -u origin fix/push-notif-data-type-parity-with-chat-ai && gh pr create`. If answer is "no change" → `git branch -D` and move on.
4. Review PR #289 with DEV-12-prep notes in hand. ~10 min including answering the 4 questions.
5. Go/no-go on 21α with updated latency data + the 3 yellows acknowledged + DEV-3 tracked as β blocker.
