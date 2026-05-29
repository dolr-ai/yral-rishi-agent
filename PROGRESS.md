# Master Feature Tracker — yral-rishi-agent v2 (1000x Vision)

**Last updated:** 2026-05-28 afternoon
**Codebase:** ~6,500 lines Python (post Chat as Human + graceful error UX)
**Total phases:** 22 (Phase 0–21) | **Est. total days remaining:** ~50-65 days

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
| 1.7 | Media upload (1 endpoint) | ✅ Done | — | #158 |
| 1.8 | Image generation in chat (1 endpoint) | ✅ Done | — | #158 |
| 1.9 | Human-to-Human chat: create + list + send (3 endpoints) | ✅ Done | — | #158 |
| 1.10 | Chat as Human (creator takeover mode) — backend ✅ shipped & retested, mobile UI PR to Sarvesh pending (feature-flag-gated, awaiting agent v2 cutover) | ✅ Done (backend) | 1 | #170 (backend, merged 2026-05-28) + mobile PR (pending) |
| 1.11 | Unified inbox v3 (1 endpoint) | ✅ Done | — | #158 |
| 1.12 | Billing paywall (calls billing.yral.com) | ✅ Done | — | #158 |
| 1.13 | WebSocket inbox + WS docs (1 WS + 1 endpoint) | ✅ Done | — | #158 |
| 1.14 | ETL data migration (3.3M messages) | ✅ Done | — | — |
| 1.15 | Swarm deploy (2 replicas on rishi-4/5) | ✅ Done | — | — |
| 1.16 | Full Motorola test of all 30 endpoints | ⏳ Tomorrow | 0.5 | — |
| 1.17 | Latency comparison vs chat-ai | ⏳ Tomorrow | 0.5 | — |
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
| 2.7 | SSE streaming (word-by-word AI responses) | ✅ Done (backend) / ⏳ Pending (mobile integration) | — | #189 (backend) |
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
| 5.4 | User-configurable frequency (daily/weekly/off) | ⏳ Pending | 1 | — |
| 5.5 | Context-aware timing (morning greetings, evening) | ⏳ Pending | 1 | — |
| 5.6 | Streak tracking (reward consistent chatters) | ⏳ Pending | 1 | — |
| 5.V1 | Verify: proactive messages actually sending? | ⏳ Pending | 0.5 | — |
| **Phase 5 total** | | **50% done** | **3.5 days left** | |

## PHASE 6: FIRST-TURN NUDGE
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 6.1 | Detect idle mid-conversation (30+ min) | ✅ Done | — | #165 |
| 6.2 | Generate follow-up nudge | ✅ Done | — | #165 |
| 6.3 | Wired into engagement loop | ✅ Done | — | #166 |
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
| 7.5 | Soul File Coach (AI helps improve personality via chat) | 🔄 In PR (backend) | 3 | #191 |
| 7.6 | A/B testing (two soul file versions, compare quality) | ⏳ Pending | 2 | — |
| 7.7 | Bot quality scorer (automatic conversation rating) | ⏳ Pending | 2 | — |
| 7.8 | Creator recommendations (AI suggests changes) | ⏳ Pending | 1 | — |
| 7.9 | 5-minute bot creation wizard (structured intake + preview) | ⏳ Pending | 3 | — |
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
| 9.3 | Run eval and verify results | ⏳ Pending | 0.5 | — |
| 9.4 | CI integration (eval runs on AI-touching PRs) | ⏳ Pending | 1 | — |
| 9.5 | Quality regression alerts | ⏳ Pending | 0.5 | — |
| **Phase 9 total** | | **40% done** | **2 days left** | |

