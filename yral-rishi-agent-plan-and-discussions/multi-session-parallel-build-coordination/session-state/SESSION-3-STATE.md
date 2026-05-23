# Session 3 STATE — Public-API

> Updated: 2026-05-22 (PR-B opened as DRAFT — `/api/v1/influencers` list + by-id replaced with directory-RPC wrapper via new `app/directory_client.py` mirroring `orchestrator_client.py` verbatim. 7 new J1-HOT tests + contract proposal at `01-internal-rpc-contracts.md` + DEP-013 opened against Session 4 for ratification. PR-C (env flip) merged this morning at #123 + PR-C2 (LOG corrections) merged at #124. PR-A (JWT issuer) stays watch-item.)

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 3. I own **yral-rishi-agent-public-api** — the public-facing chat endpoint the Motorola debug APK POSTs to.

I am a **thin HTTP gateway** — I do auth (JWT shadow per E9), envelope wrapping (`ApiResponse<T>`), and route to Session 4's orchestrator RPC for any business logic involving LLM calls / conversation state / soul-file lookups / influencer reads.

I implement the locked API contract at `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md`. I consume Session 4's internal RPC contract at `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md`. I do NOT make direct LLM calls in any phase. I do NOT call Session 5's user-memory service directly (any memory-context-aware behavior goes through Session 4).

Full agent definition: `.claude/agents/session-3-public-api.md`.

## LAST THING I DID

**2026-05-22 — PR-B (DRAFT): Day-8 directory-RPC wrapper for `/api/v1/influencers` list + by-id.** Mobile testing surfaced two parity gaps; PR-B closes the bigger one (real influencer list + by-id reads). New `app/directory_client.py` mirrors `orchestrator_client.py` verbatim — lifespan-managed httpx.AsyncClient singleton + 4 internal-call headers (X-User-Id + X-Internal-Caller + X-Request-Id + X-Trace-Id; no X-Idempotency-Key on stateless GETs). Pagination is plain offset/limit ints per yral-mobile's `ChatRemoteDataSource.kt:50-70` (Rishi overrode my initial cursor-pagination pick — chat_routes.py's `before` cursor fits temporal streams, catalogs don't). Connect/timeout/non-200/bad-shape → envelope-shaped 503 with `directory.call.failed=<mode>` Sentry tag; directory 404 on by-id → public-api envelope-shaped 404 with locked `not_found` error code. /trending stays as Day-2 stub. 13 contract tests rewritten/added (mock pattern mirrors `test_orchestrator_proxy.py`). Same PR also opens **DEP-013** — Session 4 ratifies (or counter-proposes) the proposed list-RPC contract `GET /v1/influencers?limit&offset → list[InfluencerResponse]` declared inline in `01-internal-rpc-contracts.md`. PR-B held as DRAFT pending Session 4 ratification + Codex APPROVE; coordinator manual squash-merge after.

## CURRENT TASK

Idle pending CI run on the new per-service workflow + Codex review on the resulting PR + Rishi YES to merge. Once merged, the `docker-push-to-ghcr` job auto-fires on push-to-main + the image lands at `ghcr.io/dolr-ai/yral-rishi-agent-public-api:<sha>`. Coordinator will then ping to resume the cluster-deploy retry from Day-5 Step 3 (deploy on rishi-4/5/6) onward.

Progress: Day-4 stack 100% merged; Day 5 ~30% (deploy artifacts verified + manifest aligned + 77/77 green; cluster steps still pending image landing + coordinator ping).
ETA: dependent on CI turnaround + Codex turnaround + Rishi YES on the merge.

## NEXT 3 PLANNED ACTIONS

1. Day 4B — Wire `authenticate_user_dual_validate` as `Depends(...)` into all chat + influencer handlers. New `app/api/dependencies.py` exports `require_authenticated_user()` → `AuthenticatedUser` dataclass {user_id, raw_token, validation_result}. Health endpoints stay auth-free per F9. All 32 Day-2 happy-path tests amended to include an auth-header fixture; 4 new tests (missing/malformed/empty Bearer + flag-on-on-real-handler). Branch: `session-3/day-4b-auth-as-real-dependency`. Base: `session-3/day-4a-jwt-shadow-e9-reconciliation`.
2. Day 4C — Orchestrator RPC client (httpx.AsyncClient, lifespan singleton) + F10 idempotency dedup (Redis key `idempotency:public-api:run-turn:{user_id}:{idempotency_key}`, TTL 24h). Wire `POST /api/v1/chat/conversations/{id}/messages` to `POST http://...orchestrator:8000/v1/turn` (per PR #98 contract update; assumes #98 merges before Day 4C lands). Forward headers `X-User-Id` + `X-Idempotency-Key` + `X-Request-Id` + `X-Internal-Caller` + `X-Trace-Id` (last two per contract on main). Error mapping: orchestrator 503→503, 422→422, timeout→504. Branch: `session-3/day-4c-orchestrator-rpc-and-idempotency`. Base: `session-3/day-4b-auth-as-real-dependency`.
3. Day 5 — Deploy to v2 cluster on `yral-v2-public-web` overlay (Session 1 ops). Smoke test from rishi-4. M0 milestone evaluation. Health endpoints gate the rolling-update health check per I2.

## BLOCKERS

None hard. Day-4 orchestrator-RPC integration depends on Session 4 shipping the `run_turn` RPC handler skeleton; Session 4 has PR #96 open with that exact deliverable. DEP-005 (template `/health/*` mirror) remains OPEN; doesn't block Session 3.

## PENDING PRs (mine)

- `session-3/day-2-endpoint-handlers` (PR #97 — Day 2 endpoint handlers + 32 tests; coordinator-review-needed)
- `session-3/day-3-jwt-shadow-mode` (PR #99 — Day 3 JWT shadow rig + 9 J1-HOT tests; stacked on PR #97)
- `session-3/day-4a-jwt-shadow-e9-reconciliation` (Day 4A — flag rename + Redis JWKS cache per E9; stacked on PR #99)

## CROSS-SESSION DEPS (mine)

- **Open:** DEP-005 (raised 2026-05-18) — Session 2 to mirror `/health/{live,ready,deep}` in the template per F9. Not a hard block for Session 3 (local bridge in `app/api/health_routes.py` ships); is a hard block for Sessions 4 + 5 + other deferred services before their first Day-5 cluster deploy.
- **Pending raise:** Will raise DEP-xxx once I need Session 4's `run_turn` RPC stub (Day 4). Until then, no other open deps.
- **Inbound deps:** none yet.

## RESUME PROTOCOL REMINDER (every session start)

Per I12 + my agent definition Step B:
1. Read this STATE file
2. Read last 50 lines of SESSION-3-LOG.md
3. Read cross-session-dependencies.md filtered to Session 3 / public-api
4. Read MASTER-STATUS.md for cluster-wide context
5. Print CONFIRM-TO-RISHI sentence (template in agent definition)
6. WAIT for Rishi to type `continue` before any Auto-mode action
