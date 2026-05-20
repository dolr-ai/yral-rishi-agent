# Session 3 STATE — Public-API

> Updated: 2026-05-20 (Day 5 DEPLOYED to rishi-4/5/6 — 3/3 replicas Running; /health/live + /health/ready both 200; auth dep rejects unauthenticated requests with the correct envelope. M0 = Deployed YES / agent.rishi.yral.com NO (CF 525 — Session-1 issue) / Chat round-trip PARTIAL (auth-gate verified intra-cluster; full round-trip blocked on Session 4 orchestrator deploy + Caddy fix).)

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 3. I own **yral-rishi-agent-public-api** — the public-facing chat endpoint the Motorola debug APK POSTs to.

I am a **thin HTTP gateway** — I do auth (JWT shadow per E9), envelope wrapping (`ApiResponse<T>`), and route to Session 4's orchestrator RPC for any business logic involving LLM calls / conversation state / soul-file lookups / influencer reads.

I implement the locked API contract at `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md`. I consume Session 4's internal RPC contract at `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md`. I do NOT make direct LLM calls in any phase. I do NOT call Session 5's user-memory service directly (any memory-context-aware behavior goes through Session 4).

Full agent definition: `.claude/agents/session-3-public-api.md`.

## LAST THING I DID

**2026-05-20 — Day 5 cluster deploy on rishi-4/5/6, M0 = Deployed YES / external NO / chat round-trip PARTIAL.** Coordinator-confirmed REDIS_URL composer (Option 1, single-primary `from_url` form) + 3 empty placeholder Sentry/Langfuse secrets created on rishi-4. Compose SCPd up; `docker stack deploy` first crashed in a restart loop due to two first-deploy bugs (both fixed in-this-commit `docker-compose.swarm.yml`): (1) Swarm secret files were root-owned + mode 0400 → appuser couldn't read → entrypoint wrapper's `cat` failed silently → empty env vars → wrong Redis URL → /health/ready 503; fix = `uid: "1001"` + `gid: "1001"` per secret. (2) Healthcheck used `wget` which isn't in `python:3.12-slim` → exit 127 → unhealthy → restart loop; fix = `python -c "urllib.request.urlopen(...)"`. After full stack rm + redeploy, 3/3 replicas Running on rishi-4 + rishi-5 + rishi-6 within ~15s. Intra-cluster smoke: /health/live + /health/ready both 200 OK with the right shape (redis:ok via Sentinel quorum); /api/v1/chat/conversations + /api/v1/influencers correctly 401 on missing/malformed Bearer with the locked `unauthorized` envelope from the Day-4B real auth dep. uvicorn's `/proc/1/environ` confirms REDIS_URL is the right Sentinel-quorum primary URL (password redacted in output via sed before printing — never exposed). External `agent.rishi.yral.com` still returns Cloudflare 525 (origin SSL handshake failed — pre-existing SESSION-1-LOG line 546 issue). Session 4 services (orchestrator, soul-file, influencer-and-profile) not on cluster yet; full chat round-trip blocked.

## CURRENT TASK

Idle pending coordinator decisions on the 3 I6-flagged out-of-scope items: (a) Session 1 Cloudflare → cluster Caddy TLS 525 fix; (b) Session 4 Day-5 deploy of orchestrator + soul-file + influencer-and-profile so the chat round-trip M0 metric flips green; (c) the `shell-tests` CI failure noted by coordinator (separate small fix). PR #108 now carries Piece A (secrets alignment) + the 2 compose deploy-bug fixes; CI + Codex re-review on the new commit.

Progress: Day 5 ~90% on Session-3-owned surface (deploy succeeded; intra-cluster smoke green; M0 deployed=YES, external=NO with documented Session-1 cause, round-trip=PARTIAL with documented Session-4 cause). Phase 1 ~40%.
ETA: dependent on coordinator's resolution of the 3 I6 flags + Codex review on PR #108.

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
