# yral-rishi-agent

**The YRAL agentic chat platform — v2 rebuild of the current chat-ai services.**

This repository is a **monorepo** containing both the plan/architecture docs AND every service's source code for the new agentic chat platform. It succeeds `yral-ai-chat` (Ravi's Rust) and `yral-chat-ai` (Rishi + Claude's Python), which remain live during and after v2's build.

## What's in this monorepo

| Folder | What lives here |
|---|---|
| **`yral-rishi-agent-plan-and-discussions/`** | All plan docs, constraints, timeline, architecture, feature-parity audits, coordination memos, design discussions. Start here if you're reading the project for the first time. |
| **`yral-rishi-agent-new-service-template/`** | The template every service is spawned from. Built in Phase 0. See plan-and-discussions for the design. |
| **`shared-library-code-used-by-every-v2-service/`** | Common code imported by every service: auth middleware, LLM client abstraction, Langfuse tracing, Sentry wiring, event-stream helpers, idempotency middleware, PII redaction. Built during Phase 0. |
| **`bootstrap-scripts-for-rishi-4-5-6-cluster-setup/`** | Cluster bootstrap scripts (Swarm init, Caddy, Patroni HA, Redis Sentinel, Langfuse). Populated when cluster deployment is approved. |
| **13 service folders `yral-rishi-agent-*/`** | One folder per v2 service. Each is empty until its phase (see TIMELINE). |

## The 13 services (priority order)

In the phased build order from `yral-rishi-agent-plan-and-discussions/TIMELINE.md`:

| # | Service folder | Phase | What it does |
|---|---|---|---|
| 1 | `yral-rishi-agent-public-api` | Phase 1 | HTTP/SSE edge — single API mobile hits |
| 2 | `yral-rishi-agent-conversation-turn-orchestrator` | Phase 1 | The brain — runs each chat turn end-to-end |
| 3 | `yral-rishi-agent-influencer-and-profile-directory` | Phase 1 | AI influencer catalog + profile metadata |
| 4 | `yral-rishi-agent-payments-and-creator-earnings` | Phase 1 (partial) | Reads from yral-billing for paywall check |
| 5 | `yral-rishi-agent-media-generation-and-vault` | Phase 1 (partial) | Images, audio, voice synthesis |
| 6 | `yral-rishi-agent-user-memory-service` | Phase 2 | Tiered memory with pgvector |
| 7 | `yral-rishi-agent-soul-file-library` | Phase 3 | 4-layer Soul File composer |
| 8 | `yral-rishi-agent-proactive-message-scheduler` | Phase 4 | Proactivity + first-turn nudge |
| 9 | `yral-rishi-agent-content-safety-and-moderation` | Phase 5 | Mandatory pre/post safety filter |
| 10 | `yral-rishi-agent-skill-runtime` | Phase 6 | MCP tool/skill runtime + programmatic influencer creation |
| 11 | `yral-rishi-agent-creator-studio` | Phase 7 | Soul File Coach + creator analytics |
| 12 | `yral-rishi-agent-events-and-analytics` | Phase 7 | Event pipeline + dashboards |
| 13 | `yral-rishi-agent-meta-improvement-advisor` | Phase 8 | Daily LLM-generated top-3 actions for Rishi |

## How to read this repo

1. **`yral-rishi-agent-plan-and-discussions/README.md`** — the big plan (~1500 lines): vision, capabilities, roadmap
2. **`yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md`** — every hard rule (~69 rows across 9 categories)
3. **`yral-rishi-agent-plan-and-discussions/TIMELINE.md`** — day-by-day phases with Rishi-on-Motorola checkpoints
4. **`yral-rishi-agent-plan-and-discussions/V2_TEMPLATE_AND_CLUSTER_PLAN.md`** — template + rishi-4/5/6 cluster design
5. Then the specialized doc subfolders inside plan-and-discussions (feature parity audit, mobile memo, LLM routing, etc.)

## Status

- **Plan:** in final review by Rishi before building begins
- **Build authorized:** local-only (laptop + mobile). Cluster deployment to rishi-4/5/6 and mobile-origin pushes require separate explicit approvals.
- **Current phase:** Phase 0 foundations not yet started; awaiting explicit "build" approval from Rishi.

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