## PHASE 10: SSE STREAMING
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 10.1 | Backend SSE endpoint (stream tokens from Gemini) | ⏳ Pending | 2 | — |
| 10.2 | Mobile SSE parser (Kotlin/Ktor SSE client) | ⏳ Pending | 2 | — |
| 10.3 | JSON fallback path (old clients still work) | ⏳ Pending | 0.5 | — |
| 10.4 | Feature flag to toggle SSE on/off | ⏳ Pending | 0.5 | — |
| 10.5 | Sarvesh coordination for production mobile app | ⏳ Pending | 1 | — |
| **Phase 10 total** | | **Not started** | **6 days** | |

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
| 12.1 | Per-archetype few-shot examples | ⏳ Pending | 2 | — |
| 12.2 | Different LLM models per archetype | ⏳ Pending | 1 | — |
| 12.3 | Different temperature/settings per archetype | ⏳ Pending | 0.5 | — |
| 12.4 | Global response quality guardrails | ⏳ Pending | 1 | — |
| 12.5 | Response diversity (no repetitive phrases) | ⏳ Pending | 1 | — |
| **Phase 12 total** | | **Not started** | **5.5 days** | |

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
| 19.1 | Per-user rate limiting (req/min + req/hour) | ⏳ Pending | 1 | — |
| 19.2 | Runaway cost circuit breaker | ⏳ Pending | 0.5 | — |
| 19.3 | DDoS protection (Caddy-level) | ⏳ Pending | 1 | — |
| 19.4 | Dead letter queue for failed tasks | ⏳ Pending | 1 | — |
| 19.5 | Synthetic user heartbeat (canary every 5 min) | ⏳ Pending | 1 | — |
| **Phase 19 total** | | **Not started** | **4.5 days** | |

## PHASE 20: SELF-HOSTED LLM (Month 6+)
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 20.1 | GPU server from Saikat (H100/A100) | ⏳ Pending | 1 | — |
| 20.2 | vLLM / TGI deployment | ⏳ Pending | 2 | — |
| 20.3 | Model selection (Llama/Qwen/Mistral/DeepSeek) | ⏳ Pending | 1 | — |
| 20.4 | Fine-tune on YRAL conversation data | ⏳ Pending | 5 | — |
| 20.5 | Latency benchmark vs Gemini | ⏳ Pending | 1 | — |
| 20.6 | Gradual rollout (non-critical turns first) | ⏳ Pending | 2 | — |
| **Phase 20 total** | | **Not started** | **12 days** | |

## PHASE 21: PRODUCTION CUTOVER (at Rishi's discretion)
| # | Sub-phase | Status | Est. days | PR |
|---|-----------|--------|-----------|-----|
| 21.1 | Final ETL sync (delta since last migration) | ⏳ Pending | 1 | — |
| 21.2 | 10% rollout via mobile feature flag | ⏳ Pending | 1 | — |
| 21.3 | Dual-write period (both DBs in sync) | ⏳ Pending | 2 | — |
| 21.4 | 25% → 50% → 100% gradual rollout | ⏳ Pending | 3 | — |
| 21.5 | Monitor Sentry at each rollout step | ⏳ Pending | — | — |
| 21.6 | Chat-ai standby 90+ days | ⏳ Pending | — | — |
| 21.7 | Decommission chat-ai (Rishi explicit approval) | ⏳ Pending | 1 | — |
| **Phase 21 total** | | **Not started** | **8 days** | |

## MOBILE CLIENT WORK (across phases)
| # | Feature | Depends on | Status | Est. days |
|---|---------|-----------|--------|-----------|
| M1 | H2H chat UI (new screens) | Phase 1 API ✅ | ⏳ Pending | 3 |
| M2 | Chat as Human toggle UI | Phase 1 API ✅ | ⏳ Pending | 2 |
| M3 | SSE streaming parser | Phase 10 backend | ⏳ Pending | 2 |
| M4 | Creator Studio UI | Phase 7 API ✅ | ⏳ Pending | 3 |
| M5 | Earnings dashboard UI | Phase 8 API ✅ | ⏳ Pending | 2 |
| M6 | Typing indicator animation | Phase 16 backend | ⏳ Pending | 1 |
| M7 | Presence / online status | Phase 16 backend | ⏳ Pending | 1 |
| M8 | Read receipts | Phase 16 backend | ⏳ Pending | 1 |
| M9 | Skill marketplace UI | Phase 15 backend | ⏳ Pending | 3 |
| M10 | Private content UI | Phase 14 backend | ⏳ Pending | 2 |
| M11 | Push notification improvements | Phase 5 | ⏳ Pending | 1 |
| **Mobile total** | | | | **21 days** |

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
| I10 | Weekly automated backup restore drill | ⏳ Pending |
| I11 | Offsite backup to separate S3 bucket | ⏳ Pending |
| I12 | Pre-migration snapshot automation | ⏳ Pending |
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
| 19 | Rate Limiting | 5 | 0 | 5 | 4.5 |
| 20 | Self-hosted LLM | 6 | 0 | 6 | 12 |
| 21 | Production Cutover | 7 | 0 | 7 | 8 |
| — | Mobile Client | 11 | 0 | 11 | 21 |
| — | Infrastructure | 15 | 6 | 9 | — |
| **TOTAL** | | **168** | **55** | **113** | **~55-70 days** |

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
