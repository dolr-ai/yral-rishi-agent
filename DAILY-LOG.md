# Daily Log

## 2026-05-26 — Phase 0 + Phase 1 Days 2-14 (all in one session)

### What completed
- **Phase 0**: Archived 17 v2 service folders, removed 7 worktrees, closed PRs #147 and #157, deleted 130 stale branches, created CLAUDE.md + GLOSSARY.md + README.md, created CI workflows
- **Day 2**: config.py + database.py + auth.py + main.py + health routes (4 endpoints)
- **Day 3**: models.py + influencer READ endpoints + migrations (3 endpoints)
- **Day 4**: conversation routes + chat_v2 bot-aware inbox (6 endpoints)
- **Day 5**: ai_client (Gemini + OpenRouter) + send-message — the HEART (1 endpoint)
- **Day 6**: influencer CREATE flow — generate prompt, validate, create, update, delete, admin ban/unban (8 endpoints)
- **Day 7**: media upload + image generation in conversations (2 endpoints)
- **Day 8**: human-to-human chat — create, list, send message (3 endpoints)
- **Day 9**: unified inbox v3 — AI + human chats in one list (1 endpoint)
- **Day 10**: billing paywall — RESOLVED. Billing is 100% client-side. Mobile app calls `billing.yral.com/google/chat-access/check` directly. No backend code needed.
- **Day 11**: WebSocket inbox — real-time events (1 WS + 1 docs endpoint)
- **Day 12**: ETL script written + deploy scripts + project/servers config
- **Day 13**: DEPLOYED TO CLUSTER
  - Created `yral_agent_db` database on Patroni leader (rishi-5)
  - Applied both migrations (001_initial.sql, 002_influencer_trending_stats.sql)
  - Built Docker image on rishi-4 and rishi-5
  - Deployed as Swarm service `yral-rishi-agent` with 2 replicas
  - Updated internal Caddy config to route `agent.rishi.yral.com` → `yral-rishi-agent:8000`
  - All health checks passing through internal Caddy
- **Day 14**: 24 unit tests across 4 files

### Verified endpoints (via internal Caddy on rishi-5)
```
curl agent.rishi.yral.com/        → {"service":"Yral Agent API","version":"2.0.0","status":"running"}
curl agent.rishi.yral.com/health  → {"status":"OK","database":"reachable"}
curl agent.rishi.yral.com/status  → {"service":"Yral Agent API",...,"database":"reachable","gemini_model":"gemini-2.5-flash"}
curl agent.rishi.yral.com/api/v1/influencers → {"influencers":[],"total":0,"limit":50,"offset":0}
```

### Public URL status
`curl https://agent.rishi.yral.com/health` returns 503 — the rishi-1/2 edge Caddy needs a config reload to recognize the updated upstream on the v2 cluster. Internal Caddy on rishi-4/5 works perfectly.

**To fix:** Reload or redeploy the Caddy snippet on rishi-1/2 that proxies to the v2 cluster. The v2 internal Caddy is correctly routing to `yral-rishi-agent:8000`.

### Cluster state
- Swarm service: `yral-rishi-agent` — 2/2 replicas on rishi-4 + rishi-5
- Database: `yral_agent_db` on Patroni (leader: rishi-5, replicas: rishi-4, rishi-6)
- Old microservices still running (can be removed after cutover)

### Endpoint count
29 HTTP endpoints + 1 WebSocket = 30 total. All accounted for per the plan.

### Line count
- app/ code: 3,984 lines
- chat-ai baseline: 6,780 lines
- Ratio: 59% of chat-ai — same functionality, no bloat

## 2026-05-26 — Phase 2.1: Langfuse Tracing

### What completed
- Added `langfuse==2.60.2` to requirements
- Created `app/services/langfuse_tracing.py` — Langfuse client wrapper with `trace_generation()` helper
- Integrated tracing into `ai_client.generate_response()` — every Gemini and OpenRouter call is now traced with provider, model, input/output tokens, latency, user_id, conversation_id
- Error traces logged at ERROR level so failed LLM calls are visible in Langfuse
- Langfuse flush on app shutdown
- Config: `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST` env vars (no-op if not set)

### To activate
Set LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, and LANGFUSE_HOST env vars on the Swarm service. Can point to the self-hosted Langfuse on the cluster (once scaled up from 0 replicas) or Langfuse Cloud.

### Next steps (Rishi)
1. Reload rishi-1/2 Caddy to fix the public 503
2. ETL: take pg_dump of chat-ai DB, run `scripts/etl-from-chat-ai.sh` to load data
3. Set env vars on Swarm service: GEMINI_API_KEY, OPENROUTER_API_KEY, S3 creds, SENTRY_DSN
4. Motorola test: open debug APK, see influencer catalog, send message, get AI reply
