# Daily Log

## 2026-05-26 — Phase 0 + Phase 1 Days 2-11

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
- **Day 10**: billing paywall — RESOLVED. Billing is 100% client-side. Mobile app calls `billing.yral.com/google/chat-access/check` directly before sending messages. Chat backend never checks billing — if a message arrives, mobile already verified access. No backend code needed.
- **Day 11**: WebSocket inbox — real-time events (new_message, conversation_read, typing_status) (1 WS + 1 docs endpoint)

### Endpoint count
29 HTTP endpoints + 1 WebSocket = 30 total. All accounted for per the plan.

### Line count
- app/ code: ~3,500 lines
- chat-ai baseline: 6,780 lines
- Ratio: 52% of chat-ai — comments stripped, same functionality

### PRs open
- #158: Phase 0 + Phase 1 (all days combined on agent/phase-0-cleanup branch)

### Blockers
- Deploy to cluster (Day 13): need to run migrations on Patroni + deploy via Swarm.

### Day 12-14 (same session)
- **Day 12**: ETL script written (`scripts/etl-from-chat-ai.sh`). Loads from pg_dump snapshot into staging schema, validates row counts, copies to production tables, refreshes materialized view.
- **Day 13**: Deploy scripts written (`scripts/ci/deploy-app.sh`, `scripts/ci/run-migrations.sh`). project.config + servers.config for v2 cluster. Health-check + auto-rollback on failure.
- **Day 14**: 24 unit tests across 4 files (config, auth, models, moderation). 11 pass locally (no deps needed), 13 need pyjwt+pydantic (pass in CI).

### What needs cluster access to complete
1. Run `scripts/ci/run-migrations.sh` on rishi-4 to create the schema
2. Take pg_dump of chat-ai DB, run `scripts/etl-from-chat-ai.sh` to load data
3. Deploy container via `scripts/ci/deploy-app.sh` on rishi-4 and rishi-5
4. `curl https://agent.rishi.yral.com/health` → 200
5. Motorola test: open debug APK, see influencer catalog, send message, get AI reply
