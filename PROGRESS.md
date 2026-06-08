# Master Feature Tracker — yral-rishi-agent v2 (1000x Vision)

**Last updated:** 2026-06-03 evening
**Codebase:** ~7,000 lines Python (post Phase 25 full rollout + Phase 23 V1 backend)
**Total phases:** 25 (Phase 0–25) | **Est. total days remaining:** ~50-65 days
**Cutover target:** few days from now (per Rishi 2026-06-03)

---

## PHASE 0: CLEANUP
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 0.1 | Archive 17 old v2 folders | ✅ Done | — | #158 |
| 0.2 | Remove 7 worktrees | ✅ Done | — | #158 |
| 0.3 | Prune 130 stale branches | ✅ Done | — | #158 |
| 0.4 | New CLAUDE.md (10 rules) | ✅ Done | — | #158 |
| 0.5 | New GLOSSARY.md | ✅ Done | — | #158 |
| 0.6 | New CI workflow | ✅ Done | — | #158 |
| 0.7 | New Codex review workflow | ✅ Done | — | #159 |
| **Phase 0 total** | | **✅ Complete** | **1 day** | |

## PHASE 1: FEATURE PARITY (30 endpoints matching chat-ai)
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 1.1 | Health: GET / + /health + /status | ✅ Done | — | #158 |
| 1.2 | Auth: GET /api/v1/auth/me | ✅ Done | — | #158 |
| 1.3 | Influencer READ: list + trending + get-by-id (3 endpoints) | ✅ Done | — | #158 |
| 1.4 | Conversations: create + list v1 + list v2 + messages + mark-read + delete (6 endpoints) | ✅ Done | — | #158 |
| 1.5 | Send message + AI reply (THE HEART — 1 endpoint) | ✅ Done | — | #158 |
| 1.6 | Influencer CREATE flow: generate-prompt + validate + create + edit + video-prompt + delete + ban + unban (8 endpoints) | ✅ Done | — | #158 |
| 1.7 | Media upload — image (1 endpoint, mobile UI live) | ✅ Done | — | #158 |
| 1.7b | Audio upload — backend live + 3 bug fixes (PR #254 storage_key, PR #255 audio MIME defaults, PR #257 image multimodal). Mobile PR #1179 sent to Sarvesh 2026-06-03 (dormant by default behind AudioRecordingEnabled flag) | ✅ Done (backend) / 🔄 Mobile with Sarvesh | — | #158 + #254 + #255 + #257 (backend) + yral-mobile #1179 (mobile, with Sarvesh) |
| 1.8 | Image generation in chat (1 endpoint) | ✅ Done | — | #158 |
| 1.9 | Human-to-Human chat: create + list + send (3 endpoints) | ✅ Done | — | #158 |
| 1.9a | H2H polish: sender_id in message API responses (unblocks mobile bubble alignment) | ✅ Done | — | #220 |
| 1.9b | H2H polish: v2 inbox surfaces H2H rows (LEFT JOIN + participant_b OR-clause + metadata-bulk peer enrichment) | ✅ Done | — | #228 |
| 1.9c | H2H access fix: recipient access on /messages + /read + SSE + image endpoints (PR #241), unread badge tick on inbox (PR #245, #247) | ✅ Done | — | #241 + #245 + #247 |
| 1.9d | H2H mobile UI (5-step bidirectional verified on Motorola) — PR #1178 sent to Sarvesh 2026-06-03 (dormant by default behind H2hChatEnabled flag) | 🔄 Mobile with Sarvesh | — | yral-mobile #1178 |
| 1.10 | Chat as Human (creator takeover mode) — backend ✅ shipped & retested. Mobile UI ✅ shipped via yral-mobile PR #1172 (merged 2026-05-29). Feature-flag-gated, default OFF. Awaiting agent v2 cutover before flag flip. | ✅ Done | — | #170 (backend, merged 2026-05-28) + yral-mobile #1172 (mobile, merged 2026-05-29) |
| 1.11 | Unified inbox v3 (1 endpoint) | ✅ Done | — | #158 |
| 1.12 | Billing paywall (calls billing.yral.com) | ✅ Done | — | #158 |
| 1.13 | WebSocket inbox + WS docs (1 WS + 1 endpoint) | ✅ Done | — | #158 |
| 1.14 | ETL data migration (3.3M messages) | ✅ Done | — | — |
| 1.14a | Continuous ETL chat-ai → V2 (S3-mediated, every ~5 min) — Phases 1-4 shipped + Option A skip-duplicate-conv applied. Currently OFF (ENABLE_ETL_LOOP=false since 2026-05-30 emergency) — needs verification + re-enablement. | 🔄 Off post-emergency, code complete | 0.5 left (re-enable + verify) | #211, #212, #213, #214 |
| 1.15 | Swarm deploy (2 replicas on rishi-4/5) | ✅ Done | — | — |
| 1.16 | Full Motorola test of all 30 endpoints | ⏳ Tomorrow | 0.5 | — |
| 1.17 | Latency comparison vs chat-ai — DEFERRED to pre-cutover per Rishi 2026-05-30. Run only after all backend features complete + ~2 days before Phase 21 cutover. Automated p50/p95/p99 script vs CLAUDE.md 50%-faster target. | ⏳ Deferred — pre-cutover | 0.5 | — |
| 1.18 | Automated endpoint test script (24/24 PASS) | ✅ Done | — | #169 |
| **Phase 1 total** | | **90% done** | **2 days left** | |

## PHASE 2: CORE IMPROVEMENTS
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 2.1 | Langfuse LLM tracing | ✅ Done | — | #160, #164 |
| 2.2 | LLM client abstraction (typed LlmResponse) | ✅ Done | — | #161 |
| 2.3 | Soul File 4-layer composer | ✅ Done | — | #161 |
| 2.4 | Memory enhancement (extraction + injection) | ✅ Done | — | #161 |
| 2.5 | Request ID middleware (end-to-end tracing) | ✅ Done | — | #160 |
| 2.6 | Redis cross-node WebSocket pub/sub | ✅ Done | — | #160 |
| 2.7 | SSE streaming (word-by-word AI responses) — backend ✅ live (#189). Mobile SSE parser ✅ shipped via yral-mobile PR #1173 (merged 2026-06-02). Feature-flag-gated, default OFF. | ✅ Done | — | #189 (backend) + yral-mobile #1173 (mobile, merged 2026-06-02) |
| 2.V1 | Verify: Langfuse receiving traces? | ⏳ Pending | 0.5 | — |
| 2.V2 | Verify: Redis WS connected or local fallback? | ⏳ Pending | 0.5 | — |
| **Phase 2 total** | | **85% done** | **4 days left** | |

## PHASE 3: CONTENT SAFETY
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 3.1 | Crisis detection (self-harm → helpline numbers) | ✅ Done | — | #162 |
| 3.2 | Prompt injection defense (jailbreak blocking) | ✅ Done | — | #162 |
| 3.3 | NSFW filter for non-NSFW influencers | ✅ Done | — | #162 |
| 3.4 | ML-based classifiers (replace regex with AI models) | ⏳ Pending | 3 | — |
| 3.5 | Age gating / age verification | ⏳ Pending | 2 | — |
| 3.6 | CSAM detection | ⏳ Pending | 2 | — |
| 3.7 | Consent flows for sensitive content | ⏳ Pending | 1 | — |
| 3.8 | Graceful error UX when Gemini blocks (PROHIBITED_CONTENT, etc.) — backend ✅ done (PR #173, top-level `error` object). Mobile portion pending: render `error.message` inline + "Try again" button for retryable errors. To be picked up after Chat as Human PR merges. | ✅ Done (backend) / ⏳ Pending (mobile) | 0.5 | #173 (backend) |
| **Phase 3 total** | | **38% done** | **9 days left** | |

## PHASE 4: TIERED MEMORY
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 4.1 | user_memories table + migration | ✅ Done | — | #165 |
| 4.2 | Per-conversation memory extraction via Gemini | ✅ Done | — | #165 |
| 4.3 | Memories injected into Soul File Layer 4 | ✅ Done | — | #165 |
| 4.4 | pgvector embeddings for semantic search | ✅ Done | — | #174 (backend) + #175 (Patroni-pgvector image) + #176 (Gemini model fix) + swarm env update (multi-host DATABASE_URL) |
| 4.5 | Cross-conversation memory recall | ✅ Done | — | #180 |
| 4.6 | User profile memory (name, city, job — permanent) | ✅ Done | — | #178 |
| 4.7 | Session memory in Redis (short-term) | ✅ Done | — | #182 |
| 4.8 | Memory consolidation (nightly dedup + merge) | ✅ Done | — | #183 |
| 4.9 | Polish: anti-recitation (top-K=3, variety filter, prompt hardening) | ✅ Done | — | #186 |
| **Phase 4 total** | | **100% done + 1 polish** | **0 days left** | |

## PHASE 5: PROACTIVE MESSAGES
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 5.1 | Find inactive conversations (24+ hours) | ✅ Done | — | #165 |
| 5.2 | Generate bot-initiated messages | ✅ Done | — | #165 |
| 5.3 | Background engagement loop (15 min) | ✅ Done | — | #166 |
| 5.3p | Polish: proactive quality (3-cap + variety prompt + type rotation) | ✅ Done | — | #187 |
| 5.4 | User-configurable frequency (daily/weekly/off) | ✅ Done | — | #194 (squashed into #195 via rebase) |
| 5.5 | Context-aware timing (morning greetings, evening) | ⏳ Pending | 1 | — |
| 5.6 | Streak tracking (reward consistent chatters) | ✅ Done | — | #202 |
| 5.V1 | Verify: proactive messages actually sending? | ⏳ Pending | 0.5 | — |
| **Phase 5 total** | | **50% done** | **3.5 days left** | |

## PHASE 6: FIRST-TURN NUDGE
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 6.1 | Detect idle mid-conversation (30+ min) | ✅ Done | — | #165 |
| 6.2 | Generate follow-up nudge | ✅ Done | — | #165 |
| 6.3 | Wired into engagement loop | ✅ Done | — | #166 |
| 6.3p | Polish: 1-cap on unanswered nudges (parallel to Phase 5.3p proactive cap) | ✅ Done | — | TBD |
| 6.4 | Mobile presence heartbeat (pause nudge on screen leave) | ⏳ Pending | 1 | — |
| 6.5 | Chip dismissal on new bot message | ⏳ Pending | 0.5 | — |
| 6.V1 | Verify: nudges actually sending? | ⏳ Pending | 0.5 | — |
| **Phase 6 total** | | **50% done** | **2 days left** | |

## PHASE 7: CREATOR STUDIO
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 7.1 | List creator's bots with analytics | ✅ Done | — | #166 |
| 7.2 | Per-influencer analytics (messages, conversations) | ✅ Done | — | #166 |
| 7.3 | Conversation logs for creators | ✅ Done | — | #166 |
| 7.4 | Soul File viewer | ✅ Done | — | #166 |
| 7.5 | Soul File Coach (AI helps improve personality via chat) — backend live (#191). Mobile UI in build 2026-06-03 by mobile expert — 3 known issues (bot isolation reset, CTA visibility, slowness). Slowness fixed via dashboard (soul_file_coach back to Gemini). | ✅ Done (backend) / 🔄 Mobile UI in build | 1 | #191 (backend) + mobile pending |
| 7.6 | A/B testing (two soul file versions, compare quality) | ✅ Done | — | #204 |
| 7.7 | Bot quality scorer (automatic conversation rating) | ✅ Done | — | #201 |
| 7.8 | Creator recommendations (AI suggests changes) | ✅ Done | — | #203 |
| 7.9 | 5-minute bot creation wizard (structured intake + preview) | ✅ Done (backend) / ⏳ Pending (mobile UI) | — | #205 |
| 7.10 | Mobile UI for Creator Studio | ⏳ Pending | 3 | — |
| **Phase 7 total** | | **30% done** | **14 days left** | |

## PHASE 8: CREATOR MONETIZATION
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 8.1 | Earnings summary API | ✅ Done | — | #166 |
| 8.2 | Per-influencer earnings breakdown | ✅ Done | — | #166 |
| 8.3 | Earnings transaction history | ✅ Done | — | #166 |
| 8.4 | Real payment integration (Stripe/Razorpay) | ⏳ Pending | 3 | — |
| 8.5 | Private content vault (subscriber-only media) | ⏳ Pending | 2 | — |
| 8.6 | Tip jar (one-time creator payments) | ⏳ Pending | 1 | — |
| 8.7 | 70/30 revenue split enforcement | ⏳ Pending | 1 | — |
| 8.8 | Real-time earnings notifications | ⏳ Pending | 1 | — |
| 8.9 | Mobile UI for Earnings dashboard | ⏳ Pending | 2 | — |
| **Phase 8 total** | | **25% done** | **10 days left** | |

## PHASE 9: EVAL HARNESS
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 9.1 | 50 gold prompts across categories | ✅ Done | — | #168 |
| 9.2 | Gemini-as-judge scoring | ✅ Done | — | #168 |
| 9.3 | Run eval and verify results | ✅ Done | — | #195 (eval-results-2026-05-29.json + scripts/eval_v2_vs_chat_ai.py) |
| 9.4 | CI integration (eval runs on AI-touching PRs) | ⏳ Pending | 1 | — |
| 9.5 | Quality regression alerts | ⏳ Pending | 0.5 | — |
| **Phase 9 total** | | **40% done** | **2 days left** | |

## PHASE 10: SSE STREAMING
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 10.1 | Backend SSE endpoint (stream tokens from Gemini) | ✅ Done | — | #189 |
| 10.2 | Mobile SSE parser (Kotlin/Ktor SSE client) | ✅ Done — yral-mobile #1173 merged 2026-06-02, feature-flag-gated default OFF | — | yral-mobile #1173 |
| 10.3 | JSON fallback path (old clients still work) | ✅ Done (carve-outs implemented) | — | — |
| 10.4 | Feature flag to toggle SSE on/off | ✅ Done (default OFF, Firebase Remote Config) | — | — |
| 10.5 | Sarvesh coordination for production mobile app | ✅ Done — yral-mobile #1173 merged 2026-06-02 | — | yral-mobile #1173 |
| **Phase 10 total** | | **✅ Complete (awaiting flag flip at cutover)** | **0 days left** | |

## PHASE 11: SHADOW TRAFFIC
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 11.1 | Mirror real requests to both services | ⏳ Pending | 1 | — |
| 11.2 | Compare responses offline in Langfuse | ⏳ Pending | 1 | — |
| 11.3 | Quality regression detection | ⏳ Pending | 1 | — |
| 11.4 | Latency comparison dashboard | ⏳ Pending | 0.5 | — |
| **Phase 11 total** | | **Not started** | **3.5 days** | |

## PHASE 12: RESPONSE QUALITY
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 12.1 | Per-archetype few-shot examples (educator example) | ✅ Done (educator) / ⏳ Pending (others) | 2 | #198 + #199 |
| 12.2 | Different LLM models per archetype (advisor → Claude Haiku — deferred) | ⏳ Pending | 1 | — |
| 12.3 | Different temperature per archetype (0.50–0.95 range) | ✅ Done | — | #198 |
| 12.4 | Global response quality guardrails — language enumeration ✅ kept, per-archetype sentence caps reverted after eval regressed | ⚠️ Partial | 1 | #198 → #199 (revert) |
| 12.5 | Response diversity (no repetitive phrases) | ⏳ Pending | 1 | — |
| **Phase 12 total** | | **40% done + lesson** | **3 days left** | |

## PHASE 13: ADVANCED MEMORY
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 13.1 | pgvector extension on Patroni | ⏳ Pending | 0.5 | — |
| 13.2 | Embedding generation for facts | ⏳ Pending | 1 | — |
| 13.3 | Semantic search (find memories by meaning) | ⏳ Pending | 2 | — |
| 13.4 | Cross-conversation recall | ⏳ Pending | 2 | — |
| 13.5 | Persistent user profile | ⏳ Pending | 1 | — |
| 13.6 | Memory consolidation (nightly merge/dedup) | ⏳ Pending | 1 | — |
| **Phase 13 total** | | **Not started** | **7.5 days** | |

## PHASE 14: MEDIA GENERATION
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 14.1 | Image generation (Replicate/Flux) | ⏳ Pending | 1 | — |
| 14.2 | Voice synthesis (text-to-speech) | ⏳ Pending | 2 | — |
| 14.3 | Creator-styled image generation | ⏳ Pending | 2 | — |
| 14.4 | Content vault (subscriber-only files) | ⏳ Pending | 1 | — |
| 14.5 | Consent gates for sensitive media | ⏳ Pending | 1 | — |
| **Phase 14 total** | | **Not started** | **7 days** | |

## PHASE 15: SKILL RUNTIME + MCP
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 15.1 | Tool/skill registry | ⏳ Pending | 1 | — |
| 15.2 | Skill execution sandbox | ⏳ Pending | 2 | — |
| 15.3 | MCP protocol support | ⏳ Pending | 2 | — |
| 15.4 | Built-in skills (reminders, goals, weather, news) | ⏳ Pending | 3 | — |
| 15.5 | Programmatic influencer creation API | ⏳ Pending | 1 | — |
| 15.6 | Skill marketplace (creators install skills) | ⏳ Pending | 3 | — |
| **Phase 15 total** | | **Not started** | **12 days** | |

## PHASE 16: REAL-TIME FEATURES
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 16.1 | Typing indicator ("bot is typing...") | ⏳ Pending | 1 | — |
| 16.2 | Presence heartbeat (online/offline) | ⏳ Pending | 1 | — |
| 16.3 | Read receipts (delivered/read) | ⏳ Pending | 1 | — |
| 16.4 | Online status (green dot) | ⏳ Pending | 0.5 | — |
| 16.5 | Mobile client changes for all above | ⏳ Pending | 2 | — |
| **Phase 16 total** | | **Not started** | **5.5 days** | |

## PHASE 17: ANALYTICS & DASHBOARD
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 17.1 | Per-turn cost tracking | ⏳ Pending | 1 | — |
| 17.2 | Latency dashboard (p50/p95/p99) | ⏳ Pending | 1 | — |
| 17.3 | User cohort analysis (D1/D7/D30 retention) | ⏳ Pending | 2 | — |
| 17.4 | Conversation quality scoring | ⏳ Pending | 1 | — |
| 17.5 | Anomaly detection (unusual patterns) | ⏳ Pending | 1 | — |
| 17.6 | Redis Streams event pipeline | ⏳ Pending | 2 | — |
| **Phase 17 total** | | **Not started** | **8 days** | |

## PHASE 18: META-AI ADVISOR
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 18.1 | Daily "top 3 things to do" recommendations | ⏳ Pending | 1 | — |
| 18.2 | Hypothesis generator (suggest A/B tests) | ⏳ Pending | 1 | — |
| 18.3 | Auto-experimenter (run tests, report results) | ⏳ Pending | 2 | — |
| 18.4 | Improvement recs from user feedback patterns | ⏳ Pending | 1 | — |
| **Phase 18 total** | | **Not started** | **5 days** | |

## PHASE 19: RATE LIMITING & PROTECTION
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 19.1 | Per-user rate limiting (req/min + req/hour) — config hot-editable via admin endpoint, no redeploy needed. Surfaces in 19.6 dashboard so Rishi can see who's hitting limits without ADHD-context-switching. | ✅ Done | — | #232 |
| 19.2 | Runaway cost circuit breaker — per-user daily LLM spend ceiling, hot-editable via admin endpoint. Sentry alert + 19.6 dashboard entry when triggered. | ⏳ Pending | 1 | — |
| 19.3 | DDoS protection (Caddy-level) | ⏳ Pending | 1 | — |
| 19.4 | Dead letter queue for failed tasks | ⏳ Pending | 1 | — |
| 19.5 | Synthetic user heartbeat (canary every 5 min) | ⏳ Pending | 1 | — |
| 19.6 | Admin observability page (single bookmarkable URL) — shows: top rate-limited users in last 24h, cost-breaker activations, security events from 24.x, backup-drill status, last-run timestamps. JWT-gated. ADHD-friendly: one page Rishi opens once a day, sees everything. | ✅ Done | — | #229 |
| **Phase 19 total** | | **33% done** | **4 days** | |

## PHASE 20: SELF-HOSTED LLM (Saikat-coordinated, depends on Phase 25)
**Note:** Phase 20 now follows Phase 25 (multi-provider architecture). Saikat's self-hosted endpoint becomes ONE of many providers wired through the registry, not a special-case integration.

| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 20.1 | GPU server from Saikat (H100/A100) | 🔄 In progress per Rishi 2026-05-30 | 1 | — |
| 20.2 | vLLM / TGI deployment exposing OpenAI-compatible /v1/chat/completions | ⏳ Pending | 2 | — |
| 20.3 | Model selection (Llama/Qwen/Mistral/DeepSeek) | ⏳ Pending | 1 | — |
| 20.4 | Fine-tune on YRAL conversation data | ⏳ Pending | 5 | — |
| 20.5 | Latency benchmark vs Gemini (via Phase 25.6 eval harness) | ⏳ Pending | 1 | — |
| 20.6 | Gradual rollout — flip processes one at a time via Phase 25.4 admin endpoint (no redeploy needed) | ⏳ Pending | 2 | — |
| **Phase 20 total** | | **Not started** | **12 days** | |

## PHASE 21α: ALPHA CUTOVER (yral-mobile main = internal YRAL team dogfood)
**Model established 2026-06-04 by Rishi:** Alpha = yral-mobile `main` branch, visible to whole YRAL team but NOT external users. Cutover means Firebase Remote Config flips `CHAT_BASE_URL` from chat-ai → agent.rishi.yral.com AND all 6 v2-only feature flags flip ON simultaneously (not day-by-day). 1-week internal soak before Phase 21β (production).

See memory: `project_cutover_phases_alpha_and_production.md` for full reference.

| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 21α.0 | **Auto-deploy from CI build to swarm service.** Surfaced 2026-06-02: PR #241 merged + image built by CI but swarm spec never updated → required a manual `docker service update --image` step to roll out. Hard prerequisite for alpha — hotfix during alpha cannot require manual SSH. Options: (a) CI deploy-job that SSHes from self-hosted runner / OIDC and runs `docker service update`; (b) Watchtower-style image-pull service on rishi-4 polling GHCR `:main` tag; (c) GHCR push webhook into rishi-4 endpoint. | ⏳ Pending | 1 | — |
| 21α.A1 | Mobile gate: all 6 v2 feature flags exist in `ChatFeatureFlags.kt` with `defaultValue=false` (`H2hChatEnabled`, `AudioRecordingEnabled`, `SoulFileCoachEnabled`, `SseStreamingEnabled`, `ChatAsHumanCreatorEnabled`, `VideoIdeasEnabled`) | 🔄 5/6 (VideoIdeasEnabled pending PR #1184 fix) | — | yral-mobile |
| 21α.A2 | Mobile gate: all open PRs (#1178, #1179, #1180, #1181, #1182, #1183, #1184) Sarvesh-reviewed + merged to mobile main | 🔄 In flight | 1-3 | yral-mobile |
| 21α.A3 | Mobile gate: Firebase Remote Config "alpha-override" config staged (CHAT_BASE_URL + 6 flags), pushed at T-0 | ⏳ Pending | 0.25 | — |
| 21α.B1 | Backend gate: Full Motorola test of all 30 chat-ai-parity endpoint shapes on V2 (Phase 1.16 promoted) | ⏳ Pending | 0.5 | — |
| 21α.B2 | Backend gate: Latency comparison V2 vs chat-ai; 50%-faster target met per CLAUDE.md rule 6 (Phase 1.17 promoted) | 🔴 RED — DEV-11: chat-send 9% faster (not 50% — both backends Gemini-bound, structural ceiling); inbox-list **2× SLOWER** (904ms vs 427ms p50). DEV-11b root cause: stale planner stats post-2026-06-04 failover (`pg_stat_user_tables` shows `n_live_tup=2` reality 286k, `last_analyze` NULL). Pending: Rishi-authorized `ANALYZE conversations; ANALYZE messages; ANALYZE ai_influencers; …` (≤2 min, ACCESS SHARE lock) → expected inbox-list p50 904ms → ~300-400ms, flips inbox-list 🟢. **chat-send 50% target structurally unachievable on shared Gemini provider** — recommend rule re-interpretation (measure SSE TTFT or non-LLM-overhead) before α | 0.5 | — |
| 21α.B3 | Backend gate: Continuous ETL loop re-enabled + first delta-sync verified clean (Phase 1.14a promoted) | 🔄 Off post-emergency | 0.5 | — |
| 21α.B4 | Backend gate: Phase 2.V1 Langfuse traces verified arriving | 🟢 GREEN — DEV-5: 404 successful ingestion POSTs (HTTP 207 Multi-Status) in last hour, zero error/warn lines. ~7 batches/min throughput. All 4 trace_generation call sites confirmed instrumented in `app/services/ai_client.py`. Manual UI verification a 2-min exercise during meeting if wanted | — | — |
| 21α.B5 | Backend gate: Phase 2.V2 Redis WS pub/sub verified (not local fallback) | 🟢 GREEN — DEV-6: 8 subscribers active (4 workers × 2 replicas on .1/rishi-5 + .2/rishi-4), zero "Redis not available — local-only" fallback log lines. Live cross-node propagation test deferred to β chaos suite | — | — |
| 21α.B6 | Backend gate: Phase 19.2 cost circuit breaker landed (still have $400-incident memory) | 🔄 DRAFT PR #289 — service layer complete + 4 review questions answered via `docs/cutover-audits/DEV-12-PR-289-prep.md`. **α gate added (21α.B6a below):** PR #289 explicitly defers `CostCeilingExceeded → 402` wiring in `chat.py` to follow-up — without it, breaker detects but route does not refuse. Must land before α | 1 | #289 |
| 21α.B6a | **α gate (added 2026-06-05 from DEV-12 review):** Wire `CostCeilingExceeded → 402` in `app/routes/chat.py` after #289 service layer merges. Without this, the breaker is half-shipped — detects but doesn't enforce at the user-facing endpoint. ~15 LOC + 1 test | ⏳ Pending — depends on #289 merge | 0.25 | — |
| 21α.B7 | Backend gate: Phase 1.14 ETL completeness re-verified (re-bootstrap PR #227 closed gap 2026-06-04) | ✅ Done | — | #227 + sidecar pg16 manual op |
| 21α.C1 | Risk audit gate: Push notifications (chat-ai's `services/push_notifications.py`) ported + fire-tested on V2 — high-risk silent-inbox failure if missing | 🟡 YELLOW — DEV-1: Port complete + **broader-coverage than chat-ai** (4 trigger points vs 1). One consistency issue: v2 sends `data.type="chat_message"`, chat-ai sends `"new_message"`. Mobile expert confirming what Android routes on → 1-line backend fix in ~10 min if needed. Flips 🟢 once data.type alignment confirmed | 0.5 | — |
| 21α.C2 | Risk audit gate: Image generation via Replicate Flux fired end-to-end on V2 against a real bot | 🟢 GREEN — DEV-2: 1 live Replicate Flux Dev call fired end-to-end in 9s, image URL returned, ~$0.003 cost | — | — |
| 21α.C3 | Risk audit gate: Ansuman's `recsys-influencer-feed.ansuman.yral.com` confirmed reading from V2's `ai_influencers` table (not chat-ai's) — coordinate with Ansuman | ⏳ Pending — VERIFY | 0.25 | — |
| 21α.C4 | Risk audit gate: Billing paywall (25-50 msg limit, calls billing.yral.com) tested empirically on V2 | 🟡 YELLOW (α-acceptable, **β BLOCKER**) — DEV-3: Architecture is **intentionally client-side** per commit `7881e2e` (2026-05-26). Mobile calls billing.yral.com BEFORE chat-send; v2 trusts mobile verified access. Matches chat-ai (neither service has server-side check). v2 has unused `BILLING_URL` config. Acceptable for α (internal cohort). For β: motivated user can bypass mobile gate by hitting API directly → unbounded free chat → unbounded Gemini cost. Server-side enforcement (~150 LOC, leverages DEV-12's Redis substrate) needed before β | 0.25 | — |
| 21α.C5 | Risk audit gate: Google Chat admin webhooks (`services/google_chat.py` 81 lines) port verified — silent admin blind-spot risk | 🟢 GREEN — DEV-4: webhooks port verified | — | — |
| 21α.S1 | Security gate: `gitleaks` full-history scan run; any real leaks remediated (Phase 24.1 promoted-light) | 🟢 GREEN — DEV-7: 4 findings, all false positives. Baseline saved to `docs/security/secret-scan-baseline-2026-06-05.md` | — | — |
| 21α.S2 | Security gate: DATABASE_URL secret rotated (I13 follow-up — leaked into 2026-06-02 audit transcript) | ⏳ Pending — DEDICATED SESSION | 0.5 | — |
| 21α.S3 | Security gate: JWT extraction empirically matches chat-ai (same issuer check, same fields, same error paths) | 🟢 GREEN — DEV-8: v2 accepts strict superset of chat-ai tokens; zero risk of logging users out at cutover. Two cosmetic error-message diffs only | — | — |
| 21α.S4 | Security gate: CORS + Sentry/Langfuse log redaction spot-checked (no JWTs/API keys in logs) | 🟢 GREEN — DEV-9: CORS `*` safe (auto-`allow_credentials=False`). Sentry has comprehensive `before_send` + `before_breadcrumb` scrubbers. 9 JWT hits in docker logs are ADMIN-only (overnight audit token). Zero mobile-user JWTs/API-keys in logs | — | — |
| 21α.S5 | Security gate: `pip-audit` quick run; no P0 dep vulns (Phase 24.3 promoted-light) | 🟡 YELLOW (α-acceptable, β follow-up PRs) — DEV-10: 14 vulns across 3 packages. **pyjwt 2.10.1→2.13.0** (7 PYSEC IDs — low exposure, we don't verify signatures per CONSTRAINTS E9). **python-multipart 0.0.20→0.0.27** (3 DoS CVEs, bounded by Caddy if `request_body_max_size` is set — **flagged unverified**). **starlette 0.46.2→0.49.1** (4 — don't bump alone; needs FastAPI bump). β follow-up PRs (~30 min total) | 0.25 | — |
| 21α.D1 | Snapshot 1: pre-alpha-cutover pg_dump V2 + chat-ai → `pre-alpha-cutover-YYYY-MM-DD/`, md5'd | ⏳ Pending | 0.25 | — |
| 21α.E1 | Smoke test: Mobile expert builds "alpha-preview" APK with CHAT_BASE_URL=agent + all 6 flags ON | ⏳ Pending | 0.25 | — |
| 21α.E2 | Smoke test: Rishi runs 15-test matrix on Motorola (see memory `project_cutover_phases_alpha_and_production.md`) — every test must pass | ⏳ Pending — ABORT GATE | 0.5 | — |
| 21α.F1 | T-0: Firebase Remote Config push (CHAT_BASE_URL + 6 flags ON) | ⏳ Pending | — | — |
| 21α.F2 | T+1h watch: Sentry error rate stable; kill-switch any spiking process | ⏳ Pending | — | — |
| 21α.F3 | T+24h: Clear → 7-day alpha soak begins | ⏳ Pending | 7 | — |
| **Phase 21α total** | | **Overnight audits closed 12/12 (per `docs/cutover-audits/INDEX.md`): 8 🟢 / 3 🟡 / 1 🔴 (DEV-11 latency, cheap fix queued via ANALYZE).** Remaining gates: 21α.0 auto-deploy, 21α.A2 Sarvesh queue (6 PRs), 21α.A3 Firebase staged, 21α.B1 30-endpoint Motorola test, 21α.B2 re-run after ANALYZE, 21α.B3 ETL re-enable, 21α.B6+B6a cost breaker, 21α.C3 Ansuman recsys, 21α.D1 snapshot, 21α.E1/E2 smoke, 21α.F1-F3 cutover ops. 🟡-as-α-acceptable: C1 push notif (1-line fix pending mobile), C4 billing client-side, S5 dep audit. | **~2-3 day prep + 7 day soak** | |

## PHASE 21β: PRODUCTION CUTOVER (Play Store + App Store submission)
**Triggered after Phase 21α 1-week alpha soak clean.** Same codebase, just submitted to app stores. External users get v2.

| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 21β.G1 | Gate: ≥7 days alpha with **zero Sentry P0s** | ⏳ Pending | — | — |
| 21β.G2 | Gate: All YRAL team report no regressions in alpha | ⏳ Pending | — | — |
| 21β.G3 | Gate: Phase 24 full security drill (24.1-24.4) clean | ⏳ Pending | 5 | — |
| 21β.G4 | Gate: I11 offsite backup verified + I10 weekly restore drill running | ⏳ Pending | 1 | — |
| 21β.G5 | Gate: Latency stable + meets 50%-faster under real alpha load | ⏳ Pending | — | — |
| 21β.G6 | Gate: ETL loop running continuously, no backlog | ⏳ Pending | — | — |
| 21β.D2 | Snapshot 2: pre-prod-cutover pg_dump V2 + chat-ai → `pre-prod-cutover-YYYY-MM-DD/`, md5'd | ⏳ Pending | 0.25 | — |
| 21β.F1 | Mobile expert submits alpha codebase to Play Store + App Store | ⏳ Pending | 0.5 | — |
| 21β.F2 | Wait for store approval (Play ~24h, App Store ~2-7 days) | ⏳ Pending | 2-7 | — |
| 21β.F3 | Live to general users → 24h tight monitor window | ⏳ Pending | 1 | — |
| 21.6 | Chat-ai standby 90+ days (no shutdown) | ⏳ Pending | — | — |
| 21.7 | Decommission chat-ai (Rishi explicit approval only) | ⏳ Pending | 1 | — |
| **Phase 21β total** | | **Not started — depends on 21α clean** | **~5-10 days (incl. store review)** | |

## MOBILE CLIENT WORK (across phases)
| # | Feature | Depends on | Status | Est. days |
|---|---------|-----------|--------|-----------|
| M1 | H2H chat UI — yral-mobile #1178 sent to Sarvesh 2026-06-03 (feature-flag-gated default OFF, mic-hide G6 deferred to Task #64 post-merge commit) | Phase 1 API ✅ | 🔄 With Sarvesh | 0 |
| M2 | Chat as Human toggle UI — yral-mobile #1172 merged 2026-05-29 (feature-flag-gated default OFF) | Phase 1 API ✅ | ✅ Done | 0 |
| M3 | SSE streaming parser — yral-mobile #1173 merged 2026-06-02 (feature-flag-gated default OFF) | Phase 10 backend | ✅ Done | 0 |
| M4 | Soul File Coach UI — in build 2026-06-03 by mobile expert. 3 known issues found by Rishi: (1) Create AI Influencer CTA vanishing on human profile [logs pending], (2) coach slow [fixed today via dashboard flip to Gemini], (3) bots sharing messages [openForBot reset fix pending] | Phase 7.5 API ✅ | 🔄 In build | 2 |
| M5 | Audio upload UI — yral-mobile #1179 sent to Sarvesh 2026-06-03 (mic-recording-only, feature-flag-gated default OFF; file-picker + waveform deferred to follow-up PRs; iOS NSMicrophoneUsageDescription action item flagged in PR) | Phase 1.7 backend ✅ | 🔄 With Sarvesh | 0 |
| M-Task-63 | Chat-as-Human "read-only" banner fix — BotAccountReadOnly state respects ChatAsHumanCreatorEnabled flag (mirror BotAccountPrompt logic). ~10-line fix-PR off main. Queued after Soul File Coach UI ships. | Phase 1.10 backend ✅ | ⏳ Pending (post-Coach UI) | 0.5 |
| M-Task-64 | G6 mic-hide on H2H chats — 5-line `&& !viewState.isHumanChat` gate. Added as follow-up commit on whichever of #1178 or #1179 rebases second after merge. | Phase 1.7b + 1.9 mobile | ⏳ Pending (post-Sarvesh-merges) | 0 |
| M6 | Creator Studio UI (full dashboard) | Phase 7 API ✅ | ⏳ Pending | 3 |
| M7 | Earnings dashboard UI | Phase 8 API ✅ | ⏳ Pending | 2 |
| M8 | Typing indicator animation | Phase 16 backend | ⏳ Pending | 1 |
| M9 | Presence / online status | Phase 16 backend | ⏳ Pending | 1 |
| M10 | Read receipts | Phase 16 backend | ⏳ Pending | 1 |
| M11 | Skill marketplace UI | Phase 15 backend | ⏳ Pending | 3 |
| M12 | Private content UI | Phase 14 backend | ⏳ Pending | 2 |
| M13 | Push notification improvements | Phase 5 | ⏳ Pending | 1 |
| **Mobile total** | | | | **22 days** |

## PHASE 22: AI INFLUENCER PROFILE SECTIONS (NEW — 2026-05-29)
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 22.1 | Profile section 1 — videos created/uploaded on platform (backend: query by ai_influencer_id from video service; mobile: grid view in profile tab) | ⏳ Pending | 2 | — |
| 22.2 | Profile section 2 — drafts (saved-but-unpublished content); backend storage + mobile rendering | ⏳ Pending | 2 | — |
| 22.3 | Daily 5 video ideas per bot — expanded per Feature Strategy session 2026-06-04 (design doc: `docs/VIDEO-IDEAS-FEATURE-DESIGN.md`). Decisions locked: third profile tab UX (next to Published + Drafts), headless Create wired into existing `POST offchain.yral.com/api/v2/videogen/generate`, one-at-a-time global lock via `VideoGenerationTracker`, no trending topics in v1 (ideas from archetype + Soul File + recent convs only), active bots only (≥1 message last 7 days) + cold-start on-demand, user-profile version deferred (gate on Create-tap metrics). | 🔄 Backend starting 2026-06-04 per Rishi (pre-cutover parallel); mobile waits for Coach UX overhaul | 5 | (see 22.3a-22.3e below) |
| 22.3a | Migration 032 `video_ideas` table (pg_dump first per Rule 9) — UUID PK, influencer_id FK + ON DELETE CASCADE, batch_date, rank 1-5, hook, idea_text, status fresh/used, UNIQUE(influencer_id, batch_date, rank) | Developer session | ⏳ Pending | 0.5 |
| 22.3b | Backend feature PR — `app/services/video_ideas.py` clone of quality_scorer pattern (generate_for_one_bot, generate_all_once, video_ideas_loop), kill_switch entry "ENABLE_VIDEO_IDEAS_LOOP", `video_idea_generation` registry process (cheap internal_vllm default, surfaces on 25.9 dashboard), `app/repositories/video_idea_repo.py`, two endpoints on `app/routes/influencers.py` (`GET /video-ideas` owner-only + `POST /video-ideas/{idea_id}/used` Create-tap metric for future user-profile version gating). ~350 LOC — sign-off via design doc. | Developer session | ⏳ Pending | 1.5 |
| 22.3c | Mobile: third "Ideas" profile tab + 5-idea list UI + data source — `ProfileTab.Ideas` enum, third `ProfileTabItem` with lightbulb icon, visibility gated on `isOwnProfile && isAiInfluencer`, `IdeasListContent` with header + 5 rows (rank + hook + idea_text + Create button), data layer mirrors `CoachRemoteDataSource` | Mobile expert (after Coach UX overhaul ships) | ⏳ Queued | 2 |
| 22.3d | Mobile: one-tap Create headless generation — guard `!VideoGenerationTracker.state.isGenerating` → `GenerateVideoUseCase(prompt=idea.ideaText, uploadHandling=ServerDraft)` → `videoDraftPollingManager.onGenerationSubmitted` + `markIdeaUsed` + toast "Creating video — check Drafts" + row flips to ✓. Failure (429/credits) clears tracker. **Verify on device BEFORE building: whose Drafts does the video land in when fired from bot profile?** | Mobile expert | ⏳ Queued (post-22.3c) | 1 |
| 22.3e | Motorola end-to-end verification — bot profile shows 3rd tab + 5 ideas, tap Create → toast → Drafts shows progress tile → all other Create buttons greyed → draft lands → buttons re-enable + idea shows ✓; second Create during flight → blocked; human own profile still shows 2 tabs; next day → 5 new ideas | Rishi + mobile expert | ⏳ Queued (post-22.3d) | 0.5 |
| 22.4 | Mobile UI for all three sections in AI Influencer profile | ⏳ Pending | 3 | — |
| **Phase 22 total** | | **Not started** | **10 days** | |

## PHASE 25: MULTI-PROVIDER LLM ARCHITECTURE (NEW — 2026-05-30)

**Driver:** Rishi 2026-05-30 (post-incident): "I want to become LLM independent in the near future, I feel like Gemini is too expensive... we need to have a system where we can swap to LLM... we have Gemini that we currently use or can also use any other LLM (with OpenAI API Specifications) for one or other processes... we can decide which LLM we want to use for what process. Saikat is in the process of setting up a self hosted LLM for us."

**Goal:** Decouple v2 from Gemini lock-in. Every call site (each background loop, each user-facing path) can be routed to Gemini OR any OpenAI-spec-compatible provider (OpenRouter, OpenAI, Together, vLLM, Saikat's self-hosted) via admin config — no redeploy.

**Strategic value:** This is the single biggest cost-control lever beyond Phase 19/24 ceilings. Cheap models (self-hosted Qwen/Llama) for quality_scorer + memory_extraction + nudges; reserve Gemini Pro for user-facing chat where quality matters most. The $400 incident becomes structurally impossible — different processes cap on different providers.

**Builds on:**
- Phase 2.2 (LLM client abstraction with typed LlmResponse) — already shipped; this phase completes it
- Phase 12.2 (different LLM models per archetype) — subsumed
- Phase 20 (self-hosted LLM specific deployment) — Phase 25 is the prerequisite; Phase 20 becomes "Saikat's self-hosted LLM as ONE of many providers wired through Phase 25"

**Architecture:**
- Two client implementations: `app/services/llm_clients/gemini.py` (existing, refactored) + `app/services/llm_clients/openai_compatible.py` (new — works against any /v1/chat/completions endpoint)
- Provider registry: `app/services/llm_registry.py` — single source of truth mapping `process_name` → `{provider, model, base_url, api_key_secret}`
- Admin endpoint: `PATCH /admin/llm-registry` — hot-edit which process uses which provider/model, no redeploy
- Process names map 1:1 with the per-loop budgets from Phase 19.6: `quality_scorer`, `memory_extraction`, `proactive_generation`, `user_chat_main`, `nudge_generation`, `soul_file_coach`, `character_generator`, `wizard`, `image_generation` (uses Replicate today)

| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 25.1 | `app/services/llm_clients/openai_compatible.py` — HTTPX client against /v1/chat/completions with streaming + error handling + token usage extraction; works against OpenAI, OpenRouter, Together, vLLM, Saikat self-hosted, Ollama | ✅ Done | — | #250 |
| 25.2 | `app/services/llm_registry.py` — provider registry + per-process routing logic; default config in code, hot-overrides in `llm_process_config` table | ✅ Done (table-backed overrides land in 25.4) | — | #250 |
| 25.3 | Wire all processes to registry — 7 background processes migrated via #251 | ✅ Done | — | #251 |
| 25.3b | Chat orchestration through registry — user_chat_main + audio_transcription + proactive_generation + wizard preview, symmetry restored across all 12 processes | ✅ Done | — | #252 |
| 25.4 | `PATCH /admin/llm-routing` — hot-edit endpoint + DB-backed overrides + migration 026 applied + verified end-to-end via psql | ✅ Done | — | #253 + migration 026 |
| 25.5 | `llm_costs` table + cost recording wired into all 3 dispatch paths; real + synthetic basis writes verified ($0.0000110 Gemini, $0.0000011 internal_vllm) | ✅ Done | — | #256 |
| 25.6 | Per-provider eval harness — extend Phase 9 to run the 50-gold-prompt eval against (provider, model) tuples; comparison report so we can SEE that switching processes doesn't regress quality | ⏳ Pending | 1 | — |
| 25.7 | Anshuman/Saikat self-hosted LLM integration test — internal_vllm endpoint verified end-to-end (wire, auth, dispatch, semaphore, OpenAI-spec request, response parsing, streaming, usage extraction, hot-swap mechanism). Latency observation: TTFT 4-5s on internal_vllm — fine for background loops, disqualifies user_chat_main without prefill optimization. | ✅ Done — GREEN | — | (no PR — verification via PATCH cycle) |
| 25.8 | Docs + GLOSSARY entry explaining "process name → provider/model" mental model for Rishi | ⏳ Pending | 0.5 | — |
| 25.9 | **LLM Routing dashboard page** — shipped 2026-06-03. Live at `https://agent.rishi.yral.com/admin/llm-routing?token=<jwt>`. Per-process table with provider/model/timeout dropdowns + Save + Reset. Real $/24h vs synthetic split. Traffic-light rejection % (green/amber/red). Used today to flip soul_file_coach + soul_file_recommendations + character_generator + ai_influencer_wizard_simulation back to Gemini for TTFT. | ✅ Done | — | #261 |
| 25.10 | (NEW 2026-06-03) Phase 25.3b extraction trail audits — 3 confirmed instances (PR #254 audio storage_key, PR #255 audio MIME defaults, PR #257 image multimodal). Systematic audit of every multimodal/special-case path in legacy `ai_client.generate_response` vs new `llm_registry.call` for gaps. Candidates: NSFW OpenRouter routing, audio in non-transcription paths, safety_settings per-archetype, per-archetype temperature/max_tokens. | ⏳ Pending — queued post-current bugs | 1 | — |
| 25.11 | (NEW 2026-06-03) Full rollout to internal_vllm — 9 background processes migrated via DB overrides, 8 background loops re-enabled in 4 tranches with 15-min watches between. 1-hour validation: 329 calls / 0 failures / spend dropped to ~$0.80/day projected vs $400/24h May 30 incident. Memory entry `project_phase_25_full_rollout_2026_06_03.md`. | ✅ Done — GREEN | — | #258 (outcome tracking) + migrations 027/028 + #259 (engagement loop fix) |
| **Phase 25 total** | | **~95% done — 25.6 (eval, deferred) / 25.8 (docs) remain. 25.9 dashboard shipped (#261). 25.10 audit in flight overnight.** | **0.5-1 day** | |

**When this slots in:** AFTER cutover (Phase 21) but BEFORE Phase 23 Skills Framework. Reasoning: cutover is end-of-next-week priority; Phase 23 benefits from already being multi-provider (different skill coaches likely want different models). So order: Phase 21 → Phase 25 → Phase 22/23 in parallel.

**Cross-cutting impact on tomorrow's cost-defense PR (Layers 1+2+3):**
- Table name: `llm_costs` NOT `gemini_costs` — provider column from day one
- Concurrency semaphore: per-provider, not global (Saikat's self-hosted has different limits than Gemini)
- Hard daily $ ceiling: cross-provider total (this is what we care about for total spend); per-process budgets in Layer 5 of cost-defense plan map naturally onto Phase 25's process registry

## PHASE 24: SECURITY & SAFETY DRILLS (NEW — 2026-05-30)

**Driver:** Rishi 2026-05-30: "How do we make sure that there has been no keys leaked in the entire code base and I want to do a regular safety drill also to check that nothing can attack our entire system."

**Goal:** Prove the surface area is safe, surface any vulnerabilities, and keep doing so automatically.

| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 24.1 | Codebase secret scan — `gitleaks` full-history scan once (entire repo + .env files + bootstrap/ + scripts/), remediation of any real finds (force-push only after Rishi explicit approval), GitHub Actions workflow that runs `gitleaks` on every PR. Report saved to `docs/security/secret-scan-baseline.md`. | ⏳ **Pending — post Phase 25 cutover** | 1 | — |
| 24.2 | Weekly automated safety drill — cron job (e.g., Sun 03:00 UTC) running a script against production that exercises: (a) auth bypass (hit auth-required endpoints without token, expect 401), (b) token tampering (modified JWT, expect 401), (c) IDOR (try to access another user's conversation, expect 403/404), (d) SQL injection (special chars in query params), (e) path traversal in path params, (f) large payload (10MB JSON body — expect 413), (g) rate limit verification (overwhelm a single endpoint, expect 429), (h) cost circuit breaker verification (try to burn money rapidly, expect breaker trip), (i) WebSocket malformed frame, (j) Caddy-level slowloris attempt. Each test idempotent + safe to run weekly. Report saved to `/admin/safety-drill/latest`, surfaced on 19.6 dashboard with PASS/FAIL per category. Sentry alert on any new FAIL. | ⏳ **Pending — post Phase 25 cutover** | 2 | — |
| 24.3 | Dependency vulnerability scan — `pip-audit` (Python) + Trivy (Docker images) wired into CI. Reports saved to `docs/security/dep-audit/`. Renovate or Dependabot for automated patch PRs. | ⏳ **Pending — post Phase 25 cutover** | 1 | — |
| 24.4 | Secret rotation runbook — written process for rotating each external key (Gemini, OpenRouter, Replicate, S3, Sentry DSN, Langfuse, billing.yral.com auth). Stored at `docs/security/secret-rotation-runbook.md`. NOT automated rotation in V1 — just the runbook so Rishi can rotate in a hurry if needed. **First entry: 2026-06-02 Redis password rotation (atomic-swap via `--secret-rm` + `--secret-add` with target-name preservation; see audit transcript). Write tomorrow alongside DATABASE_URL rotation.** | ⏳ Pending — write tomorrow with DATABASE_URL rotation | 0.5 | — |
| 24.5 | Email digest (daily, 08:00 IST) — single email to rishi@gobazzinga.io summarizing: rate-limited users yesterday, cost-breaker hits, security drill last status, backup-drill last status, any new Sentry alerts. Same data as 19.6 dashboard, pushed not pulled. ADHD-friendly — Rishi doesn't have to remember to check. **Audit 2026-06-02: SMTP_HOST never configured; only one digest attempt (2026-05-30) failed to send. Configure SMTP when Phase 25's daily-cost line is ready to surface — no point sending digests without meaningful cost data.** | ✅ Done (plumbing) / ⏳ SMTP_HOST + re-enable: defer to Phase 25 follow-up | — | #230, #231 |
| **Phase 24 total** | | **Not started** | **5.5 days** | |

## PHASE 23: SKILLS FRAMEWORK (NEW — 2026-05-30)

**Design doc:** `docs/SKILLS-FEATURE-DESIGN.md` (Session 7 / Coach Strategy, 2026-05-30, reviewed with Codex).

**The strategic insight:** YRAL Agent v2 already has the bones of a Coach OS. The skill framework adds the smallest thin layer that turns it into an Expert Factory — one Python dict + one new table + one prompt-composer change — that lets the same engine power nutrition coaches, news briefings, travel advisors, language tutors, and the long tail of verticals without hand-tuning each one.

**Mental model:** Influencer = archetype (personality) × skill (job-to-be-done). Same skill pairs with different archetypes (Kareena = advisor × nutrition_coach; Rohan-the-bro = entertainer × nutrition_coach). Soft compatibility per skill (nutrition_coach pairs with advisor + educator, NOT companion — companion's "no medical advice" rule contradicts the skill's job).

**V1 scope locked:** one skill (`nutrition_coach`), one influencer (Kareena), three behaviors (store goal + scheduled check-ins + adherence via existing `current_streak_days`). ~420 lines total — above the CLAUDE.md 100-line ceiling, Rishi sign-off captured.

**Personal dogfood motivation (Rishi, 2026-05-30):** "I myself can start using the AI Nutrition feature and give feedback on what is improving and what all is lagging. It will help me dogfood the app for myself."

**Prerequisites — Phase 23 starts only AFTER all of these complete:**
- M1 H2H chat mobile UI shipped
- M4 Soul File Coach UI shipped ("Make your bot better" button)
- M5 Audio upload UI shipped
- Streaming backup verified (offsite S3 + restore drill)
- Phase 21 production cutover live (end of next week target — ~2026-06-07)

| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 23.1 | Migrations 029 (user_skill_state table) + 030 (ai_influencers.skill_slug column) — pg_dump snapshot per Rule 9 | ✅ Done | — | #263 |
| 23.2 | `app/services/skills.py` — SKILLS Python dict with `nutrition_coach` entry. Pure-data catalog. | ✅ Done | — | #263 |
| 23.3 | `app/services/soul_file.py` — skill + user_skill_state layers added (order: GLOBAL → ARCHETYPE → SKILL → PER_INFLUENCER → USER_STATE → MEMORIES) | ✅ Done | — | #263 |
| 23.4 | `app/repositories/skill_state_repo.py` — get / upsert / mark_event_fired / list_due (all skill-agnostic) | ✅ Done | — | #263 |
| 23.5 | `app/routes/skills.py` — 3 endpoints + first-turn onboarding hook in chat.py + `app/services/skill_parser.py` (streaming-safe <skill_state> filter, mobile never sees the tag mid-stream) | ✅ Done | — | #264 |
| 23.6 | `app/services/proactive.py` — `find_due_skill_events` + `generate_skill_checkin` + `send_skill_checkin` wired into existing 15-min engagement loop (3rd gated block with ENABLE_SKILL_PROACTIVE_LOOP switch) | ✅ Done | — | #265 |
| 23.6c | Architecture cleanup — removed dead `skill_chat` LLM process (PR #267); skills use existing `user_chat_main` + `proactive_generation` processes. Pure-data scaling promise literally true: english_coach / daily_briefing / travel_advisor / real_estate_advisor = ONE SKILLS dict entry, zero code changes. | ✅ Done | — | #266 (rename) + #267 (remove) |
| 23.7 | Assign Kareena `skill_slug=nutrition_coach` + dogfood on Motorola — Rishi creates Kareena from his Motorola tomorrow 2026-06-04 morning then runs the UPDATE SQL + sends first message | ⏳ Tomorrow morning | 0.5 | — |
| **Phase 23 total** | | **Not started** | **4.5 days** | |

**Phase 24+ (deferred — expansion):** Once V1 is proven via Rishi's dogfooding, each new skill is one `SKILLS` dict entry + maybe a check-in prompt template. No new tables, no new services. Candidates: `daily_briefing` (India News, Stock Market), `travel_advisor`, `real_estate_advisor`, `running_coach`, `hyrox_coach`, `language_coach`, `study_coach`, `creator_growth_coach`. ~1 day per skill.

## INFRASTRUCTURE (ongoing)
| # | Item | Status |
|---|------|--------|
| I1 | Patroni HA on rishi-4/5/6 + WAL-G backups | ✅ Done |
| I2 | Redis Sentinel on rishi-4/5/6 | ✅ Done |
| I3 | Caddy on rishi-1/2/3 routing agent.rishi.yral.com | ✅ Done |
| I4 | Swarm service (2 replicas) | ✅ Done |
| I5 | Sentry project (yral-rishi-agent) | ✅ Done |
| I6 | Langfuse on rishi-6 | ✅ Done |
| I7 | Langfuse env vars on Swarm service | ⏳ Verify |
| I8 | Redis env vars on Swarm service | ⏳ Verify |
| I9 | Codex review API key working | ⏳ Verify |
| I10 | Weekly automated backup restore drill | ⏳ **Pending — post Phase 25 cutover** |
| I11 | Offsite backup to separate S3 bucket | ⏳ **Pending — post Phase 25 cutover** |
| I12 | Pre-migration snapshot automation | ⏳ **Pending — post Phase 25 cutover** |
| I13 | **DATABASE_URL secret rotation** (urgent follow-up — Postgres password leaked into audit-2026-06-02 session transcript). Atomic-swap via `--secret-rm` + `--secret-add` mirror of 2026-06-02 Redis rotation. Own session needed (high blast radius — all monolith DB connections roll). | ⏳ **Pending — schedule tomorrow** | 0.5 |
| I13 | Sentry error rate alerting | ⏳ Pending |
| I14 | Latency threshold alerting | ⏳ Pending |
| I15 | Synthetic user heartbeat (canary) | ⏳ Pending |

---

## SUMMARY TABLE

| Phase | Name | Sub-phases | Done | Pending | Est. days left |
|-------|------|-----------|------|---------|---------------|
| 0 | Cleanup | 7 | 7 | 0 | 0 |
| 1 | Feature Parity | 18 | 15 | 3 | 2 |
| 2 | Core Improvements | 9 | 6 | 3 | 4 |
| 3 | Content Safety | 7 | 3 | 4 | 8 |
| 4 | Tiered Memory | 8 | 3 | 5 | 7 |
| 5 | Proactive Messages | 7 | 3 | 4 | 3.5 |
| 6 | First-turn Nudge | 6 | 3 | 3 | 2 |
| 7 | Creator Studio | 10 | 4 | 6 | 14 |
| 8 | Creator Monetization | 9 | 3 | 6 | 10 |
| 9 | Eval Harness | 5 | 2 | 3 | 2 |
| 10 | SSE Streaming | 5 | 0 | 5 | 6 |
| 11 | Shadow Traffic | 4 | 0 | 4 | 3.5 |
| 12 | Response Quality | 5 | 0 | 5 | 5.5 |
| 13 | Advanced Memory | 6 | 0 | 6 | 7.5 |
| 14 | Media Generation | 5 | 0 | 5 | 7 |
| 15 | Skill Runtime + MCP | 6 | 0 | 6 | 12 |
| 16 | Real-time Features | 5 | 0 | 5 | 5.5 |
| 17 | Analytics & Dashboard | 6 | 0 | 6 | 8 |
| 18 | Meta-AI Advisor | 4 | 0 | 4 | 5 |
| 19 | Rate Limiting + Observability | 6 | 0 | 6 | 6 |
| 20 | Self-hosted LLM | 6 | 0 | 6 | 12 |
| 21α | Alpha Cutover (yral-mobile main + YRAL team dogfood) | 26 | 1 | 25 | ~3 prep + 7 soak |
| 21β | Production Cutover (Play Store + App Store) | 12 | 0 | 12 | ~5-10 |
| 22 | AI Influencer Profile Sections | 4 | 0 | 4 | 10 |
| 23 | Skills Framework (post-cutover, dogfood) | 7 | 0 | 7 | 4.5 |
| 24 | Security & Safety Drills | 5 | 0 | 5 | 5.5 |
| 25 | Multi-Provider LLM Architecture | 9 | 0 | 9 | 7 |
| — | Mobile Client | 11 | 0 | 11 | 21 |
| — | Infrastructure | 15 | 6 | 9 | — |
| **TOTAL** | | **195** | **55** | **140** | **~80-96 days** |

---

## INFRASTRUCTURE BACKLOG

Items surfaced during Phase 4.4 rollout (Spilo pgvector + Patroni failover gap). Not urgent — current setup works — but worth tracking for production cutover.

### Infra-X: pgbouncer hardcoded DB_HOST creates failover gap
**RESOLVED for agent service** via asyncpg `target_session_attrs=read-write` (applied as a manual `docker service update --env-add DATABASE_URL=...` swarm env change on 2026-05-28, not yet committed to repo). Agent now connects to all 3 Patroni nodes with the multi-host URL, and asyncpg auto-discovers the writer — verified via switchover round-trip (rishi-4 → rishi-5 → rishi-4, write succeeded against the new leader).

pgbouncer's `DB_HOST: patroni-rishi-4` is still hardcoded, so any **future** service that goes through pgbouncer (instead of asyncpg-direct like the agent) will break on Patroni failover. Revisit when adding the next service that needs pooled connections. Long-term answer is HAProxy + Patroni REST `/master` endpoint, but that's a separate architectural project.

### Infra-Y: agent DATABASE_URL lives only in swarm service env, not in repo
The multi-host URL change for Infra-X was applied via `docker service update --env-add`. There's no IaC source-of-truth for the agent's env vars yet. Next service-spec change will overwrite it unless we codify. **Action:** when we eventually add a `bootstrap/scripts/agent-stack.yml` (mirroring `patroni-stack.yml`), wire DATABASE_URL through it.

### Phase 0 re-audit needed before production cutover
Two Phase 0 assumptions didn't survive contact with reality on 2026-05-28:

1. **Spilo 3.0-p1 was assumed to ship pgvector — it doesn't.** Caught during migration 008. Fixed by extending Spilo with `postgresql-15-pgvector` in `bootstrap/scripts/Dockerfile.patroni-pgvector` (PR #175).
2. **pgbouncer's hardcoded DB_HOST was assumed to be transparent — it isn't.** Caught after the rolling Patroni restart. Mitigated via Infra-X above.

Recommend a Phase 0 design re-audit before production cutover: enumerate every "assumed-included" or "assumed-transparent" piece of the cluster setup and verify each empirically. Other candidates to check: WAL-G restore drill, Redis Sentinel failover, Caddy cert renewal, Langfuse S3 retention policy.

---

## ✅ RESOLVED — Phase 1.10 takeover bugs (discovered & fixed 2026-05-28)

3 bugs surfaced during Motorola testing of the Chat as Human creator-takeover UI. All fixed in PR #170 (merged 2026-05-28), verified live on agent.rishi.yral.com, and re-tested on Motorola.

| Bug | Symptom | Fix | File |
|---|---|---|---|
| 1 | Timer reset on **user** activity instead of creator's | New column `human_creator_last_message_at`; sweep + `remaining_seconds` keyed on it | migration `007_takeover_creator_timer.sql`, `takeover_repo.py`, `takeover_helpers.py` |
| 2 | Up to 36s gap between local timer expiry and server sweep | `SWEEP_INTERVAL_SEC: 30 → 5`; mobile also proactively calls `human-creator-release` at local 0:00 | `app/main.py`, mobile `ConversationViewModel.startCountdownTicker()` |
| 3 | "X has left the chat" appeared 2-3× per release | Atomic `deactivate_if_active` returns whether row was actually flipped; system message only written if it was | `takeover_repo.py`, `creator_takeover.py`, `app/main.py` |

**Verification:**
- Unit tests: `tests/test_takeover.py` — 7 tests including Bug 1 regression guard, all pass.
- E2E on live cluster: `scripts/test_takeover_e2e.py` — exercises full takeover lifecycle + AI context preservation.
- Motorola retest (2026-05-28): all 3 scenarios from `~/Claude Projects/yral-mobile/HANDOFF-CHAT-AS-HUMAN.md` pass.

**Mobile UI PR to Sarvesh still pending** — feature-flag-gated, awaiting agent v2 cutover.
