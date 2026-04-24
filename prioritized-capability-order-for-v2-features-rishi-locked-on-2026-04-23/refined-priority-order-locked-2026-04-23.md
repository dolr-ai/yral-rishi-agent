# Refined Capability Priority Order (Rishi, 2026-04-23)

This ordering SUPERSEDES the earlier slice order in CONSTRAINTS.md row F15. Locked pending Rishi's refinement.

## The priority order

1. **Memory and Depth** — Bot remembers user across sessions. Tiered memory (session / episodic / semantic / profile / pgvector embeddings). Turns the 10-message shallow chat into a genuine relationship.
2. **Better Quality / Response Quality Foundation** — Soul File layered system (Global / Archetype / Per-Influencer / Per-User-Segment), Tara Template distilled, tone normalizer, response-length guardrail, opening-line optimizer, ask-back ratio, eval harness.
3. **Billing integration** — Consume yral-billing (Google Play IAP), pre-chat access check, paywall 402 metadata, YRAL Pro tier credits. Also: creator earnings reading from billing transactions.
4. **Proactivity** — Scheduler + triggers + planner + throttler. Bot texts first. Starts with Plan B.0 within-session nudge, expands to cross-session proactive pings once memory is rich enough to know WHAT to ping about.
5. **Programmatic AI Influencer creation — open API + MCP-like service** ⭐ NEW PRIORITY from Rishi 2026-04-23. Currently influencers are created through the mobile app UI; Rishi wants them creatable programmatically via an open API AND eventually via an MCP server so external tools/agents can create influencers too. Preserves all the existing Python endpoints (`POST /api/v1/influencers/create`, `POST /api/v1/influencers/generate-prompt`, etc.) and wraps them in an MCP-compatible interface.
6. **Real Influencer parity** — Migrate every existing AI influencer (from Ravi's Rust + our Python) into v2 with ID preservation. Mobile deep-links still work. Creators keep their earnings history.
7. **First-turn nudge (Plan B.0)** — User opens chat → greeting + 3-4 option chips → 20-30s of inactivity → chips disappear, bot sends follow-up. Stop after 2nd silence. Bundled into Proactivity slice as the first concrete proactive feature.
8. **Creator tools and analytics** — Soul File Coach (creator chats with LLM to improve bot), bot quality scorer, creator analytics dashboard, earnings dashboard.
9. **Creator monetization and private content** — Private image/voice requests, content vault, consent manager, safety gate, AI-creator-image-gen, tip jar, custom video requests.
10. **Meta AI advisor and automated analytics** — LLM reads yesterday's metrics and tells Rishi top-3 actions for today. Hypothesis generator. Auto-experimenter.

## Why this ordering differs from earlier slicing

Earlier plan had slicing: MVP turn → memory+influencer-directory → quality+safety → creator tools → rest. Rishi's new ordering front-loads MEMORY + QUALITY + BILLING + PROACTIVITY because these are the most visible user-perceived wins. Programmatic AI-influencer creation moves up to position 5 as a net-new capability that expands the creator ecosystem.

## Mapping priorities to the 13 services

| Priority | Primary service(s) owning it | Dependencies |
|---|---|---|
| 1. Memory + Depth | `yral-rishi-agent-user-memory-service` | orchestrator (2), public-api (1), pgvector |
| 2. Quality | `yral-rishi-agent-soul-file-library` + `yral-rishi-agent-content-safety-and-moderation` | orchestrator (2), creator-studio (8) |
| 3. Billing | `yral-rishi-agent-payments-and-creator-earnings` (reads yral-billing) | public-api (1) for pre-chat access check |
| 4. Proactivity | `yral-rishi-agent-proactive-message-scheduler` | orchestrator (2), memory (4), events (10) |
| 5. Programmatic AI influencer creation | `yral-rishi-agent-influencer-and-profile-directory` + `yral-rishi-agent-skill-runtime` (MCP wrapper) | soul-file-library (3), creator-studio (8) |
| 6. Real influencer parity | `yral-rishi-agent-influencer-and-profile-directory` | one-time ETL + continuous CDC from existing `ai_influencers` tables |
| 7. First-turn nudge | `yral-rishi-agent-proactive-message-scheduler` (within-session mode) | public-api (1), orchestrator (2), mobile presence heartbeat |
| 8. Creator tools + analytics | `yral-rishi-agent-creator-studio` + `yral-rishi-agent-events-and-analytics` | soul-file-library (3), memory (4), payments (12) |
| 9. Creator monetization + private content | `yral-rishi-agent-media-generation-and-vault` + `yral-rishi-agent-content-safety-and-moderation` + `yral-rishi-agent-payments-and-creator-earnings` | yral-billing (team service) |
| 10. Meta-AI advisor + automated analytics | `yral-rishi-agent-meta-improvement-advisor` + `yral-rishi-agent-events-and-analytics` | Langfuse, full service telemetry |

## Expected phase structure (weeks are aspirational — cutover is NOT tied to any phase, per Rishi's no-cutover rule)

- **Phase 0 — Foundations:** template, hello-world, cluster, observability, backups, latency baselines
- **Phase 1 — MVP turn:** public-api + orchestrator (the minimum viable chat)
- **Phase 2 — Memory + Depth** (priority 1): user-memory-service + influencer-and-profile-directory (includes real-influencer parity, priority 6)
- **Phase 3 — Quality** (priority 2): soul-file-library + content-safety-and-moderation
- **Phase 4 — Billing integration** (priority 3): payments-and-creator-earnings + pre-chat access check wiring
- **Phase 5 — Proactivity + first-turn nudge** (priorities 4 + 7): proactive-message-scheduler (session + cross-session modes)
- **Phase 6 — Programmatic AI influencer creation** (priority 5): open API + MCP-compat wrapper over the existing endpoints
- **Phase 7 — Creator tools + analytics** (priority 8): creator-studio + events-and-analytics
- **Phase 8 — Creator monetization + private content** (priority 9): media-generation-and-vault
- **Phase 9 — Meta-AI advisor** (priority 10): meta-improvement-advisor

All phases coexist with the current Python yral-chat-ai service. Cutover timing is 100% at Rishi's discretion — there is no "when we cut over" plan.
