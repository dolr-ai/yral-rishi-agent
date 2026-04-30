# yral-rishi-agent

**The YRAL agentic chat platform — v2 rebuild of the current chat-ai services.**

This repository is a **monorepo** containing both the plan/architecture docs AND every service's source code for the new agentic chat platform. It succeeds `yral-ai-chat` (Ravi's Rust) and `yral-chat-ai` (Rishi + Claude's Python), which remain live during and after v2's build.

## What's in this monorepo

| Folder | What lives here |
|---|---|
| **`yral-rishi-agent-plan-and-discussions/`** | All plan docs, constraints, timeline, architecture, feature-parity audits, coordination memos, design discussions. Start here if you're reading the project for the first time. |
| **`yral-rishi-agent-new-service-template/`** | The template every service is spawned from. Built in Phase 0. See plan-and-discussions for the design. |
| **`shared-library-code-used-by-every-v2-service/`** | Common code imported by every service: auth middleware, LLM client abstraction, Langfuse tracing, Sentry wiring, event-stream helpers, idempotency middleware, PII redaction. Built during Phase 0. |
| **`bootstrap-scripts-for-the-v2-docker-swarm-cluster/`** | Cluster bootstrap scripts (Swarm init, Caddy, Patroni HA, Redis Sentinel, Langfuse). Populated when cluster deployment is approved. |
| **13 service folders `yral-rishi-agent-*/`** | One folder per v2 service. Each is empty until its phase (see TIMELINE). |

## The 13 services (canonical phase order, reconciled 2026-04-27 per Codex audit)

Authoritative phase order = priority doc (`prioritized-capability-order-for-v2-features-rishi-locked-on-2026-04-23/refined-priority-order-locked-2026-04-23.md`). TIMELINE.md follows this order. CONSTRAINTS.md row F15 references it.

| # | Service folder | Phase | What it does |
|---|---|---|---|
| 1 | `yral-rishi-agent-public-api` | Phase 1 (MVP turn) | HTTP/SSE edge — single API mobile hits |
| 2 | `yral-rishi-agent-conversation-turn-orchestrator` | Phase 1 (MVP turn) | The brain — runs each chat turn end-to-end |
| 3 | `yral-rishi-agent-influencer-and-profile-directory` | Phase 1 (MVP turn — Phase 2 expands w/ real-influencer parity per priority 6) | AI influencer catalog + profile metadata |
| 4 | `yral-rishi-agent-user-memory-service` | Phase 2 (Memory + Depth — priority 1) | Tiered memory with pgvector |
| 5 | `yral-rishi-agent-soul-file-library` | Phase 3 (Quality — priority 2) | 4-layer Soul File composer |
| 6 | `yral-rishi-agent-content-safety-and-moderation` | Phase 3 (Quality — priority 2, bundled with soul-file) | Mandatory pre/post safety filter (must be live before any real-user canary) |
| 7 | `yral-rishi-agent-payments-and-creator-earnings` | Phase 4 (Billing integration — priority 3) | Reads from yral-billing; pre-chat access check + earnings rollups |
| 8 | `yral-rishi-agent-proactive-message-scheduler` | Phase 5 (Proactivity + first-turn nudge — priorities 4 + 7) | Bot texts first; within-session nudge; cross-session pings |
| 9 | `yral-rishi-agent-skill-runtime` | Phase 6 (Programmatic AI influencer creation — priority 5) | MCP tool/skill runtime + open API + MCP wrapper for influencer CRUD |
| 10 | `yral-rishi-agent-creator-studio` | Phase 7 (Creator tools + analytics — priority 8) | Soul File Coach + creator analytics |
| 11 | `yral-rishi-agent-events-and-analytics` | Phase 7 (Creator tools + analytics — priority 8) | Event pipeline + dashboards |
| 12 | `yral-rishi-agent-media-generation-and-vault` | Phase 8 (Creator monetization + private content — priority 9) | Images, audio, voice synthesis, content vault |
| 13 | `yral-rishi-agent-meta-improvement-advisor` | Phase 9 (Meta-AI advisor — priority 10) | Daily LLM-generated top-3 actions for Rishi |

> **Earlier drafts of this table (pre-2026-04-27) had different phase numbers** — Codex audit caught the drift. The order above is canonical. Source of truth chain: CONSTRAINTS.md → priority doc → TIMELINE.md → this README. If they disagree in future, CONSTRAINTS wins.

## How to read this repo

1. **`yral-rishi-agent-plan-and-discussions/CURRENT-TRUTH.md`** — single-source-of-agreement; if other docs disagree, this wins. Created 2026-04-27 after Codex audit.
2. **`yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md`** — every hard rule (~73 rows across 9 categories)
3. **`yral-rishi-agent-plan-and-discussions/README.md`** — the big plan (~1500 lines): vision, capabilities, roadmap
4. **`yral-rishi-agent-plan-and-discussions/TIMELINE.md`** — day-by-day phases with Rishi-on-Motorola checkpoints
5. **`yral-rishi-agent-plan-and-discussions/V2_INFRASTRUCTURE_AND_CLUSTER_ARCHITECTURE_CURRENT.md`** — template + rishi-4/5/6 cluster design
6. **`yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/`** — 6-doc folder: master plan, scope sharding, auto-mode guardrails, Codex review, coordinator playbook, getting-started, state persistence, MASTER-STATUS, decision-log
7. Then the specialized doc subfolders inside plan-and-discussions (feature parity audit, mobile memo, LLM routing, priority order, etc.)

## Status (clarified 2026-04-27 per Codex audit)

- **Build mode:** PLAN-ONLY until Rishi types "build" in the coordinator session (per CONSTRAINTS A5 + I1).
- **Authorized scope when "build" is given:** local laptop work (Docker Compose, template, hello-world) + mobile changes per A12 workflow (local-only, never pushed). Per A13.
- **Still requires separate explicit approval after "build":** rishi-4/5/6 cluster deployment (Days 4-7 of Phase 0); rishi-1/2 Caddy snippet via `yral-rishi-hetzner-infra-template` repo (Day 7 — A2 carve-out exists per I8); pulling live yral-chat-ai data per A14; mobile-origin pushes per A12; cutover per A6.
- **Current state:** all plan docs done. No code written. Coordinator session awaiting "build" trigger.

For the literal launch sequence: see `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/05-GETTING-STARTED-TOMORROW.md`.

## Related services (not in this monorepo)

- `dolr-ai/yral-ai-chat` — Ravi's Rust chat (live)
- `dolr-ai/yral-chat-ai` — Python chat (live since 2026-04-23)
- `dolr-ai/yral-billing` — Google Play IAP billing; v2 consumes as-is
- `dolr-ai/yral-auth-v2` — OAuth2 + JWT; v2 consumes as-is
- `dolr-ai/yral-metadata` — user profiles + push notifications; v2 consumes as-is
- `dolr-ai/yral-mobile` — KMP mobile client (iOS + Android)
- `dolr-ai/yral-rishi-hetzner-infra-template` — predecessor template; reference only, never modified

## License + ownership

Internal DOLR project. Public for transparency. Rishi owns decisions; Claude assists with planning and implementation.
