# Master Feature Tracker — yral-rishi-agent v2 (1000x Vision)

**Last updated:** 2026-06-13 morning (pre-flip)
**Codebase:** ~7,500 lines Python (post 13-PR shipping day 2026-06-08)
**Total phases:** 25 + cutover phases 21α / 21α→β / 21αβ.I / 21β / 21γ
**Cutover target:** ~2 weeks from 2026-06-08 (realistic D+17 to real users on V2; mostly waiting on Sarvesh merges + Play Store approval)

## 2026-06-13 morning — pre-flip status

**Deployed image:** `ghcr.io/dolr-ai/yral-rishi-agent:43bdd77` (post-#363 + #364 H7 dep bumps, deployed 2026-06-13 09:15Z).
**ETL health:** ✅ heartbeat 101s old, sentinel canary 6s end-to-end (verified 2026-06-12), importer running every ~5 min, 419 files / 5,913 rows / 9,787 skipped over the last 24h.
**Pre-rollout pg_dump:** ✅ 547 MB, SHA `4613aa69da3e4a8399c843fa274163aa5de6c65a947c3ce4e93f43f98175d557`, on rishi-4 at `~/yral-backups/pre-cutover-21b-20260612-135637.dump`.
**Sentry:** ✅ DSN points at sentry.rishi.yral.com, environment=production, traces 100%, profiles 5%, Principal ID auto-attached on every authenticated request.

### Pre-flip OPEN items (in order)

1. ~~Merge #363 + #364~~ — ✅ done 09:15Z, image `43bdd77` live.
2. **Rishi smoke test on Motorola** against `agent.rishi.yral.com` — chat → reply, image upload if you use it.
3. **Confirm Sarvesh's rollback procedure** verbally (Remote Config audience flip back).
4. **Send pause messages to mobile expert** (dev session will get a NEW brief instead — see "Active autonomous work" below).
5. **Send dev-session autonomous-work brief** for H2 + H4 + H5 + H8 + H11 status check.
6. **Open monitoring tabs** — `/admin/etl-status`, `/admin/etl/reconciliation`, Sentry dashboard.
7. **Record 4 pre-flip numbers** in a note: exact flip UTC time, Remote Config audience definition Sarvesh used, baseline chat-ai p50/p95 + error rate, Sarvesh's rollback procedure.

### Active autonomous work during rollout window (developer session)

Dev session works in branches, opens PRs as ready-for-review, does NOT merge until Rishi green-lights post-rollout-stability:
- **H11** status check FIRST — confirm if it's done, partial, or not started; update PROGRESS.md.
- **H2** server-side billing paywall — 3-PR ladder per `~/.claude/plans/h2-server-side-billing-paywall-brief-2026-06-11.md`.
- **H4** Patroni failover drill — script + runbook + workflow ONLY; live break-the-cluster execution waits 24h after stable rollout.
- **H5** Redis Sentinel failover drill — same shape as H4.
- **H8** Phase 24 security drills — 24.4 rotation runbook first (lowest risk), then 24.1/24.3 CI verification, then 24.2 weekly drill workflow.

### Accepted-risk OPEN items going into rollout

- **H2 server-side billing paywall** — NOT shipped. Decision 2026-06-12: accept revenue leak for 10% cohort, ship within 48h post-rollout. Brief: `~/.claude/plans/h2-server-side-billing-paywall-brief-2026-06-11.md`.
- **H4 Patroni failover drill** — never run. WAL-G + healthy Patroni cluster is the safety net; explicit drill deferred to post-rollout.
- **H5 Redis Sentinel failover drill** — never run. Deferred to post-rollout.
- **H8 Phase 24 security drills** — NOT done (~5 days). Per 2026-06-08 model this is a strict PROD prereq; Rishi proceeding anyway with the gap documented.
- **H9 DATABASE_URL secret rotation** — NOT done. Hygiene; deferred.
- **H10 Phase 19.6 dashboard tiles** — NOT done. Observability polish.
- **H11 real-time LLM cost alerting** — was "in PR" yesterday; no merge visible today. Status to verify post-rollout.

### Post-flip queue (DAILY-LOG.md 2026-06-12 has full details)

1. **21γ.P12** — 3-line reconciliation key-name fix. First fix-up PR.
2. **H2** — billing paywall (48h target from flip).
3. **21γ.P13** — Sentry bot_id + conversation_id tags (30-min polish).
4. **Mobile expert's queued work** — #1195 Soul File UI, then #1191/#1192/#1193 corrections (Motorola pass first).
5. **21γ.P11** — +64K message gap audit.
6. **21γ.P10** — drain endpoint async refactor + TIMESTAMPTZ schema.
7. **21γ.P14** — Sentry per-iteration background-task context.

## 2026-06-08 EOD snapshot — what shipped today

**13 PRs merged** (#292–#304), 2 PRs closed (#227 + #289), zero outages, first end-to-end auto-deploy fully live.

| Area | Live now |
|------|----------|
| **LLM routing** | runpod_vllm (Saikat) provider added, Saikat-primary + Anshuman-fallback for all async, leak guard fires Sentry on async→gemini, multi-replica cache coherence via Redis pub/sub + dashboard reload-on-load |
| **Deploy infrastructure** | GitHub Actions Deploy + Rollback buttons (#294), auto-deploy on merge via workflow_run (#297, #298), auto-rollback on /health failure, 3-swarm-manager failover, `:stable` tag for known-good fallback (#303) |
| **CI safety** | gitleaks blocks PRs introducing secrets (#300), pip-audit blocks PRs introducing P0 vulns (#302) |
| **Dashboard** | View raw DB overrides page (#295), reload-on-load truth (#296), pub/sub broadcast for hot-flip visibility across replicas |
| **Operational** | ANALYZE ran on rishi-5 (inbox-list 904ms → ~350ms expected), $22 quality_scorer Gemini leak diagnosed + closed at the routing layer, Option A hard-cutover model locked in (#301) |

## What's left until real users on V2 (per 2026-06-08 EOD review)

| Owner | What |
|-------|------|
| **Sarvesh (biggest gate)** | Merge yral-mobile #1185 + #1186 → build alpha APK → upload to Play Store alpha track |
| **Dev session overnight** | 21αβ.H1 Option A runbook, 21αβ.H11 cost alerting, 21αβ.H12 multimodal routing, 21αβ.I-Mig items |
| **YRAL team** | Install alpha APK + run 8-section test (D+1 to D+7) |
| **Dev session days 2-7** | Server-side billing (H2), failover drills (H4+H5), restore drill (H6), Phase 24 security (H8), DB rotation (H9), dashboard (H10) |
| **You** | Review 2-3 design docs when dev session opens them, take cutover-day pg_dump, tell Sarvesh "go" for prod-track submission |
| **App store approval** | ~2-7 days (mostly App Store) |
| **Real users see V2** | ~D+17 (realistic) |

See `Phase 21α / 21α→β / 21αβ.I / 21β / 21γ` tables below for full detail.

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

## PHASE 21α: ALPHA CUTOVER (Play Store alpha-track for internal YRAL team)
**Model CLARIFIED 2026-06-08 by Rishi:** Alpha = the YRAL Alpha Play Store track (app ID `4975001505184260102`), visible to internal YRAL team only. Real users use the prod-track app (`4974628203228829567`) — that's Phase 21β. Cutover means PR #1186 (CHAT_BASE_URL = agent.rishi.yral.com) + PR #1185 (SoulFileCoachEnabled flag default fix) merge → Sarvesh builds alpha APK → uploads to Play Store alpha track → internal team installs via Play Store update → team runs 8-section test plan → satisfied → Phase 21β. **12-section Motorola smoke test ALREADY PASSED 2026-06-08** with all 6 v2 flags overridden ON against agent.rishi.yral.com — empirical alpha-readiness evidence.

See memory: `project_cutover_phases_alpha_and_production.md` (original) + `project_cutover_model_clarified_2026_06_08.md` (clarification + 5-min lag requirement at 21β).

| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 21α.0 | **Auto-deploy from CI build to swarm service.** Surfaced 2026-06-02: PR #241 merged + image built by CI but swarm spec never updated → required a manual `docker service update --image` step to roll out. Hard prerequisite for alpha — hotfix during alpha cannot require manual SSH. Options: (a) CI deploy-job that SSHes from self-hosted runner / OIDC and runs `docker service update`; (b) Watchtower-style image-pull service on rishi-4 polling GHCR `:main` tag; (c) GHCR push webhook into rishi-4 endpoint. | ⏳ Pending | 1 | — |
| 21α.A1 | Mobile gate: all 6 v2 feature flags exist in `ChatFeatureFlags.kt` with `defaultValue=false` (`H2hChatEnabled`, `AudioRecordingEnabled`, `SoulFileCoachEnabled`, `SseStreamingEnabled`, `ChatAsHumanCreatorEnabled`, `VideoIdeasEnabled`) | 🔄 5/6 — SoulFileCoachEnabled regressed to `true` in PR #1180; fix PR #1185 open with Sarvesh. Flips ✅ once #1185 merges | — | yral-mobile #1185 |
| 21α.A2 | Mobile gate: 6 feature PRs Sarvesh-reviewed + merged to mobile main | ✅ Done 2026-06-08 — #1178 H2H + #1179 Audio + #1180 Coach + #1181 Auth + #1182 Coach entry + #1183 Banner fix + #1184 Video Ideas all merged | — | yral-mobile |
| 21α.A3 | Mobile gate: PR #1186 (CHAT_BASE_URL = agent.rishi.yral.com) + PR #1185 (Coach flag default fix) merge → Sarvesh builds alpha APK + uploads to Play Store alpha track → Firebase Remote Config audience targeting for the 6 v2 flags = `true` for alpha audience | 🔄 Both PRs open with Sarvesh. PR #1186 is the actual cutover commit (1-line CHAT_BASE_URL change, pre-commit hook bypass legitimate per Rishi authorization). Pre-flight smoke test already passed 2026-06-08 — once Sarvesh merges, the path is mechanical | 0.5 | yral-mobile #1185 + #1186 |
| 21α.B1 | Backend gate: Full Motorola test of all 30 chat-ai-parity endpoint shapes on V2 (Phase 1.16 promoted) | ⏳ Pending | 0.5 | — |
| 21α.B2 | Backend gate: Latency comparison V2 vs chat-ai; 50%-faster target met per CLAUDE.md rule 6 (Phase 1.17 promoted) | 🔴 RED — DEV-11: chat-send 9% faster (not 50% — both backends Gemini-bound, structural ceiling); inbox-list **2× SLOWER** (904ms vs 427ms p50). DEV-11b root cause: stale planner stats post-2026-06-04 failover (`pg_stat_user_tables` shows `n_live_tup=2` reality 286k, `last_analyze` NULL). Pending: Rishi-authorized `ANALYZE conversations; ANALYZE messages; ANALYZE ai_influencers; …` (≤2 min, ACCESS SHARE lock) → expected inbox-list p50 904ms → ~300-400ms, flips inbox-list 🟢. **chat-send 50% target structurally unachievable on shared Gemini provider** — recommend rule re-interpretation (measure SSE TTFT or non-LLM-overhead) before α | 0.5 | — |
| 21α.B3 | Backend gate: Continuous ETL loop re-enabled + first delta-sync verified clean (Phase 1.14a promoted) | 🔄 Off post-emergency | 0.5 | — |
| 21α.B4 | Backend gate: Phase 2.V1 Langfuse traces verified arriving | 🟢 GREEN — DEV-5: 404 successful ingestion POSTs (HTTP 207 Multi-Status) in last hour, zero error/warn lines. ~7 batches/min throughput. All 4 trace_generation call sites confirmed instrumented in `app/services/ai_client.py`. Manual UI verification a 2-min exercise during meeting if wanted | — | — |
| 21α.B5 | Backend gate: Phase 2.V2 Redis WS pub/sub verified (not local fallback) | 🟢 GREEN — DEV-6: 8 subscribers active (4 workers × 2 replicas on .1/rishi-5 + .2/rishi-4), zero "Redis not available — local-only" fallback log lines. Live cross-node propagation test deferred to β chaos suite | — | — |
| 21α.B6 | Backend gate: Phase 19.2 cost circuit breaker landed (still have $400-incident memory) | 🔄 DRAFT PR #289 — service layer complete + 4 review questions answered via `docs/cutover-audits/DEV-12-PR-289-prep.md`. **α gate added (21α.B6a below):** PR #289 explicitly defers `CostCeilingExceeded → 402` wiring in `chat.py` to follow-up — without it, breaker detects but route does not refuse. Must land before α | 1 | #289 |
| 21α.B6a | **α gate (added 2026-06-05 from DEV-12 review):** Wire `CostCeilingExceeded → 402` in `app/routes/chat.py` after #289 service layer merges. Without this, the breaker is half-shipped — detects but doesn't enforce at the user-facing endpoint. ~15 LOC + 1 test | ⏳ Pending — depends on #289 merge | 0.25 | — |
| 21α.B7 | Backend gate: Phase 1.14 ETL completeness re-verified (re-bootstrap PR #227 closed gap 2026-06-04) | ✅ Done | — | #227 + sidecar pg16 manual op |
| 21α.C1 | Risk audit gate: Push notifications (chat-ai's `services/push_notifications.py`) ported + fire-tested on V2 — high-risk silent-inbox failure if missing | 🟢 GREEN — DEV-1 + mobile expert K answer 2026-06-08: Android `NotificationHandler.kt` routes only on `VideoUploadedToDraft` + `RewardEarned`. `data.type` divergence (`"chat_message"` vs `"new_message"`) doesn't affect routing today. Backend keeps `"chat_message"`. Recommend standardizing on it if chat-push-driven inbox refresh is added later | — | — |
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
| 21α.E1 | Smoke test: Mobile expert builds APK with CHAT_BASE_URL=agent + all 6 flags ON | ✅ Done 2026-06-08 | — | — |
| 21α.E2 | Smoke test: Rishi runs smoke matrix on Motorola — every test must pass | ✅ Done 2026-06-08 — **12/12 sections passed clean** (A AI chat + SSE, B H2H, C audio recording + transcription, D G6 mic-hide, E1 CreatorTakeoverBar, E2 takeover toggle, F Coach button, G Coach end-to-end, H Create CTA stable, I1 Video Ideas tab + 5 ideas, I2 Create handler + toast nav, J human profile = 2 tabs). Rishi noted: Coach still needs polish (tracked as mobile expert (a) follow-up) | — | — |
| 21α.F1 | T-0: Firebase Remote Config push (CHAT_BASE_URL + 6 flags ON) | ⏳ Pending | — | — |
| 21α.F2 | T+1h watch: Sentry error rate stable; kill-switch any spiking process | ⏳ Pending | — | — |
| 21α.F3 | T+24h: Clear → 7-day alpha soak begins | ⏳ Pending | 7 | — |
| **Phase 21α total** | | **Mobile gate cleared (12/12 smoke test passed 2026-06-08). 12 overnight audits closed: 9 🟢 / 2 🟡 / 1 🔴 (DEV-11 latency — cheap fix queued).** Remaining α gates: 21α.A1+A3 PR #1185+#1186 Sarvesh merge → alpha APK build → Play Store alpha track upload → team install. The handful of pending backend items (21α.B2 ANALYZE, B3 ETL re-enable, B6+B6a cost breaker, C3 Ansuman recsys, D1 snapshot, F1-F3 cutover ops, plus 21α.0 auto-deploy if hotfix needed) can proceed in parallel with the alpha team test cycle — they don't block the alpha-track upload itself. | **~1 day prep + N day team test** | |

## PHASE 21α→β: V2 HARDENING WINDOW (between alpha-satisfied and prod-submission)
**Established 2026-06-08 by Rishi:** "We just need to make sure that the API is robust, we have failover mechanisms, nothing can go wrong … say max we should lose just 5 minutes of data from chat-ai when it finally goes onto [prod] before real users start using our V2 version." Items previously tagged as β-only now become **prod-cutover prereqs** — they must land before real users hit V2.

See memory: `project_cutover_model_clarified_2026_06_08.md`.

| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 21αβ.H1 | **Option A — hard cutover + mini re-bootstrap** (Rishi 2026-06-08, after dev session's G measurement showed ~20.5k message gap since the 2026-06-04 re-bootstrap). Continuous ETL stays OFF — skip the 1.5-day re-enable work + avoid reviving the orphan-bug code path that produced 8,932 orphans on 2026-06-04. Instead: schedule cutover at a low-traffic moment (Sunday 3am IST or similar), hard-switch via Caddy returning "service moved, restart app" on chat-ai → forces all clients to refresh Firebase + pick up new URL immediately. Then mini re-bootstrap captures the frozen chat-ai state. ~30 min wall, ~2 min DB apply, proven mechanism (mirror of 2026-06-04 re-bootstrap). Brief user-visible "restart app" at cutover is the accepted trade-off vs the original "≤5 min lag" framing. **Action items:** (1) Caddy hard-cutover snippet ✅ embedded in runbook, (2) mini re-bootstrap runbook ✅ `docs/runbooks/cutover-day-mini-rebootstrap.md`, (3) confirm cutover-day window with team (Rishi-driven). | 🔄 Runbook ready — awaiting cutover-day window confirm | 0 left (runbook done; execution = 30-min window) | — |
| 21αβ.H2 | DEV-3 follow-through: server-side billing paywall enforcement on V2 (~150 LOC, leverages DEV-12's Redis substrate). Was β-only; now PROD BLOCKER per Rishi 2026-06-08 — motivated user on prod bypasses mobile gate → unbounded Gemini cost. | ⏳ Pending — PROD BLOCKER. **Brief drafted 2026-06-11 at `~/.claude/plans/h2-server-side-billing-paywall-brief-2026-06-11.md`.** Dev session picks up after Coach PR-3 + Bucket 2 ship. 3-PR structure: billing_client + caching → /messages route → /messages-stream + /images routes. | 2 | — |
| 21αβ.H3 | Auto-deploy mechanism (21α.0 promoted) — GitHub Actions Deploy + Rollback buttons (#294) → Path 1 auto-deploy on merge with workflow_run + auto-rollback on `/health` failure + concurrency lock (#297, #298). Matches chat-ai's deploy-baremetal.yml pattern PLUS auto-rollback chat-ai doesn't have. First end-to-end auto-deploy 2026-06-08 (#298 merge). | ✅ Done | — | #294 + #297 + #298 |
| 21αβ.H4 | Patroni failover drill — live test of leader promotion under simulated load. Required per "robust + failover-ready" mandate. | ✅ Done 2026-06-13 — drill executed live during pre-flip window. Failover patroni-rishi-5 → patroni-rishi-4 took 6.6s, longest contiguous `/health` disruption ~5s (under 30s soft threshold). Timeline 29 → 32. Cluster healthy post-drill with leader=patroni-rishi-4. Switchback to patroni-rishi-5 rejected by Patroni `SYNCHRONOUS_MODE_STRICT` (rishi-5 became async replica post-failover, not sync standby); left at patroni-rishi-4 leader since cluster is healthy. Real finding queued as 21γ.P17: ~30s of intermittent curl timeouts AFTER failover (asyncpg pool stabilization). Pre-drill pg_dump 552MB SHA `97fb32c5...`. PRs #383 (script + runbook). | — | #383 |
| 21αβ.H5 | Redis Sentinel failover drill — kill primary, verify subscriber reconnect + WS pub/sub recovery. DEV-6 noted as β follow-up; promoted. | ✅ Lite verification done 2026-06-13 — `SENTINEL CKQUORUM` returned "OK 3 usable Sentinels. Quorum and failover authorization can be reached" + master `redis-primary:6379` healthy (last-ok-ping 967ms) + 3 sentinels visible (rishi-4/5/6) + 1 replica visible. Topology is proven configured correctly + failover machinery is authorized. Full break-it-to-test-it drill (docker stop primary + verify WebSocket pub/sub recovery) deferred to post-rollout stable window as 21γ.P18. PR #384 (script + runbook ready to execute). | — | #384 |
| 21αβ.H6 | **PROMOTED TO PROD BLOCKER 2026-06-08** — WAL-G restore drill: spin up ephemeral Postgres on a side VM, restore from WAL-G S3 archive, verify all data present, document exact commands so an emergency doesn't require figuring out the mechanism on the fly. WAL-G is streaming (verified 2026-06-04), but we've never actually restored. The 2026-06-04 re-bootstrap showed how painful "figure it out during the incident" can be — same risk applies to backup-restore. Must validate the safety net before real users hit prod. | ✅ Done 2026-06-11 — drill #4 on rishi-6 GREEN end-to-end: 3,941 ai_influencers + 287,183 conversations + 3,460,303 messages restored from S3 with 10-min WAL lag. Workflow + script + runbook at `.github/workflows/walg-restore-drill.yml` + `scripts/walg_restore_drill.sh` + `docs/runbooks/walg-restore-drill.md`. PRs #346 + #347 + #353 + #354 + #355 (4 iterations to clean). | — | #346, #347, #353, #354, #355 |
| 21αβ.H7 | DEV-10 dep bumps — pyjwt 2.10.1 → 2.13.0 + python-multipart 0.0.20 → 0.0.27. Verify Caddy `request_body_max_size` is set (DEV-10 flagged as suspect). Defer starlette to FastAPI bump PR. | ✅ Done 2026-06-13 — #363 merged 09:07Z (smoke green 09:12Z) → #364 merged 09:12Z (smoke green 09:15Z). Live image `43bdd77` carries both security-patched deps for the 21β 10% rollout. Caddy `request_body_max_size` verification + starlette/FastAPI bump tracked as separate hygiene items if needed. | — | #363, #364 |
| 21αβ.H8 | Phase 24 security drills promoted from β: 24.1 gitleaks CI workflow on every PR (baseline done in DEV-7), 24.2 weekly automated safety drill, 24.3 dep CI (pip-audit + Trivy), 24.4 rotation runbook. | ⏳ Pending | 5 | — |
| 21αβ.H9 | DATABASE_URL secret rotation (was I13 + 21α.S2, no longer dedicated to its own session since the audit transcript that leaked it is months old, but still real prereq). | ⏳ Pending — DEDICATED SESSION | 0.5 | — |
| 21αβ.H10 | Phase 19.6 dashboard additions to cover the new prereqs: ETL lag tile + cost-breaker activations tile + last-failover-drill timestamps. ADHD-observability baseline per `feedback_adhd_observability_and_security_baseline.md`. | ⏳ Pending | 1 | — |
| 21αβ.H11 | **NEW PROD BLOCKER 2026-06-08** — Real-time LLM cost alerting. The $22 quality_scorer leak today was caught by Rishi happening to check Google Cloud billing 4 days later. At prod scale that's a $400 incident. **3 alerts to wire:** (1) Sentry alert when Gemini hourly cost > $X threshold (default: $1/hour). (2) Sentry alert when any async process logs a non-200 LLM response in the last 5 min (separate from the existing leak guard — this catches RUNAWAY error spend, not just gemini-leak spend). (3) Daily 08:00 IST email digest: yesterday's cost broken down by process + provider, sourced from `llm_costs` table. Hooks into existing Phase 25.5 substrate. | ✅ Done 2026-06-09 — shipped via #306 (`feat(cost-alerts): 21αβ.H11 — real-time LLM cost alerting (3 alerts)`). All 3 alerts live: Gemini hourly threshold (Sentry), async non-200 LLM detector (Sentry), daily 08:00 IST email digest. Confirmed by dev session 2026-06-13. PROGRESS.md was stale; corrected. | — | #306 |
| 21αβ.H12 | **NEW PROD BLOCKER 2026-06-08** — Image/multimodal LLM routing fix. **Real bug surfaced today:** when Rishi flipped user_chat_main → runpod_vllm via dashboard, chat messages with image attachments silently failed (Saikat's pod is text-only). Same pattern as audio is already split as a separate routable process; vision needs the same treatment. **Implementation:** (1) Add new process `user_chat_main_multimodal` to PROCESS_NAMES + LLM_DEFAULTS, default gemini, no fallback to non-vision providers. (2) Add `supports_vision: True/False` capability flag to PROVIDERS — gemini=true, openrouter=true, runpod_vllm=false, internal_vllm=false. (3) `upsert_override()` capability check refuses to flip user_chat_main_multimodal to a non-vision provider (dashboard shows error like the existing audio_transcription guard). (4) chat-send detects images in messages → routes via user_chat_main_multimodal instead of user_chat_main. Text-only stays on user_chat_main. **After this:** flip user_chat_main (text) to runpod for cost savings WITHOUT breaking image chats. They route independently. | ✅ Done via PR #312 (`feat(routing): split user_chat_main_multimodal for vision-bearing chat`). Verified live 2026-06-13: `/admin/llm-routing.json` shows `user_chat_main_multimodal → gemini` separate from `user_chat_main → gemini`. PROGRESS.md was stale; corrected. | — | #312 |
| 21αβ.H13 | **NEW PROD BLOCKER 2026-06-15** — Creator earnings write path missing. v2 has `creator_earnings` table (migration 005) + 3 READ endpoints (`GET /api/v1/creator/earnings`, `/earnings/by-influencer`, `/earnings/history`) wired in `app/routes/earnings.py`, but **ZERO write path** — no webhook receiver from billing.yral.com, no periodic sync job, zero `INSERT INTO creator_earnings` anywhere in app/. Migration 005 comment explicitly says "Aggregated from billing.yral.com events (webhook or periodic sync)" — the bridge was intended but never built. Production table is essentially empty (8 KB = Postgres block size minimum). **chat-ai NEVER had earnings tracking either** — verified 2026-06-15 by grepping yral-chat-ai repo: zero matches for `creator_earnings`/`wallet`/`payout`/`revenue`/`earnings`/`subscription`/`billing`/`payment` across all 40 .py files and 3 migrations. So creators have HISTORICALLY had no in-app visibility into subscription revenue. **Impact escalated 2026-06-14:** Sarvesh rolled v2 backend to 100% prod + Firebase Remote Config has `chat_subscriptionAllowedInfluencerId` set to a real influencer ID (`qi6gd-esmrx-v2oyd-7fwhm-ibfs5-trflm-xm3iy-xq6d3-3hmwu-jb7tk-5qe`) → real users CAN subscribe RIGHT NOW + their Rs 9 goes to billing.yral.com with NO creator-side visibility. **Confirm with Sarvesh before locking urgency:** (1) Has anyone EVER subscribed to a chat-ai influencer for money? (2) If yes, where does that money show up for creators today? (3) Is there a billing.yral.com creator dashboard outside the YRAL app entirely? **Fix paths (decision needed after Sarvesh):** (A) Build webhook receiver in v2 from billing.yral.com → creator_earnings. (B) Build periodic sync job pulling from billing.yral.com. (C) Have billing.yral.com directly write to creator_earnings (anti-pattern). (D) Document that wallet view is decorative/future + remove the route from mobile until backend ships. **Audit script to test the gap empirically:** Rishi subscribes to Anastasia from his Motorola → check wallet view → should show ₹0 / no earnings → confirms diagnosis. Picked up when bandwidth allows per Rishi 2026-06-15. | unknown until Sarvesh check — 1-3 days build once decided |
| **Phase 21α→β total** | | **Started 2026-06-08. H3 (auto-deploy) ✅ done. 12 sub-phases remaining (H1-H2, H4-H13 except already-done H6/H7/H11/H12).** | **~9-12 days** | |

## PHASE 21αβ.I: PRODUCTION-GRADE SAFETY (industry-standard guardrails before real users)
**Established 2026-06-08 by Rishi** after the question "are we doing CI/CD right by industry standards?" The 21α→β phase above covers operational hardening (failover drills, ETL, secrets). This phase covers the safety guardrails that go around every code change + every deploy. Each item is small (~30 min to 2 hours) and independent — can ship in any order, one PR each.

**Three groups:** Security checks, Migration safety, Deploy safety. Pick from any group; nothing blocks anything else.

| # | Sub-phase | Group | Status | Est. effort |
|---|-----------|-------|--------|-------------|
| 21αβ.I-Sec1 | gitleaks in CI — fails PR if a new secret is introduced; 4 DEV-7 baseline FPs allowlisted | Security | ✅ Done 2026-06-08 (PR #300) | — |
| 21αβ.I-Sec2 | pip-audit in CI — fails PR on new P0 vulns; 14 DEV-10 baseline ignored | Security | ✅ Done 2026-06-08 (PR #302) | — |
| 21αβ.I-Mig1 | Automated pre-migration pg_dump — wrap migration runner in a script that always takes a snapshot first. Replaces manual "Rule 9" with automation so we can't forget | Migration safety | ✅ Done 2026-06-09 (PR #309 + #326+#328 awscli + #330 rolling-update + verified on 033/034) | — |
| 21αβ.I-Mig2 | Migration linter (squawk or similar) — fail PRs that add dangerous patterns (DROP COLUMN, ALTER COLUMN ... NOT NULL without backfill, etc.). Forces backwards-compatible migrations only | Migration safety | 🔄 Partial 2026-06-09 (squawk wired + lock_timeout/statement_timeout enforced; expand to more rules) | 1 hr remaining |
| 21αβ.I-Mig3 | Migration testing in CI — spin up ephemeral Postgres, run all migrations, verify they succeed. Catches syntax errors before they hit prod | Migration safety | 🔄 Partial 2026-06-09 (`Apply all migrations in order` test live; expand to idempotency + down-migrations) | 1 hr remaining |
| 21αβ.I-Mig4 | Migration runner hardening — auth, defensive checks, bootstrap workflow, rolling-update workflow for the patroni image. Earned during the 2026-06-09 #314 incident. | Migration safety | ✅ Done 2026-06-09 (PRs #323 + #324 + #325 + #326 + #327 + #328 + #329 + #330 + #331 + #332) | — |
| 21αβ.I-Dep1 | Tag `:stable` in GHCR after every successful deploy — known-good marker we can always pin to | Deploy safety | 🔄 Wired 2026-06-08 (PR #303) but step has been failing on every deploy with "installation not allowed to Write organization package" — needs GHCR token scoped to `write:packages` at org level OR remove the step in favor of commit-SHA tags only | 1 hr |
| 21αβ.I-Dep2 | Post-deploy smoke test workflow — runs the 24/24 endpoint script automatically after every successful deploy. Catches "service is up but routes are broken" | Deploy safety | ⏳ Pending | 1 hr |
| 21αβ.I-Dep3 | Read-only SSH user (`rishi-readonly`) on rishi-1/2/3/4/5/6 with `command=` restriction in authorized_keys → can read logs, can't write. Restrict `rishi-deploy` to CI only. Documentation in CLAUDE.md | Deploy safety | ⏳ Pending — needs Rishi review of design | 1 day |
| **Phase 21αβ.I total** | | | **4/9 fully done + 3 partial 2026-06-09 (Sec1 + Sec2 + Mig1 + Mig4 complete; Mig2/Mig3/Dep1 partial). 2 remaining: Dep2 + Dep3.** | **~1.5 days remaining** |

## PHASE 21γ: POST-CUTOVER POLISH (good-to-have, NOT blocking real-user launch)
**Established 2026-06-08 by Rishi** after his "what would the best developer in the world add to the cutover plan?" question. Session 6 identified 9 items the best developers would recommend; Rishi accepted 2 as PROD BLOCKERs (now 21αβ.H11 cost alerting + 21αβ.H6 promoted restore drill) and notes the rest below as **post-cutover polish — important but not blocking real users on prod**.

These are listed so they're tracked, not so they're done before real users arrive. Pick one at a time after cutover when bandwidth allows.

| # | Sub-phase | Why nice-to-have | Est. effort |
|---|-----------|------------------|-------------|
| 21γ.P1 | Load test before peak traffic shifts — k6/locust script simulating 100 concurrent chat sessions + chaos test (kill rishi-5 mid-load) | We don't know V2's behavior at peak load. Catches "melts down at cutover moment" risk. Bigger concern post-cutover when real-user traffic grows. | 1 day |
| 21γ.P2 | Caddy rate-limit + body-size cap on public endpoints | Defense against random internet flood + Gemini-key-throttling-by-stranger. Already partially mitigated by per-user rate limits (Phase 19.1) — those need a JWT, so pre-auth flood unprotected. | 2 hr |
| 21γ.P3 | AI disclosure mechanism — first-message "you're talking to AI" + "AI" badge in mobile profile + ToS update | Legal requirement in some jurisdictions (EU AI Act, California SB-942). Pre-cutover the only real users are YRAL team, who know it's AI; matters more post-cutover. | 1 day |
| 21γ.P4 | Staging environment (`yral-rishi-agent-staging` swarm service, separate `staging` DB snapshot) | Lets us test changes against real-world data shape without risking real users. Auto-rollback already covers in-production failure; staging is a SECOND safety layer. | 2 days |
| 21γ.P5 | Incident response playbook — document top 5 failure modes with exact 3-step recovery for each, pin to `docs/INCIDENTS.md` | When something breaks at 3am and Rishi is asleep, no one currently knows the procedure. Documentation surfaces knowledge from heads/context. | 1 day |
| 21γ.P6 | User data deletion endpoint — `DELETE /api/v1/users/me` nukes user's data across all tables + audit log | GDPR/CCPA compliance + first-user "delete my data" request. Pre-cutover: manual SQL works for tiny YRAL-team scale; post-cutover: needs to be self-serve. | 4 hr |
| 21γ.P7 | Status page (lightweight) — `status.agent.rishi.yral.com` static page on Caddy OR free hosted service (betterstack, instatus) | When V2 is down (it will happen), users need somewhere to see "we know, working on it" — otherwise they uninstall. Mobile app could surface status via a banner. | 3 hr |
| 21γ.P8 | Split `ai_influencers.category` (display taxonomy) from a new `archetype` column (personality key for `ARCHETYPE_PROMPTS`) + backfill + Coach edit hook | Today `category` does double duty: catalog display label AND archetype selector. Anastasia's `category="Culture & Arts"` → no L2 archetype block at chat time (only 5 keys match). Surfaced 2026-06-12 via the new system-prompt-preview page. Clean fix is a separate `archetype` column so both jobs get their own field. | 1-2 days |
| 21γ.P9 | TZ audit — `user_skill_state.preferred_times` ("21:00") → `next_event_at` (UTC TIMESTAMPTZ) conversion at onboarding | If the local→UTC conversion is wrong, skill check-ins fire at the wrong wall-clock time for the user. Not actively biting (Nutrition Coach is Kareena-dogfood only), but a real foot-gun once any skill goes to a real user pool. Read-only audit of the onboarding code path + fix-if-broken. | 30 min audit + fix-if-needed |
| 21γ.P10 | Drain endpoint refactor — return 202 + job ID + separate poll endpoint instead of blocking until Phase 2 completes | `POST /admin/etl/drain` currently blocks until importer drains + all 3 integrity layers refresh. Phase 2 routinely exceeds Cloudflare/Caddy's ~100s edge timeout → user gets HTTP 524 while the server keeps working. Surfaced 2026-06-12 testing the endpoint end-to-end against deployed v2 service. Refactor: route returns 202 + drain_id immediately, drain runs as background task, `GET /admin/etl/drain/{drain_id}` returns status + report when ready. Schema migration to `TIMESTAMPTZ` on `etl_processed_files.processed_at` + `etl_integrity_results.verified_at` to eliminate the tz-juggling we now do at every call site can ride along. | 1-2 days |
| 21γ.P11 | Audit the +64K v2-vs-chat-ai message gap | At 2026-06-12 12:20 hourly integrity tick v2 reported 3,469,214 messages vs chat-ai's 3,404,927 — v2 has +64,287 (~1.85%). Breakdown so far: 16,601 explained by `is_proactive`/`is_nudge`/`is_human_creator_takeover`/role=system flags; ~27,000 explained by re-bootstrap PR #227; ~50 by watermark timing gap. ~20,636 still unexplained (probably chat-ai TTL/soft-deletion + bot replies generated on v2 that weren't echoed back). Read-only audit to time-bucket the diff + confirm. Not data loss (v2 has MORE not less); want to close the books before declaring "fully reconciled." | 1-2 hr audit |
| 21γ.P12 | Fix reconciliation key-name bug — `_chat_ai_counts_from_hourly_payload` looks for `chat_ai_count` but integrity payload uses key `chat_ai` | `GET /admin/etl/reconciliation` returns `INVESTIGATE` forever because the helper can't extract chat-ai row counts from the integrity verifier's actual payload format. ~3-line fix in `app/services/etl_drain.py`. Catches up the verdict tooling with the verifier output. Should NOT have shipped while sentinel + heartbeat already prove data flow — this is verdict-display polish. | 1 hr |
| 21γ.P13 | Sentry context polish — add `bot_id` + `conversation_id` tags at chat-send, chat-v2, coach route entries | Today every authenticated Sentry event has `user.id = <principal>` but no bot/conversation context. Filtering "all errors for Anastasia" requires grepping stack traces. ~15-line change across 4 routes. Free Sentry-query power-up. | 30 min |
| 21γ.P14 | Sentry context for background tasks — wrap proactive/nudge/ETL loop iterations with `set_user`/`set_tag` per row | Background work surfaces in Sentry without per-user/per-bot context because the loops process many in one go. Errors look "global" when they were specific to one user-bot pair. Wrap each iteration with a Sentry scope, clear at end. ~50 lines spread across `proactive.py`, `nudge.py`, ETL services. | 4-6 hr |
| 21γ.P15 | Image-gen monetization strategy + paywall (post-analytics decision). Default recommendation: 3 generations / day free tier with soft paywall above ("you've used today's free images. Upgrade for unlimited, or wait 24h"). Implementation mirrors H2 paywall pattern — `image_generation_quota` table + check on existing `/chat/conversations/{id}/images` route + 402 response + mobile UI for upgrade prompt. **DECISION GATED on Phase 26 View 7 economics**: do not ship until analytics shows whether image gen is a discovery feature (used in first 5 messages — DO NOT gate) or a depth feature (used after message 30 — safe to gate). Pre-PMF gating is the classic mistake. See 2026-06-13 conversation for the strategic push-back. | Decision: 0 (waits on data). Implementation: 1-2 days when called. |
| 21γ.P16 | Mobile nav-bar swap — Discover Influencer becomes position-1 (default landing tab); Home Feed moves to position-2. Pure UI change, no agent endpoints touched, no flag-gating required. Brief sent to mobile expert 2026-06-13. Standing rule: Rishi Motorola pass before PR opens; PR holds until rollout window closes. | 2-3 hr mobile work |
| 21γ.P17 | Investigate post-Patroni-failover connection pool stabilization window. H4 drill (2026-06-13) showed: failover itself took 6.6s with ~5s contiguous disruption (within 30s soft threshold), but for ~30s AFTER cluster was already healthy, the agent service had intermittent curl timeouts (6 single-probe failures scattered among ~20 successes). Likely cause: asyncpg connection pool slowly draining cached connections to the OLD leader; the multi-host DSN with `target_session_attrs=read-write` correctly fails-over for NEW connections but cached pool connections take time to recycle. Investigate tuning `pool_recycle_time`, adding explicit pool drain on Sentry-detected DB error spike, or pgbouncer-style external pooling. | 2-3 hr investigation + tune |
| 21γ.P18 | Full H5 Redis Sentinel failover drill — `docker stop redis-primary` + watch Sentinel promote replica + verify pub/sub via WebSocket tracer + restart. H5 LITE verification done 2026-06-13: `SENTINEL CKQUORUM` returned "OK 3 usable Sentinels. Quorum and failover authorization can be reached" + master `redis-primary:6379` healthy + 3 sentinels visible + 1 replica visible. Topology proven configured correctly; full break-it-to-test-it drill deferred to post-rollout stable window. Also worth noting: 1-primary-1-replica is tighter than canonical 1+2 — after a real failover we'd be at 1+0 until restart, worth thinking about for infra capacity. | 0.5 day for full drill + WS tracer setup |
| 21γ.P19 | Patch analytics `config.HEADLINE_TOKEN` to read from `/run/secrets/HEADLINE_TOKEN` file (mirroring the file-first pattern `database.py` already uses), so the `docker service update --env-add HEADLINE_TOKEN=<value>` workaround can be dropped. | ✅ Done 2026-06-14 — analytics repo PR #4 added `_secret()` helper that reads `/run/secrets/<key>` first, env fallback; `HEADLINE_TOKEN = _secret("HEADLINE_TOKEN")`. Workaround removed via `docker service update --env-rm HEADLINE_TOKEN` after #4 deployed. `/headline` now reads token from Swarm-mounted secret file as designed. | — | analytics #4 |
| 21γ.P20 | Make first-deploy of analytics not crash on `/headline` — at startup the hourly refresh job hasn't fired yet, so `analytics.analytics_sessions` view doesn't exist and the route raises `UndefinedTableError`. Bootstrap-refresh on lifespan startup OR expose `/admin/refresh-sessions` POST. | ✅ Done 2026-06-14 — analytics repo PR #4 added startup table-ensure + "warming-up" friendly response so a fresh deploy doesn't 500 before the first refresh tick fires. | — | analytics #4 |
| 21γ.P21 | **VERIFY H11 cost alerting actually fires.** 2026-06-14 H2 was closed as WON'T FIX based on B6 + H11 + 19.1 being the existing safety net for the "motivated bypass user → unbounded Gemini cost" risk. H11 cost alerts (shipped 2026-06-09 via #306) are now load-bearing for this decision. Confirm: (1) Sentry alert config exists for hourly Gemini cost threshold, (2) trigger a synthetic spike — e.g. temporarily lower threshold to $0.01 + fire a few Gemini calls — and verify the Sentry alert lands, (3) confirm daily 08:00 IST email digest fires + reaches Rishi's inbox. End-to-end proof, not just "we shipped the code." Without this verification we're trusting code we haven't tested. **Important.** | 1-2 hr | #306 (the H11 shipping PR — verify still wired) |
| 21γ.P22 | **Decommission old Metabase on rishi-2** — 2026-06-15 new Metabase migrated to rishi-6 / `metabase.rishi.yral.com` reading from v2 Postgres via `metabase_ro` role. Old Metabase on rishi-2 (chat-ai cluster, H2 file backend) still running untouched as safety net. After ~1 week of confidence: (1) confirm all dashboards Rishi cared about are rebuilt on new Metabase, (2) `docker stop` old Metabase container on rishi-2 + remove its old Caddy stanza (the chat-ai-targeted one that pre-dated the migration), (3) keep H2 file backup of old Metabase application DB for 30 days then delete. Zero impact to chat-ai (Metabase is read-only analytics, never on the chat path). | 0.5 hr |
| 21γ.P23 | **Metabase 100X Tier 1 — do this week.** (a) Set up 5-7 critical alerts on Founder Pulse + System Health cards: DAU drops 30% vs 7d avg, LLM rejection rate > 2%, ETL lag > 5 min, daily cost spike > 50%, NSFW share > 60%, daily messages drop > 25%, top error count > 100/hr. (b) Daily email subscription for Founder Pulse delivered 09:00 IST — Rishi's morning ritual. Requires SMTP wired in Metabase admin if not already (Postmark or Sendgrid). (c) Add Date Range + Influencer dropdown filters to Conversation Quality + Bots & Quality dashboards — turn static charts into investigation tool. (d) Build 3 Metabase Models: `Enriched Messages` (messages + conversations + ai_influencers join pre-built), `Active Influencer Stats` (bot + latest quality score + msg counts), `User Lifetime` (per-user roll-up). After Models land, Neha + other viewers can self-serve via GUI builder instead of waiting for SQL. | 4 hr total |
| 21γ.P24 | **Metabase 100X Tier 2 — do this month.** (a) Add Sentry as a second data source — Metabase can query Sentry's Postgres backend OR API. Build "Errors + Sessions × Product Data" combined dashboard. (b) Add Langfuse as a third data source — dedicated LLM Quality dashboard with prompt-response pairs + latency + eval scores. (c) Build 3 more domain-specific dashboards: Coach Effectiveness (suggestion adoption, sections changed/session, wizard drop-off), Skills Framework (skill trigger rates, completion, skill chain success), Multimodal Usage (image-gen adoption, audio %, mixed-modality conversations). (d) SQL snippets — save common WHERE/JOIN fragments for reuse. (e) Caching strategy per dashboard — 5-min TTL on Founder Pulse + 1-hr on Retention to cut Patroni replica load. | 1-2 days total |
| 21γ.P26 | **Langfuse trace-level input/output propagation — SMALL fix.** Surfaced 2026-06-15 when Rishi opened Langfuse for first product tour. Diagnostic: trace-level `Input: null` + `Output: undefined` in every chat-response trace; child generation (`openrouter/google/gemini-2.5-flash`) has FULL data — Input = user message, Output = AI reply, latency_ms 612.4, provider, model, cost, tokens all populated correctly. Generation instrumentation is solid; only the trace summary roll-up is missing. Fix: in chat endpoint (`app/routes/chat.py` or wherever `langfuse.trace()` is called), after the LLM call returns, call `trace.update(input=user_message, output=ai_reply)`. ~15 LOC change. Without this, Langfuse Traces table shows empty Input/Output columns making product review impossible at the trace level — viewers have to drill into the child generation for every single trace, which kills the workflow. Confirmed working: user_id, conversation_id, tokens, cost, provider, model, latency — all present. Only trace-level rollup missing. | 1 hr | 🔄 In PR #394 (2026-06-16 overnight Task A; DRAFT pending Rishi review). Helper module choice (`app/services/langfuse_tracing.py`, not `chat.py`) per the brief's "or wherever langfuse.trace() is called" clause — codebase uses raw HTTP batch ingestion, not the SDK. ~2 LOC + 5 tests. Stacked on #393. |
| 21γ.P28 | **Kareena dogfood + nutrition_coach skill — finish tomorrow morning.** Surfaced 2026-06-15 evening when Rishi tried to create Kareena on mobile + hit generic "something went wrong" error. RCA: server logs show generate-prompt 200 + validate-and-generate-metadata 200 + Replicate avatar gen 201 ALL succeeded, but `POST /api/v1/influencers/create` never reached the server. DB confirms no Kareena row + no recent influencer for Rishi's principal (`7azwu...`). Per standing rule `feedback_when_mobile_error_disagrees_with_backend_state_its_mobile`: mobile error + backend never saw request = 100% mobile-side bug. Likely culprit: mobile waiting for Replicate avatar URL (async ~30-60s) before allowing submit. Tomorrow: Path A — retry on Motorola with 60-sec pause before submit + screen record for Sarvesh to debug the mobile wizard. If Path A retry succeeds, attach `skill_slug = 'nutrition_coach'` via SQL UPDATE on rishi-4 Patroni leader. If Path A fails twice, fall to Path B (direct INSERT bypassing mobile entirely). Then dogfood: send first message → Kareena asks 3 onboarding questions (nutrition goal + diet + check-in times) → answer → verify `user_skill_state` row written with `status='active'` → ongoing chat is nutrition-themed. Scheduled check-ins fire at preferred_times via `trigger_type=skill_nutrition_checkin`. Closes Phase 23.7 (Kareena dogfood gate). | 1 hr |
| 21γ.P29 | **Post-100%-prod safety net verification — URGENT.** v2 went 100% prod 2026-06-15 via Sarvesh's Firebase Remote Config flip. H2 paywall was closed WON'T FIX based on B6 (cost circuit breaker) + H11 (cost alerting) + 19.1 (per-user rate limit) trio being the safety net. **None of these have been synthetically triggered end-to-end with real users on the platform.** Three drills: (a) **H11 cost alert** — temporarily lower threshold to $0.01 + fire 5 Gemini calls + verify Sentry alert fires + daily 08:00 IST email digest reaches inbox. (b) **B6 cost circuit breaker** — simulate cost spike past threshold, verify circuit opens, verify subsequent LLM calls 503 instead of executing, verify automatic recovery after cooldown. (c) **19.1 per-user rate limit** — fire 100 messages in 60s from a test principal, verify rate limit kicks in (429 or message-blocked), verify user gets a friendly message rather than silent failure. Without these drills we're trusting code we wrote but never tested under real conditions. Cost runaway from a motivated bypass user could wipe out the Gemini budget overnight. | 2-3 hr |
| 21γ.P30 | **Daily founder rhythm — 5 min/morning, prevents flying blind.** Now that v2 is 100% prod, set up daily ops ritual: (a) Founder Pulse Metabase dashboard delivered as email at 09:00 IST. (b) Sentry weekly digest (already configured) + check for any P0 alerts overnight. (c) Cost circuit breaker + rate limit hot-edit dashboard glance. (d) Langfuse latest 20 traces eyeball scan — feel for product quality drift. (e) Once a week (Mondays), 30-min "state of YRAL" written self-review. **The discipline of seeing signals is what separates founders who catch problems early from those who learn from users complaining.** Pairs with `21γ.P23` Metabase 100X Tier 1 (subscription/alert setup). | 30 min setup + 5 min/day forever |
| 21γ.P31 | **Mobile crash + error telemetry audit.** Surfaced 2026-06-15 when Kareena create silently failed mobile-side. The mobile app's "something went wrong" suggests there's no mobile-side crash reporting (Crashlytics / Sentry mobile / Bugsnag) wired up, OR if wired, no one watches the dashboard. At 100% prod, silent mobile bugs erode UX without any signal reaching us. Action: ask Sarvesh (1) is Crashlytics/Sentry-mobile wired into both alpha + prod-track APKs? (2) if yes, where's the dashboard? (3) if no, when can he wire it? Should NOT block product work but should be tracked as a gap. Without this, our entire post-flip operational visibility is server-side only — half the system is dark. | Coordination — depends on Sarvesh |
| 21γ.P27 | **Langfuse onboarding stack** for Rishi to use Langfuse like best-in-the-world. Sequenced: (a) Tag every trace with `bot_id` + `is_nsfw` + `user_segment` (small instrumentation PR, ~30 LOC in chat endpoint metadata). (b) Pipe `bot_quality_scores` results into Langfuse via `langfuse.score()` API — currently scores live only in Postgres, never surface at trace level. (c) Build a Golden Set dataset — 30-50 representative prompts (short greetings, deep emotional, NSFW edge cases, prompt-injection attempts, multilingual) — re-run nightly against current production prompts with Gemini-as-judge scoring. (d) Move Soul Files (system prompts) from Postgres into Langfuse Prompts — version-controlled, hot-swap, A/B testable. (e) Daily 10-min annotation ritual — Rishi reviews 5 worst-scored traces, annotates good/bad. Builds training data + product taste. (f) Langfuse alerts — latency p95 spike, cost spike, error surge. **This is the Langfuse playbook the best LLM companies (Cursor, Character.AI, Notion AI) run internally.** | 3-5 days total over 2-3 weeks |
| 21γ.P25 | **Metabase 100X Tier 3 — scale features.** Trigger when team grows or stakeholder asks. (a) Slack integration — pipe Metabase alerts to `#yral-pulse`. (b) Embedded analytics in YRAL admin panel — signed-embedding for creator-influencer-facing live metrics + investor-facing growth metrics. (c) dbt transformation layer between Postgres and Metabase once analytics complexity grows past raw SQL. (d) Anomaly detection — Metabase's "alert on unusual change" + forecasting. (e) Public investor dashboard — single read-only URL with sanitized growth metrics, replaces ad-hoc deck updates. (f) Audit logging — Metabase tracks who viewed what; kill unused dashboards. (g) Branding — replace Metabase logo + colors with YRAL. (h) Connect billing service once Sarvesh ships H13 Creator Earnings → unit economics + LTV/CAC + payout funnel dashboards. (i) Connect Cloudflare analytics for request-vs-error correlation. (j) Weekly auto-PDF export of all dashboards delivered Monday 08:00 IST — saved record + self-review ritual. | 3-5 days total |
| **Phase 21γ total** | | **Tracked — NOT blocking real-user launch** | **~11-15 days** |

**How this maps to Rishi's questions on 2026-06-08:**
- "Do we have the right CI tests?" → I-Sec1 + I-Sec2 + I-Dep2 add the missing security + smoke gates
- "Canary rollbacks for DB migrations?" → I-Mig1 + I-Mig2 + I-Mig3 cover the migration safety triangle (snapshot + lint + test)
- "Last-good-state flag?" → I-Dep1 tags `:stable` after every successful deploy
- "Post-cutover SSH lockdown" → I-Dep3 splits humans (read) from CI (write)

## PHASE 21β: PRODUCTION CUTOVER (Play Store prod-track for real users)
**Model CLARIFIED 2026-06-08 by Rishi:** Prod-track Play Store app (`4974628203228829567`). Same codebase as alpha-track. Triggered after alpha team is satisfied + Phase 21α→β hardening window is closed. Sarvesh bumps versionCode → submits to Play Store production → app store approval (~2-7 days) → live to real users. Firebase Remote Config audience condition flips so 6 v2 flags = `true` for prod users. **V2 must be ≤5 min behind chat-ai at this moment.**

| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 21β.G1 | Gate: ≥7 days alpha with **zero Sentry P0s** | ⏳ Pending | — | — |
| 21β.G2 | Gate: All YRAL team report no regressions in alpha | ⏳ Pending | — | — |
| 21β.G3 | Gate: Phase 24 full security drill (24.1-24.4) clean | ⏳ Pending | 5 | — |
| 21β.G4 | Gate: I11 offsite backup verified + I10 weekly restore drill running | ⏳ Pending | 1 | — |
| 21β.G5 | Gate: Latency stable + meets 50%-faster under real alpha load | ⏳ Pending | — | — |
| 21β.G6 | Gate: mini re-bootstrap runbook + Caddy hard-cutover snippet ready (per 21αβ.H1 Option A — continuous ETL is intentionally NOT running). Cutover-day execution = 30-min window. | ⏳ Pending | — | — |
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

## PHASE 26: ANALYTICS DASHBOARD — `yral-rishi-analytics` (NEW — 2026-06-13)

**Driver:** Rishi 2026-06-13: "I want to be very close to the analytics of the chat feature we are shipping... I want this analytics solution to be build mostly autonomously... best analytics solution in the universe... a beautiful UI... reach product-market fit."

**Goal:** A standalone read-only analytics service for the chat product that answers ONE question: *is anyone falling in love with these bots?* Built as a separate Docker Swarm service pinned to rishi-6, on `analytics.rishi.yral.com`, behind Google Workspace login restricted to `@gobazzinga.io`. Pulls from the existing product Postgres via a read-only role + own `analytics` schema on a Patroni replica. Zero ability to touch the chat service.

**Strategic value:** Direct line of sight to PMF. The dashboard's metric spine (engaged sessions, W1 return rate, flattening cohort curve) lets Rishi distinguish "acquiring users" from "delighting users" — the only distinction that matters pre-PMF. Also funds business decisions: Phase 21γ.P15 (image-gen monetization) is decision-gated on View 7 economics from this phase.

**Architecture (full design at `docs/designs/analytics-100x-vision.md`):**
- New repo `dolr-ai/yral-rishi-analytics`, mirroring chat service file shape (config.py/database.py/routes/repositories) for cross-project SYMMETRY
- Service runs on rishi-6 via Swarm placement constraint, co-located with Langfuse
- DB access: dedicated `analytics_ro` role with SELECT on public + own `analytics` schema for derived data + `statement_timeout=5s` at the role level + default read-only transactions
- Auth: Google OAuth, domain-restricted to gobazzinga.io, Redis sessions, Postgres audit table
- 8 curated dashboard views (NOT a query builder, NOT Grafana clone)
- Server-rendered HTML + inline-SVG sparklines — no React/SPA/build step
- Hourly materialized-view refresh (sessionization is the one new primitive)

**Orchestration:** Analytics Architect promoted to Coordinator (2026-06-13). Spawns Developer worker for Phase 0+A+B; Frontend worker spawned at Phase C. Session 6 (Rishi's main session) is safety reviewer + cross-repo bridge. Status reports to Rishi every ~2h.

**Hard safety rules (non-negotiable):** Replica-only reads via role-level read-only enforcement. NO writes to product tables. NO chat service edits / docker ops / Caddy edits to agent.rishi.yral.com. NO touching rishi-1/2/3. Per-action authorization required from Rishi for: DB role/schema creation (pg_dump first per Rule 9), GitHub repo creation, Swarm secrets, deploy, Caddy stanza addition.

| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 26.0 | Service skeleton — new repo, `config.py`/`database.py`/`main.py`/`/healthz`, Dockerfile, CI, Swarm spec with rishi-6 placement + resource caps + auto-rollback, DB-setup SQL script (analytics_ro role + analytics schema + statement_timeout + grants), DO-NOT-EXECUTE markers. **Local-only at 2026-06-13: 6 Python files / 212 logic lines / no GitHub remote / no deploy / pg_dump-first gate.** | 🔄 Local complete — privileged actions queued for Rishi | 1 | (TBD — repo not yet created) |
| 26.A | Foundation + first signal — `analytics_sessions` materialized view + hourly refresh loop, `repositories/analytics_repo.py` core queries (engaged sessions, second-message rate, W1 return), headline tiles behind temporary shared-secret token. **First real signal day.** | ⏳ Blocked on action #1 (DB role/schema creation) | 1-2 | — |
| 26.B | Google OAuth + Redis sessions + `analytics_login_audit` table + Caddy stanza for `analytics.rishi.yral.com`. Retires temporary token. | ⏳ Pending | 1 | — |
| 26.C | The Glance view — calm, server-rendered, inline-SVG sparklines, sample-size badges, mobile-responsive grid. Frontend worker spawned here. | ⏳ Pending | 1 | — |
| 26.D | Retention + depth views — cohort comeback grid (View 1), still-here-at-N funnel (View 2), return-to-same-bot (View 3). | ⏳ Pending | 1 | — |
| 26.E | Bot quality leaderboard (View 4) + negative signals + error-reply detection (View 5). | ⏳ Pending | 1 | — |
| 26.F | Drill-down chrome — cohort → user → session metadata. Raw-transcript view HELD per Rishi 2026-06-13 (decided NO content view for now). | ⏳ Pending | 1 | — |
| 26.G | Coach funnel (View 6) + economics / cost-per-engaged-user (View 7). View 7 is the decision gate for Phase 21γ.P15. | ⏳ Pending | 1 | — |
| 26.H | Iterate — add/kill views weekly based on signal-vs-noise. Candidate: in-app Sean Ellis one-tap PMF survey (post-cutover, mobile change required). | 🔄 Ongoing | — | — |
| **Phase 26 total** | | **~8-9 days end-to-end build; first signal at end of 26.A** | **8-9 days** | |

### Decided 2026-06-13 (locks the design)

- Repo name: `yral-rishi-analytics` under `dolr-ai`
- DB access: read-only role + `analytics` schema on Patroni replica (NOT separate database)
- Engaged session: ≥4 user messages, 20-min gap (both hot-editable; Rishi tuned down from architect's 6/30 defaults)
- Raw transcript access: HOLD ENTIRELY for now (metadata drill-down only)
- 100-line rule: logic lines count, HTML/CSS templates exempt with PR-description heads-up
- Metabase: SKIP — not strictly needed
- rishi-6 headroom: comfortable for an internal low-traffic service
- Google OAuth client: Rishi creates in Workspace admin at Phase B

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
| 21α | Alpha Cutover (Play Store alpha-track, YRAL internal team) | 26 | 12 | 14 | ~1 prep + N team-test |
| 21α→β | V2 Hardening Window (operational — ETL, failover drills, cost alerting, multimodal LLM) | 12 | 1 | 11 | ~9-11 |
| 21αβ.I | Production-grade safety (CI guardrails — security, migration, deploy) | 8 | 3 | 5 | ~1.5-2 |
| 21β | Production Cutover (Play Store prod-track, real users) | 12 | 0 | 12 | ~5-10 |
| 21γ | Post-cutover polish (good-to-have, NOT blocking real-user launch) | 7 | 0 | 7 | ~6-7 |
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
