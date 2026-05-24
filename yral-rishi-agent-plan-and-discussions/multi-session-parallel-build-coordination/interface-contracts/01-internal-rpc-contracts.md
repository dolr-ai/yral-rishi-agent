# Internal RPC Contracts — Service ↔ Service

> Inter-service calls inside the v2 cluster. All on Swarm overlay `yral-v2-internal` per C3. JSON over HTTP (FastAPI). No public exposure.

## Authentication between services

Services trust each other on the overlay (no public access per C3). Optional mTLS in future phases. Each request carries:
- `X-Internal-Caller: <service-name>` (for tracing)
- `X-Trace-Id: <uuid>` (for end-to-end Langfuse correlation)
- `X-User-Id: <user-id>` (forwarded from public-api after JWT validation)

Downstream services trust X-User-Id without re-validating (per E6).

## public-api → orchestrator

> **READ THIS FIRST.** The shape below is the INTERNAL RPC payload that
> the orchestrator returns to public-api. It is NOT what mobile clients
> receive. Public-api wraps this payload inside the locked
> `ApiResponse<MessageResponse>` envelope (per `00-api-contract.md`)
> before returning to mobile. Internal callers and mobile clients see
> DIFFERENT outer shapes; only the inner `MessageResponse` fields are
> byte-shape-identical to chat-ai's existing parity contract. Do not
> copy this internal-bare shape into any handler that returns to mobile.

```
POST http://yral-rishi-agent-conversation-turn-orchestrator:8000/v1/turn

Request:
{
  conversation_id: string,         // UUID of the conversation row;
                                   // orchestrator joins on this to find
                                   // user_id + ai_influencer_id (the
                                   // latter feeds the Soul-File lookup).
                                   // Orchestrator MUST verify the
                                   // conversation row's user_id equals
                                   // X-User-Id below before responding;
                                   // mismatch returns 403 (a caller may
                                   // never query another user's
                                   // conversation by id-guessing).
  user_message: string,            // Raw text the user typed. PII per
                                   // H6 — log only LENGTH, never the
                                   // value.
  media_urls: string[] | null,     // Attachment URLs from the user's
                                   // message (images/audio/video). REQUIRED
                                   // by A8 multi-modal parity; do NOT
                                   // drop. Public-api forwards these
                                   // inline so the orchestrator does not
                                   // need a second DB read per turn.
  client_message_id: string | null // Optional client-side dedup id the
                                   // mobile app may attach to the user
                                   // message; orchestrator echoes it
                                   // onto persisted user-msg traces but
                                   // assistant replies do NOT carry one.
}

Headers (ALL three required on every call):
  X-User-Id          Forwarded from public-api after JWT validation, per E6.
                     Orchestrator MUST cross-check this against the
                     conversation_id's owning user before doing any work;
                     reject 403 on mismatch.
  X-Idempotency-Key  REQUIRED from day 1, per F10 (default-on for every
                     non-GET endpoint). Same key + same user/conversation
                     within 24h MUST return the previously created
                     assistant MessageResponse from Redis without a
                     second LLM call. Implementations MUST ship the
                     Redis-backed dedup at the same time as the route
                     itself — F10 forbids deferring it.
                     Backend MUST be the C11 Sentinel-aware Redis client
                     (NOT `redis.asyncio.Redis.from_url(...)` directly).
                     Dedup MUST be atomic against concurrent duplicate
                     requests (e.g. `SET NX` in-progress lock + completed
                     payload, or Lua/transaction). Reject 400 if the
                     header is missing.
  X-Request-Id       Per Langfuse correlation, D4.

Response (internal-bare): JSON MessageResponse — the orchestrator returns
the bare object below to public-api over the internal RPC. The mobile-
facing endpoint (`POST /api/v1/chat/conversations/{id}/messages` on the
public-api) wraps this object in `ApiResponse<MessageResponse>{success,
msg, error, data}` per `00-api-contract.md`. The inner field names and
types below are byte-shape-identical to chat-ai's existing parity
contract — do not mutate them, only the outer wrapper differs by hop.
{
  id: string,                        // fresh UUID per assistant reply
  conversation_id: string,           // echoes the request's conversation_id
  role: "user" | "assistant",        // orchestrator always returns "assistant"
  content: string,                   // the assistant reply text
  media_urls: string[] | null,       // attachment URLs the assistant
                                     // returns (null today; real Day-5+
                                     // may include generated images)
  client_message_id: string | null,  // null on assistant replies; copied
                                     // from request on user messages
                                     // (public-api owns user-msg persist)
  created_at: string,                // ISO8601 UTC, "YYYY-MM-DDTHH:MM:SSZ"
  count_toward_paywall: boolean      // E7 paywall counter; safety-blocked
                                     // turns flip false once H4/H5 land
}
```

