# Testing Strategy — Risk-Weighted (Option E, locked 2026-04-29)

> **Designed for:** non-programmer + ADHD reader. Tests catch bugs you (Rishi) wouldn't notice in working memory; they don't replace your Motorola check.

## Core principle

**Test investment scales with risk.** Heavy testing where bugs hurt (auth, billing, safety, data correctness). Light testing where bugs are cosmetic. AI agents write the tests; Codex reviews them; you read 1-line summaries.

## The risk-weighted coverage map

```
   ╔═══════════════════════════════════════════════════════════════╗
   ║  HOT PATH — 70-80% coverage target                              ║
   ║  ──────────────────────                                         ║
   ║  • yral-rishi-agent-public-api          (auth, billing check)   ║
   ║  • yral-rishi-agent-conversation-turn-orchestrator (the brain)  ║
   ║  • yral-rishi-agent-content-safety-and-moderation (regulatory)  ║
   ║  • yral-rishi-agent-payments-and-creator-earnings (financial)   ║
   ║  • etl-scripts/ (no row dropped per A4)                         ║
   ╠═══════════════════════════════════════════════════════════════╣
   ║  WARM — 40-60% coverage target                                   ║
   ║  ──────────────────────                                         ║
   ║  • yral-rishi-agent-soul-file-library                            ║
   ║  • yral-rishi-agent-user-memory-service                          ║
   ║  • yral-rishi-agent-influencer-and-profile-directory            ║
   ║  • yral-rishi-agent-skill-runtime                                ║
   ╠═══════════════════════════════════════════════════════════════╣
   ║  COOL — 20-40% coverage target (smoke + happy path only)       ║
   ║  ──────────────────────                                         ║
   ║  • yral-rishi-agent-creator-studio                               ║
   ║  • yral-rishi-agent-events-and-analytics                         ║
   ║  • yral-rishi-agent-proactive-message-scheduler                  ║
   ║  • yral-rishi-agent-media-generation-and-vault                   ║
   ║  • yral-rishi-agent-meta-improvement-advisor                     ║
   ╚═══════════════════════════════════════════════════════════════╝
```

**Target is a floor, not a ceiling.** If a service gets to 50% on a Hot-Path service, CI blocks merge. If it gets to 90% naturally, that's fine.

## The 10 testing layers

| Layer | What it does | When it runs | Who writes |
|---|---|---|---|
| 1 — Unit tests | Test single functions in isolation | Every PR (CI) | AI per PR; Codex reviews |
| 2 — Integration tests | Test components together w/ real DB+Redis | Every PR (CI) | AI per PR |
| 3 — Contract tests | v2 returns same JSON shapes as chat-ai | Every PR (CI) | Session 5 builds; AI extends |
| 4 — Smoke tests | Post-deploy "is the pipe up" check | After every deploy | AI per service |
| 5 — End-to-end tests | Full user flow through real stack | Nightly | Session 5 + AI |
| 6 — Load / perf tests | Verify p95 < 0.5 × Sentry baseline | Pre-phase-transition | Session 1 builds; AI extends |
| 7 — Eval tests | LLM response quality scoring | LLM-touching PRs | Session 5 curates gold prompts |
| 8 — Chaos tests | Fault injection (kill node, fill disk, partition) | Phase 0 + quarterly | Session 1 |
| 9 — Security tests | Pre-commit gitleaks, JWT, prompt-injection, PII | Every PR + every commit | AI + manual review |
| 10 — Motorola test | YOU using the bot on your phone | Daily | Rishi |

## How a single PR's tests look

