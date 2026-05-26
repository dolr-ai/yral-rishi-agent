# Session 3 STATE — Public-API

> Updated: 2026-05-25 (PR #141 round-7 — Codex post-squash verdict: 2 BLOCKERs + 2 CONCERNs closed. BLOCKER 1 = B2 runtime `exc`/`exc_type` → `exception`/`exception_type` rename in my user-memory-hop error handlers (PR #154 carve-out makes B2 runtime-only; this is runtime code). BLOCKER 2 = industry/security: added 2 trust-boundary verification assertions before the `derived_influencer_id` read — (a) returned `id` must match URL-path `conversation_id` (wrong-row signal → 503); (b) returned `user_id` must match JWT-authenticated `user.user_id` (CROSS-TENANT LEAK signal → 503 + Sentry `level="fatal"` to page on-call). 2 new regression tests prove the assertions fire + orchestrator NEVER fires when triggered. CONCERN 1 = scaffold INTENTIONAL-SCAFFOLD comment added in `tests/integration/test_gate_a1_send_message_smoke.py` (informational ack only). CONCERN 2 = E1 violation: `user_memory_request_timeout_seconds` dropped 5.0 → 0.5 + `user_memory_connect_timeout_seconds` 2.0 → 0.2 so a degraded user-memory fail-fasts in 500 ms instead of holding mobile requests for 5 s. Cumulative PR #141 diff vs origin/main: 10 files / +1436 / -28 / 2 commits (round-6 squash `5c43f3b` + round-7 fixup); 20 J1-HOT contract tests in `test_orchestrator_proxy.py`. Round 7 is past the 5-round cap but BLOCKERs are runtime/security per the cap-rule's override carve-out.)

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 3. I own **yral-rishi-agent-public-api** — the public-facing chat endpoint the Motorola debug APK POSTs to.

I am a **thin HTTP gateway** — I do auth (JWT shadow per E9), envelope wrapping (`ApiResponse<T>`), and route to Session 4's orchestrator RPC for any business logic involving LLM calls / conversation state / soul-file lookups / influencer reads.

I implement the locked API contract at `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md`. I consume Session 4's internal RPC contract at `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md`. I do NOT make direct LLM calls in any phase. **For Phase 1, the ratified architecture (PR #145, 2026-05-25; binding Phase-1 decision recorded in `01-internal-rpc-contracts.md` section "Architectural decision — Phase-1 ratification (2026-05-24)") establishes that public-api makes a direct synchronous call to Session 5's user-memory-service `GET /v1/conversations/{id}` to derive a trusted `influencer_id` before every orchestrator call.** This is NOT framed as a narrow exception — it IS the binding Phase-1 architecture per Codex round-1 CONCERN on PR #141 + coordinator ratification. The trust-root must be public-api (the JWT-validated user_id + URL-path conversation_id are public-api's authoritative inputs); the user-memory by-id endpoint's 404-on-cross-tenant-or-missing-conversation is the load-bearing security property. Memory-context-aware *behavior* (history reads, summarization, etc.) still goes through Session 4. The alternative "orchestrator owns the derivation" is captured in the ratification doc as a post-production-traffic re-evaluation candidate at Rishi's A6 discretion, NOT as a Phase-1 path. Gate A1 (per-PR public-api integration SMOKE check) lives at `yral-rishi-agent-public-api/tests/integration/`; Gate A_user_memory (cross-cut SQL/pool behavior) lives in Session 5's CI per cross-session test-ownership separation.

Full agent definition: `.claude/agents/session-3-public-api.md`.

## LAST THING I DID

**2026-05-25 — PR #141 round-7 (Codex post-squash verdict on rebased PR #141): 2 BLOCKERs + 2 CONCERNs closed.** Codex re-evaluated against current main after the round-6 squash-rebase; produced 2 BLOCKERs (B2 runtime + trust-boundary security gap) + 2 CONCERNs (scaffold information + E1-violating user-memory timeout). All 4 closed:

- **BLOCKER 1 (B2 runtime)** — `app/api/chat_routes.py`: renamed `exc` → `exception` (variable) + `exc_type` → `exception_type` (Sentry context key) + `exc` Sentry context key → `exception` across the 7 occurrences in my user-memory-hop error handlers. PR #154 B2 carve-out makes B2 runtime-only; this is runtime production code so the carve-out doesn't help. Pre-existing `exc` references in `app/main.py` + the orchestrator-path handlers (Day-4C, pre-PR-#141) NOT touched per "fix what you ship" — diff grep confirms 0 `exc` / `exc_type` tokens in my additions vs origin/main.

- **BLOCKER 2 (industry — trust boundary security gap)** — `app/api/chat_routes.py`: added 2 verification assertions BEFORE the `derived_influencer_id = conversation.get("ai_influencer_id")` read. (a) `conversation.get("id") != conversation_id` → envelope-shaped 503 + Sentry `error` (signals user-memory implementation bug — wrong row returned for the by-id lookup). (b) `conversation.get("user_id") != user.user_id` → envelope-shaped 503 + Sentry **`level="fatal"`** (CROSS-TENANT LEAK signal — pages on-call immediately; user-memory regression CANNOT reach mobile). Both Sentry contexts deliberately record only `*_type` rather than the leaked values themselves (H6 PII discipline). 2 new regression tests in `tests/contract/test_orchestrator_proxy.py` prove the assertions fire + orchestrator NEVER fires when triggered: `test_send_message_returns_503_when_user_memory_id_does_not_match_url_conversation_id` + `test_send_message_returns_503_when_user_memory_user_id_indicates_cross_tenant_leak`.

- **CONCERN 1 (test — scaffold CI-coverage gap)** — `tests/integration/test_gate_a1_send_message_smoke.py`: expanded the module-level "SCAFFOLD ONLY" comment to "**INTENTIONAL SCAFFOLD** — implementation lands by Day 11-13 per PR #145 Gate A1 acceptance criteria. CI-coverage gap on the new public-api → user-memory behavior is INTENTIONAL until the implementation step (Codex PR #141 round-6 CONCERN 1 informational acknowledgment). The contract-level unit tests in `tests/contract/test_orchestrator_proxy.py` (including the trust-boundary forgery-rejection test + the round-7 id / user_id verification checks) cover the public-api side of the boundary at the unit-test tier in the meantime." Codex CONCERN was informational; this comment is the explicit acknowledgment.

- **CONCERN 2 (industry — E1 violation)** — `app/config.py`: dropped `user_memory_request_timeout_seconds` **5.0 → 0.5** (500 ms total) + `user_memory_connect_timeout_seconds` **2.0 → 0.2** (200 ms connect). The previous 5 s ceiling would have held the send-message hot path for up to 5 s on a degraded user-memory — directly violating CONSTRAINTS E1 (user-interactive endpoints MUST be 50% faster than chat-ai). 500 ms is the aggressive end of Codex's suggested 200-500 ms band + leaves Postgres p95 headroom; a down user-memory now fail-fasts to envelope-shaped 503 instead of holding mobile requests. Comments on both fields document the round-7 rationale.

**Round-arc cumulative state**: 10 files / +1436 / -28 / 2 commits (round-6 squash @ `5c43f3b` + round-7 fixup). 20 J1-HOT contract tests (18 pre-round-7 + 2 round-7 trust-boundary regressions). **Carve-out exception**: at round 7 (> 5-round cap), but both BLOCKERs are runtime/security per the cap rule's "only override for paperwork" carve-out — keep iterating until Codex APPROVE. **Merge gate**: Codex APPROVE on round-7 + coordinator squash-merge. Coordinator unblocked PR #141 with three back-to-back merges this morning: PR #145 (architectural ratification — `public-api → user-memory direct call is the binding Phase-1 architecture per Codex round-1 CONCERN`; merged 10:13 UTC; adds 189 lines to `01-internal-rpc-contracts.md` ratifying the decision + Gate A1/A2/A2-PR/A2-NIGHTLY/Gate-B 5-gate latency-enforcement layer), PR #137 (Redis-AUTH wiring; merged 10:51 UTC via coordinator override under the new 5-round cap — paperwork-only items remaining at round-19), PR #154 (audit-recs adoption: B2/B7 carve-out → runtime-code-only scope so tests need only plain-English names + 1-line WHAT/WHEN/WHY per J3; 5-round Codex cap; worktree-setup script; merged 11:19 UTC). **Round-6 ships**: (1) STATE `Updated:` line + START-OF-SESSION SUMMARY rewritten — the "PR-B2 added one narrow exception" framing replaced with the ratified-architecture reference pointing at `01-internal-rpc-contracts.md` section "Architectural decision — Phase-1 ratification (2026-05-24)"; (2) Gate A1 scaffold under `yral-rishi-agent-public-api/tests/integration/` — `__init__.py` + `conftest.py` (4 ephemeral-port-fake fixtures: user_memory_fake_server, orchestrator_fake_server, yral_billing_fake_server, langfuse_in_process_span_exporter — each with WHAT/WHEN/WHY docstring + `pytest.skip` body for the implementation step) + `test_gate_a1_send_message_smoke.py` (5 acceptance-criterion-bearing skipped tests: envelope-maps-to-ApiResponse<MessageDto> per 00-api-contract.md:35; request path + 4 headers per PR #145 line 304; response Pydantic model parses cleanly; public-api → user-memory call carries Langfuse span per PR #145 line 303; user-memory timeout/5xx → envelope-shaped 503 with orchestrator + yral-billing fakes receiving zero calls). Module-level `pytestmark = pytest.mark.skip(...)` keeps CI green until Day-12-13 implementation step lands. PR #154 carve-out applied: scaffold uses plain-English test names + 1-line WHAT/WHEN/WHY (no full B7 ceremony per the new runtime-only scope). **Cumulative PR #141 state**: 12 files / +1352 / -28 / 7 commits (rounds 1-5 + round-6 ratification adoption + scaffold). **Merge gate**: Codex APPROVE on round-6 scope-fix; coordinator squash-merge after.

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