Used: every chat turn. Plain JSON response (NOT SSE) per A16 — mobile
parity requires byte-shape-identical to chat-ai's existing
`POST /api/v1/chat/conversations/{id}/messages` contract. The v1 path
stays plain-JSON forever for parity stability. SSE streaming per E2
(first-token <200ms p95 target) lives at a separate `POST /v2/turn-stream`
path behind a feature flag per the Session-4 agent definition — the v1
JSON shape above never silently mutates into a stream.

Naming note (B1 + B2 + Rishi 2026-05-19): the response model is
`MessageResponse`, NOT `MessageDto`. The "DTO" abbreviation is not on the
B2 allowed-abbreviation list and the project's English-naming rule applies
to Python class names, not only JSON fields. Same rule applies to every
other internal response model owned by v2 (`InfluencerResponse`, etc.).
EXCEPTION — external contracts we consume but do not own (e.g.
yral-billing's `ChatAccessDataDto` later in this doc) keep their source-
party name in the doc + JSON shape; the internal Python class that
deserializes it may use a B-rules-compliant alias, but the wire format
and the doc reference both keep the external-owned name.

Source of truth: `yral-rishi-agent-conversation-turn-orchestrator/app/models/turn.py`
(`RunTurnRequest` + `MessageResponse` Pydantic models) and
`yral-rishi-agent-conversation-turn-orchestrator/app/run_turn.py`
(`POST /v1/turn` handler). Update this section if those models change.

## public-api → influencer-and-profile-directory

```
GET http://yral-rishi-agent-influencer-and-profile-directory_service:8000/v1/influencers
  ?limit=<int 1..100>
  &offset=<int >=0>
→ list[InfluencerResponse]    [PROPOSED — see DEP-013]

GET http://yral-rishi-agent-influencer-and-profile-directory_service:8000/v1/influencers/{id}
→ InfluencerResponse

POST .../v1/influencers (create flow)
→ InfluencerResponse

PATCH .../v1/influencers/{id}/system-prompt
→ InfluencerResponse

DELETE .../v1/influencers/{id}
→ {}
```

Mostly thin proxy — public-api forwards to influencer-directory.

**Headers on every request** (4 internal-call headers per public-api's
`directory_client._internal_headers()`): `X-User-Id` (forwarded from
the public-api JWT-validated user); `X-Internal-Caller`
(`yral-rishi-agent-public-api`); `X-Request-Id` + `X-Trace-Id` (both
carry the same value from public-api's `request_id_middleware`). No
`X-Idempotency-Key` on GETs (stateless reads; F10's per-endpoint
opt-out applies). The directory MAY mTLS-verify the caller by SAN
when the Day-N internal-mesh-mTLS lands; current shape relies on
the same-overlay-mesh trust model that orchestrator → soul-file
already uses.

**The list endpoint (`GET /v1/influencers?limit&offset`) is the
PROPOSED contract from DEP-013 (Session 3, 2026-05-22).** Session 4
ratifies when they build the real endpoint at
`yral-rishi-agent-influencer-and-profile-directory/app/api/`, or
pushes back with a different shape and Session 3 adjusts public-api's
wrapper accordingly. The by-id + create + edit + delete shapes are
the previously-declared contract on main.

**Pagination semantics:**
- `limit`: 1..100 plain int (matches yral-mobile
  `ChatRemoteDataSource.kt:50-70` listInfluencers contract — plain
  offset/limit, no cursor). Default `20`.
- `offset`: 0-indexed non-negative int. Default `0`.
- Response is a flat `list[InfluencerResponse]` — no `total_count`
  or `next_offset` wrapper today; mobile derives "more pages
  available" client-side from `len(items) == limit`. Future PR can
  add a `count` header or wrap the body if the catalog grows beyond
  the natural one-shot read.

**Note: stack-service DNS naming.** The Swarm DNS name for the
directory service is `<stack>_<service>` →
`yral-rishi-agent-influencer-and-profile-directory_service` (per
project.config + the compose service name `service`). Previous
version of this section dropped the `_service` suffix; updated here
to match the actual Swarm DNS resolution.

## orchestrator → soul-file-library

```
GET http://yral-rishi-agent-soul-file-library:8000/composed-prompt
  ?influencer_id=<id>
  &user_segment=<new|paying|dormant>

→ {
    layered_prompt: string,    // 4 layers concatenated
    version_pin: string,       // for rollback if needed
    cache_hit: boolean
  }
```

Hot path. Must be <5ms warm cache hit (E1 budget).

## orchestrator → user-memory-service

```
GET http://yral-rishi-agent-user-memory-service:8000/context
  ?user_id=<id>
  &influencer_id=<id>
  &recent_messages=10

→ {
    semantic_facts: [{fact_text, confidence}],
    user_profile: {tone_preference, language, ...},
    recent_episodes: [...]
  }

POST http://yral-rishi-agent-user-memory-service:8000/extract-async
{
  user_id, message_id, content
}
→ 202 Accepted (fire and forget)
```

`/context` is hot path (parallel-fetched per Section 2.7). `/extract-async` is fire-and-forget for memory extraction.

## public-api → user-memory-service

```
GET http://yral-rishi-agent-user-memory-service:8000/v1/conversations/{conversation_id}
  Headers: X-User-Id + X-Internal-Caller +
           X-Request-Id + X-Trace-Id (4 internal-call headers;
           no X-Idempotency-Key on stateless GETs)

→ 200 {
    id: string,
    user_id: string,
    ai_influencer_id: string,
    conversation_type: string,
    created_at: string,
    last_message_at: string,
    message_count: integer,
    soft_deleted_at: string|null,
    last_message: {...}|null
  }
→ 404 (conversation not found, soft-deleted, OR owned by different
       user — tenant-isolation 404; never 403 + never leaks
       existence of other users' conversations)
```

**Used by**: `public-api`'s `send_message` handler (`app/api/chat_routes.py`) at request time on every `POST /api/v1/chat/conversations/{id}/messages`. The handler derives `ai_influencer_id` from the conversation lookup, then forwards it as the per-request `influencer_id` field in the orchestrator `run_turn` call.

### Architectural decision — Phase-1 ratification (2026-05-24)

Codex CONCERN on PR #141 round-5 surfaced the cross-service boundary question: should `public-api` call `user-memory-service` directly to derive `influencer_id`, OR should `orchestrator` own the derivation (calling `user-memory-service` itself)?

**Coordinator decision: public-api → user-memory direct call is the ratified Phase-1 architecture.**

Reasoning:
- Both architectures honor the trust-boundary requirement (Codex CONCERN on PR #131): `influencer_id` derived from the conversation record, never from client-supplied body/header/query
- Public-api derivation keeps orchestrator's contract minimal — orchestrator is "process the turn given an influencer," not "look up which influencer" + "load context for influencer." Cleaner separation of concerns.
- Yesterday's PR-B2 plan was approved on this shape; switching mid-flight would add 1-2 days rework for a CONCERN (not BLOCKER) where both architectures meet the parity floor
- Phase 1 timeline pressure: parity-correctness is the production-readiness floor (at Rishi's A6 discretion), not architectural-elegance optimization

**Alternative (NOT chosen for Phase 1)**: orchestrator owns the derivation. Public-api forwards just `conversation_id`; orchestrator queries user-memory directly. Cleaner "service owns its own data path" + fewer cross-service hops in some workflows. Captured as a **post-production-traffic re-evaluation candidate (at Rishi's A6 discretion)**: once v2 is in steady state + observability shows real call patterns, re-evaluate whether moving the user-memory call into orchestrator simplifies the request graph enough to justify the rework. Filed under the post-production-traffic architectural sweep that DEFER'd memory `feedback_v2_greenfield_freedom_1000x_better_no_chat_ai_inheritance_2026_05_22.md` tracks.

This decision is binding for Phase 1. Future PRs touching the public-api ↔ user-memory boundary cite this section; Codex re-scoring should treat this as the cross-service boundary ratification per I9.

### Latency rationale — E1 50%-faster-than-chat-ai compliance

Codex PR #145 round-1 raised a real performance question: does this synchronous public-api → user-memory call on the user-interactive hot path comply with E1's hard 50%-faster latency target? And does it duplicate orchestrator's existing `/context` call?

**Not duplicative — different endpoints serve different purposes:**
- `public-api → user-memory: GET /v1/conversations/{id}` — returns conversation metadata (id, user_id, ai_influencer_id, message_count, last_message). Used by public-api to derive `influencer_id` + verify tenant ownership (404 if not-owned).
- `orchestrator → user-memory: GET /context?user_id&influencer_id&recent_messages=10` (per the section above) — returns semantic memory facts + user profile + recent episode summaries. Used by orchestrator to enrich the LLM prompt.

The two calls fetch DIFFERENT data shapes. The `/v1/conversations/{id}` call is a single-row Postgres SELECT (~1-3ms warm + ~5-10ms cold including network round-trip). The `/context` call is a multi-table join + aggregation (heavier, but its own bucket; was already in the design).

**E1 50%-faster target compliance approach:**

1. **p95 budget**: GET `/v1/conversations/{id}` p95 ≤ 15ms (Postgres index hit on conversation_id PK + asyncpg connection pool reuse). The total send-message hot-path p95 budget allocates ~30ms to all user-memory calls combined (this one + orchestrator's `/context`); we're well inside.

2. **Timeout/fallback behavior**: per Session 3's PR-B2 implementation, user-memory unreachable / 404 / 5xx / timeout → public-api returns envelope-shaped 503 with `user_memory.call.failed=<mode>` Sentry tag. NO silent fallback to a stale or default influencer_id — failing loudly preserves the trust-boundary semantic.

3. **CI/Sentry latency observability**: Session 1's daily Sentry baseline pull from chat-ai (per CONSTRAINTS E1 + I7 + memory `project_v2_first_build_task_sentry_baseline_pull`) establishes the chat-ai latency baseline at `latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/daily-baseline.csv`; v2 latency observability via Langfuse traces every `POST /api/v1/chat/conversations/{id}/messages` turn with cross-service-call breakdowns. The 50%-faster comparison is enforced via Gate A2 (the controlled benchmark runner described below), NOT per-PR in shared CI (per J2 zero-flake + Codex round-4 CONCERN).

### Measurable acceptance gate (CONCERN from PR #145 round-2 — addressed round-3)

Codex correctly flagged that the p95 numbers above are designed budgets, not measured gates. Treating this architecture as safe requires a concrete pre-merge / pre-production-traffic acceptance condition with owners and fail-stop semantics (where any reference to production traffic stays at Rishi's A6 discretion). **This is the gate**:

The canonical send-message endpoint per `00-api-contract.md:35` is `POST /api/v1/chat/conversations/{id}/messages` (mobile-facing; matches yral-mobile's `ChatRemoteDataSource`). All references in the gates below use this exact path — PR #145 round-3 and round-4 mistakenly used `POST /v1/send-message` as a shorthand; round-5 corrects to the canonical contract path.

**Gate A1 — per-PR PUBLIC-API integration-test SMOKE check (owner: Session 3, lives in `yral-rishi-agent-public-api/tests/integration/`)**

Required by Phase 1 parity smoke (Day 12-13 target). Public-api side of the boundary only — tests what public-api can validly observe about its own RPC call to user-memory. Implementation:

- Add an integration test that spins up real public-api + **contract-stub/fake HTTP servers for EVERY downstream service public-api calls** (NOT real testcontainers — see boundary note below) on ephemeral ports. The full downstream set on the send-message hot path:
  - `user-memory` (Session 5's service) — fake on ephemeral port, injected as `USER_MEMORY_SERVICE_BASE_URL`
  - `orchestrator` (Session 4's service) — fake on ephemeral port, injected as `ORCHESTRATOR_SERVICE_BASE_URL` (orchestrator handles the `POST /v1/turn` call public-api makes for the actual chat reply)
  - `yral-billing` (external, Ravi's service) — fake on ephemeral port, injected as `YRAL_BILLING_BASE_URL` (called per E7 for paywall counter; cached 60s but fresh on first hit in a 500-call burst)
  - Any other downstream public-api calls on the send-message hot path discovered when PR #141 + the implementation code lands. **Stub EVERY one** so the test is isolated from cross-session implementation churn.
- Each pytest fixture binds to port 0; OS assigns a free port; fixture exposes the generated base URL and injects the corresponding `*_BASE_URL` env var into public-api's config for the test run. Tests then issue N=500 `POST /api/v1/chat/conversations/{id}/messages` calls (the per-request `influencer_id` lookup path once PR #141 lands).
- The test is a **SMOKE check at PR-CI tier, not a hard p95 fail-stop gate** (Codex round-4 correctly flagged that ms-scale p95 thresholds in shared GitHub-runner CI risk J2 zero-flake violations). The PR-CI tier verifies what public-api can validly observe at the RPC boundary:
  - Asserts the public-api → user-memory call is INSTRUMENTED with a Langfuse span (test reads the in-process span exporter on the public-api side).
  - Asserts the RPC contract shape — request **path, headers (X-User-Id, X-Internal-Caller, X-Trace-Id), and response body** match the proposed shape; response Pydantic model parses cleanly (contract-level validation against the HTTP wire shape, not implementation peek). Note: `GET /v1/conversations/{id}` has no request body — fake server validates path + headers + emits the documented JSON response.
  - Asserts TIMEOUT + ERROR + 5xx behavior — the fake user-memory server is scripted to return 504/500/sleep-past-timeout on specific test inputs; public-api returns the documented envelope-shaped 503 with `user_memory.call.failed=<mode>` Sentry tag (per Session 3's PR-B2 spec). NO silent fallback.
  - Asserts ENVELOPE MAPPING — the (fake) user-memory response maps correctly into public-api's wire-shape envelope as defined in `00-api-contract.md:35` for `POST /api/v1/chat/conversations/{id}/messages`: the JSON wire shape is `ApiResponse<MessageDto>` (the parity-locked external contract name kept for mobile-side stability per A8 + the existing chat-ai serialized field names). The internal Python class consumed inside public-api is `MessageResponse` per this file's naming convention block (lines 108-117 — internal v2 class names follow B2 + Rishi 2026-05-19; the `Dto` suffix only persists in WIRE shapes locked to existing mobile parity). The test asserts the JSON wire shape produced is `ApiResponse<MessageDto>` regardless of the internal Python class name.
- **Boundary note (Codex round-8 CONCERN, correctly flagged)**: Using the REAL user-memory-service testcontainer in public-api's CI couples Session 3's per-service CI to Session 5's service implementation — cross-session test ownership leakage. Public-api's CI exercises the HTTP CONTRACT (path/headers/response shape) via a contract stub/fake; the REAL user-memory testcontainer lives in Gate A_user_memory inside Session 5's CI. Both gates together cover the boundary: Session 3's side validates the contract from the caller's perspective; Session 5's side validates the implementation from the implementer's perspective. Neither side inspects the other's internals.
- Implementation: use a lightweight in-test HTTP fake server (e.g. `pytest-httpserver`, `respx`, or an asyncio `aiohttp.web` instance fixture) declared in `yral-rishi-agent-public-api/tests/integration/conftest.py`; the fake's request/response shapes are kept in lock-step with the contract via a shared schema-validation step (e.g. `pydantic` model imported from a shared contracts package, OR a copy-with-test-that-asserts-equality if no shared package exists yet).
- Gate this SMOKE integration test into the per-service CI workflow as a **required check** for PRs touching `yral-rishi-agent-public-api/`.

**Gate A_user_memory — per-PR USER-MEMORY-SERVICE internal test (owner: Session 5, lives in `yral-rishi-agent-user-memory-service/tests/`)**

Required by Phase 1 parity smoke (Day 12-13 target). User-memory side of the boundary only — tests internal SQL + pool behavior in Session 5's scope, NOT exposed to public-api's tests (Codex round-6 CONCERN correctly flagged cross-service test boundary leakage). Implementation:

- Add an integration test that spins up real user-memory-service + real Postgres via testcontainers, issues N=500 `GET /v1/conversations/{id}` calls direct to user-memory (no public-api involvement).
- Asserts the asyncpg query uses the `conversation_id` PK INDEX (test reads `EXPLAIN ANALYZE` output of the underlying SQL — this is Session 5's internal SQL, tested inside Session 5's scope).
- Asserts the asyncpg connection POOL is reused across N=500 calls (test reads asyncpg's pool stats — Session 5's internal pool, tested inside Session 5's scope).
- Asserts the response Pydantic model serializes cleanly for the contract shape.
- Gate this test into the per-service CI workflow as a **required check** for PRs touching `yral-rishi-agent-user-memory-service/`.

**Gate A2 — controlled benchmark runner, MERGE-BLOCKING (owner: Session 1, lives in `yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/`)**

Codex round-6 BLOCKER correctly flagged that E1 explicitly requires a CI latency gate that blocks merge when a user-interactive endpoint misses the 0.5× chat-ai baseline. This architecture adds a synchronous hot-path call, so the hard latency gate cannot be deferred to nightly/comment-only. Round-7 makes Gate A2 the **required, merge-blocking** controlled-runner gate for the send-message hot path.

The gate lives at `latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/` (this path is nested under `yral-rishi-agent-plan-and-discussions/` in the actual repo, but the `scripts/` subfolder is **Session 1's owned territory** per the explicit carve-out in `multi-session-parallel-build-coordination/01-SESSION-SHARDING-AND-OWNERSHIP.md:70` and the lint-scope-violations.yml workflow allowlist at line 89: `SESSION_PATHS[1]="bootstrap-scripts-for-the-v2-docker-swarm-cluster/|yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/|...`. The docs-around-the-scripts are coordinator-owned; the scripts themselves are Session 1's — same DEP-001 fix Session 1 caught on 2026-05-04). Codex round-7 mis-flagged this as an I8 violation by reading only the umbrella path prefix; the carve-out is explicit and CI-enforced.

The gate runs in TWO modes for J2 zero-flake compliance — stable hardware ALONE doesn't help if external LLM latency variance is in the measured path (Codex round-7 CONCERN, correctly flagged):

**Gate A2-PR (merge-blocking, deterministic mock LLM)** — TWO-PART OWNERSHIP per Codex round-12 CONCORN (correctly flagged that workflow files + branch protection are coordinator-scope, NOT Session 1's):

*Session 1 deliverable (lives in `yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/`)*:
- The benchmark script itself: spins up the deterministic mock LLM scaffold, issues N=500 calls, computes p95s, exits 0/1 based on the fail-stop thresholds.
- Owns the script's runtime behavior + the MOCK_LLM_FIXED_LATENCY_MS constant + the threshold formula.

*Coordinator deliverable (lives in `.github/workflows/` per I9 coordinator-scope)*:
- The GitHub Actions workflow file (`.github/workflows/gate-a2-pr-benchmark.yml` or similar) that wires the Session 1 script into `pull_request` event triggers.
- Path filters: `yral-rishi-agent-public-api/**`, `yral-rishi-agent-user-memory-service/**`, AND the orchestrator/billing call-paths if they're touched (`yral-rishi-agent-conversation-turn-orchestrator/app/run_turn.py`, any code consuming yral-billing's chat-access endpoint).
- Workflow_dispatch is also available for manual re-runs but is NOT the primary trigger — the `pull_request` path filter is what makes the gate automatically protect every PR touching the hot path, satisfying E1's hard CI gate requirement (Codex round-10 BLOCKER, correctly flagged).
- Branch protection rule update (via GitHub repo settings — owned by coordinator at the repo-admin level) to mark this workflow as a REQUIRED check on the send-message hot path.
- LLM provider replaced with deterministic mock (fixed-latency mock client at a known response time, e.g. always 50ms) so the measured p95 isolates infrastructure variance ONLY (Postgres, asyncpg pool, network, public-api → user-memory hop)
- Same N=500 `POST /api/v1/chat/conversations/{id}/messages` call pattern on stable-resource hardware (self-hosted GitHub Actions on a dedicated worker OR scheduled job on rishi-4/5/6 cluster, resource-isolated)
- FAIL the workflow check if measured p95 of the isolated public-api → user-memory call exceeds **15ms** (budgeted ceiling)
- FAIL the workflow check if `(measured_full_p95 - MOCK_LLM_FIXED_LATENCY_MS) > infrastructure_budget` where `infrastructure_budget = 0.5 × chat-ai-baseline-p95`. The subtraction is applied on the MEASURED side (not the threshold side) — this isolates v2 infrastructure cost from the mock's contribution without double-counting. Worked example: chat-ai baseline p95 = 400ms → infrastructure_budget = 200ms. If the mock fires at MOCK_LLM_FIXED_LATENCY_MS = 50ms and measured_full_p95 = 245ms, the check evaluates `(245 - 50) > 200` → `195 > 200` → FALSE → PASS. If measured_full_p95 = 260ms, `(260 - 50) > 200` → `210 > 200` → TRUE → FAIL. (Codex round-11 CONCERN correctly flagged the round-7-through-round-11 formulation as double-counting; round-12 fixes the formula.)
- Marked as a REQUIRED check in the merge protection rule for the send-message hot path — implementation PR cannot merge until Gate A2-PR passes. J2 zero-flake compliance comes from BOTH stable hardware AND deterministic mock LLM.

**Gate A2-NIGHTLY (telemetry, NOT merge-blocking, real LLM provider)**:
- Run nightly on a schedule (catches baseline-drift + real-provider latency regressions early)
- Same N=500 calls but against real LLM providers (Gemini Flash for default, OpenRouter for Tara/NSFW per A10)
- Records full real-world p95 telemetry into Langfuse + the daily-baseline.csv
- Surfaces alerts via Google Chat webhook per D6 if p95 drifts > 10% week-over-week
- Does NOT block merges — real-provider variance makes this telemetry, not gating

Sources:
- baseline file: `yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/daily-baseline.csv` (maintained by Session 1 per the Sentry-baseline-cron)
- mock-LLM latency value: declared in the Gate A2-PR workflow's environment as `MOCK_LLM_FIXED_LATENCY_MS`; same value in PR runs so the subtraction is deterministic

**Sequencing note**: Gate A2-PR cannot fire until Session 1's controlled benchmark runner + deterministic mock LLM scaffold exist. Session 1's deliverable to stand both up is on the critical path for Phase 1 parity smoke (Day 12-13). If Session 1 cannot land both by Day 11, coordinator surfaces the conflict to Rishi as either: (a) scope slip on Phase 1 parity smoke, (b) accept Gate B (pre-production-shape rehearsal) as the SOLE latency gate temporarily until A2-PR lands post-merge, or (c) change E1's strict CI-gate language to allow time-boxed post-merge enforcement. None of these is coordinator's call to make unilaterally per A6 + the no-changes-to-E1-without-Rishi rule.

**Gate B — pre-production-traffic production-shape rehearsal at Rishi's A6 discretion (owner: Session 1, lives in `bootstrap-scripts-for-the-v2-docker-swarm-cluster/chaos-tests/`)**

Codex round-12 BLOCKER correctly flagged that the chaos-tests folder is Session 1's scope (per ownership doc), NOT Session 4 + coordinator's. Round-13 corrects: Session 1 owns Gate B since it's a cluster-level shadow-traffic exercise + lives in their existing chaos-tests/ scope. Required before any Rishi-approved production traffic (no scheduled date per A6). Implementation:

- Before any Rishi-approved production traffic, Session 1 runs a 1-hour shadow-traffic rehearsal at projected day-0 RPS (chat-ai current ~25K msgs/day = ~0.3 RPS sustained, ~2-5 RPS burst) against the live rishi-4/5/6 cluster.
- Pull Langfuse p95 for the public-api → user-memory call span.
- BLOCK Rishi-approved production traffic if measured p95 exceeds 15ms OR if total `POST /api/v1/chat/conversations/{id}/messages` p95 exceeds 0.5× chat-ai baseline. Coordinator surfaces this to Rishi as the go/no-go criterion (per A6, the decision sits with Rishi).

**Revisit trigger — combine-candidate re-evaluation:**

If Gate A2 or Gate B fails at the 15ms public-api → user-memory threshold (i.e., the call is hotter than budgeted), the post-production-traffic combine-candidate below (extending `/context` to subsume the lookup) becomes a Phase-2 must-do rather than a re-evaluation candidate. The decision triggers a one-day rework spike across Sessions 3/4/5 (at Rishi's A6 discretion for when the rework lands).

**Why per-PR Gate A (SMOKE) is not pre-merge of this contract doc:** the contract doc ratifies the architectural shape. The SMOKE check requires the actual implementation code from PR #141 (per-request `influencer_id`). PR #141 is in flight today (Day 8); the SMOKE check lands as part of Session 3's integration-test batch by Day 11 at the latest, two days before the Day 12-13 parity smoke target. Gate A2 (the hard p95 gate) depends on Session 1's benchmark-runner deliverable — separate work track, no Day 11 blocker.

This acceptance gate set replaces the vague "fires per-PR via the per-service-ci latency check" language in #3 above with concrete tiered fail-stop thresholds, owners, and J2-compliant test placement.

**Combine candidate (NOT chosen for Phase 1)**: extend `user-memory's /context` endpoint to accept `conversation_id` instead of `influencer_id` + return BOTH the memory context AND the conversation's influencer_id in one shape. Then orchestrator's existing `/context` call subsumes the public-api lookup; public-api just forwards `conversation_id` to orchestrator + orchestrator does the single user-memory call. Cleaner single-hop architecture. NOT chosen for Phase 1 because:
- Requires Session 5 to add `conversation_id` parameter handling to `/context`
- Requires Session 3 to remove the user-memory call from public-api
- Requires Session 4 to update orchestrator's `/context` call shape
- Adds 1-2 days rework where the current shape meets the E1 p95 budget
- Captured as **post-production-traffic re-evaluation candidate at Rishi's A6 discretion** alongside the orchestrator-derives alternative above

The current 2-call shape stays for Phase 1. Post-production-traffic sweep (at Rishi's A6 discretion for when it runs) evaluates both alternatives (single-call consolidation + orchestrator-derives) once real cluster latency telemetry shows actual call patterns + bottlenecks.

## orchestrator → content-safety-and-moderation

```
POST http://yral-rishi-agent-content-safety-and-moderation:8000/check-input
{
  user_id, message_content
}
→ {
    safe: boolean,
    crisis_detected: boolean,
    flag_reason: string | null
  }

POST .../check-output
{
  user_id, response_content
}
→ same shape
```

Pre-LLM check on user message + post-LLM check on response. Per H4, must be live before any real-user canary.

## public-api → yral-billing (EXTERNAL — Ravi's service)

```
GET https://yral-billing.../google/chat-access/check
  ?user_id=<id>&bot_id=<id>

→ ApiResponse<ChatAccessDataDto>  // External contract from yral-billing
                                  // (Ravi-owned). Keep the source-party
                                  // name in this doc + JSON wire shape.
                                  // Per E7. Our internal Python class
                                  // may alias to a B-rules-compliant name
                                  // (e.g. ChatAccessData), but the
                                  // serialised JSON field and class
                                  // identifier mirror yral-billing's
                                  // existing release contract.
```

Cached in v2 Redis 60s per E7. Per D1 — yral-billing is external; we consume.

## payments-and-creator-earnings → yral-billing (EXTERNAL)

```
GET https://yral-billing.../transactions?bot_id=<id>&since=<timestamp>
→ Transaction[]
```

Read-only mirror. v2 caches earnings rollups; we never write to yral-billing's ledger.

## All services → Sentry (sentry.rishi.yral.com)

Standard Sentry SDK. DSN per service from secrets.yaml. Tag `service=<name>` per D3. Per A7 + C4 — NEVER apm.yral.com.

## All services → Langfuse (rishi-6 self-hosted)

Standard Langfuse SDK. Public + secret keys from Vault per D8 (shared, not per-service).

Every LLM call auto-traced per D4 + middleware in template.

## Event stream (Redis Streams)

Services emit + consume via overlay `yral-v2-data-plane`. Stream keys:

| Stream | Producer | Consumer(s) |
|---|---|---|
| `events:user.message.sent` | public-api | analytics, memory-extractor |
| `events:turn.completed` | orchestrator | analytics, bot-quality-scorer |
| `events:memory.candidate` | orchestrator | memory-service |
| `events:influencer.created` | influencer-directory | analytics |
| `events:safety.flagged` | content-safety | analytics, audit-log |
| `events:payment.completed` | payments | analytics, earnings rollups |

Standard envelope:
```json
{
  "event_id": "uuid",
  "event_type": "user.message.sent",
  "timestamp": "ISO8601",
  "user_id": "...",
  "data": { ... }
}
```

## Failure modes

- Downstream timeout → return graceful fallback (e.g., orchestrator without memory enrichment)
- Downstream 5xx → log to Sentry, return `service_unavailable` to caller
- Network partition → Patroni/Sentinel handle stateful; stateless services already replicated 3×