```
   PR opens (e.g. session-3/billing-precheck)
        │
        ▼
   GitHub Actions fires:
   ┌───────────────────────────────────────────────────────────────┐
   │  pytest tests/unit/                       (~30 sec)             │
   │  pytest tests/integration/ (Docker compose) (~2 min)            │
   │  pytest tests/contract/ (vs chat-ai shapes) (~1 min)           │
   │  load-test --quick (synthetic burst)        (~3 min)            │
   │  IF LLM code touched: langfuse-eval --diff (~5 min)            │
   │  coverage-report → posted as PR comment                         │
   │  Codex review checks test quality + coverage delta              │
   └───────────────────────────────────────────────────────────────┘
        │
        ▼
   PR comment shows:
   ┌───────────────────────────────────────────────────────────────┐
   │ ✅ 47 tests, 0 failures                                         │
   │ ✅ Coverage: hot-path 78% (was 76%) — ABOVE FLOOR (70%)         │
   │ ✅ Latency: p95 612ms (target ≤710ms) — WITHIN BUDGET           │
   │ ✅ Contract parity: 21/21 endpoints match chat-ai shapes        │
   │ ✅ Codex: APPROVE — tests cover all new code paths              │
   │                                                                  │
   │ Recommend: Rishi YES merge.                                     │
   └───────────────────────────────────────────────────────────────┘
        │
        ▼
   Rishi reads. ~30 sec. Types YES (or NO + reason).
```

## What you read, what you don't

```
   YOU READ:                          YOU IGNORE:
   ─────────                          ───────────
   • PR comment summary (1-line)      • Individual test names
   • Coverage trend (going up?)       • Test code itself
   • Latency target compliance        • Mock setup details
   • Codex APPROVE / concern          • Coverage report HTML
   • MASTER-STATUS testing health     • Failed assertions verbose output
                                       (Codex summarizes if relevant)
```

## What "Motorola is the final smoke test" means

You use the chat 5-10 min/day during build. If something feels off — slow, weird, broken character, missing memory — that's the bug report. **Subjective UX trumps green tests.** A passing CI suite + bad-feel-on-Motorola = we have a gap in our tests; we add coverage there.

## Phase-by-phase testing intensity

| Phase | Heaviest testing | Lightest testing |
|---|---|---|
| Phase 0 (template + cluster) | Chaos tests, smoke tests, infra-bootstrap idempotency | Eval (no LLM yet) |
| Phase 1 (feature parity) | Contract tests (every chat-ai endpoint), integration tests | Eval (limited LLM use) |
| Phase 2 (memory) | Integration tests (memory + orchestrator), unit tests for memory extraction | Cosmetic UI flows |
| Phase 3 (Soul File + safety) | Eval tests (response quality), unit tests for safety filters | Admin endpoints |
| Phase 4 (billing) | Unit + integration for paywall logic, end-to-end for IAP flow | Eval |
| Phase 5+ | Eval becomes primary signal; load tests verify scale claims | Routine paths |

## What this strategy WILL catch

- Auth bypass / JWT validation regressions
- Billing miscalculation (wrong paywall counts)
- Latency regressions (E1 violation)
- Parity breakage (mobile sees a different shape)
- Chat-history data loss in ETL
- Crisis-detection silently disabled
- LLM quality drops (eval scores tank)
- Cluster failover not working

## What this strategy WON'T catch (acceptable gaps)

- Bugs in admin / ban / trending endpoints (low-traffic)
- Subtle UX issues without you noticing on Motorola
- Race conditions only at >100K DAU scale (we don't have that DAU yet)
- Bugs in cosmetic logging / instrumentation paths

These get caught later via Sentry, support tickets, or your Motorola feel-check.

## What's NEW vs already locked

Already locked in CONSTRAINTS (these become Layer 3, 6, 7, 8):
- A8: Contract tests vs chat-ai
- E1: Latency CI gate
- F14: Langfuse eval harness
- H3: Chaos tests
- H7: Shadow traffic
- H8: Eval on LLM-touching PRs
- H9: Synthetic user heartbeat
- H11: Schema migration safety net

New rows added in CONSTRAINTS Category J (Testing & Quality Gates) — see `01-coverage-targets-per-service.md` for the per-service floors.

## Tests live in the standard place per service

```
yral-rishi-agent-<service>/
├── app/                   ← service code
├── tests/
│   ├── unit/              ← Layer 1 — fast, isolated
│   ├── integration/       ← Layer 2 — Docker Compose required
│   ├── contract/          ← Layer 3 — vs chat-ai shapes
│   ├── smoke/             ← Layer 4 — post-deploy
│   ├── eval/              ← Layer 7 — gold prompts (if LLM-touching)
│   └── load/              ← Layer 6 — k6/Locust (if hot-path)
├── pytest.ini             ← coverage config + markers
└── secrets.yaml           ← (per D8)
```

Template's `new-service.sh` creates this structure for every spawned service.
