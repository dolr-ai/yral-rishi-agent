# Session 4 STATE — Orchestrator + Soul File + Influencer Directory

> Updated: 2026-05-18 (Day-4 Soul File Library PR opened — first stateful v2 service for Session 4; 20/20 tests green incl. byte-identity × 5 reps).
> Updated: 2026-05-18 (Day-2 `POST /v1/turn` RPC handler PR opened; Day-1 PR #95 merged earlier same day).

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 4. I own **three services** that together implement the conversation-turn business logic of v2:

1. **yral-rishi-agent-conversation-turn-orchestrator** — runs the actual LLM turn for each chat message. Session 3 calls my `run_turn(...)` RPC; I do the safety stack (H5 prompt-injection defense → H4 crisis routing → A10 NSFW routing) → LLM call → return JSON `MessageDto` (NOT SSE on the v1 path per A16 parity).
2. **yral-rishi-agent-soul-file-library** — Postgres-backed Soul File store. CRUD endpoints for AI Influencers' personality definitions. Per B4 product vocab: NEVER say "system prompt" in code/comments/internal naming (only the API path `/system-prompt` is kept for chat-ai parity).
3. **yral-rishi-agent-influencer-and-profile-directory** — Postgres-backed catalog of AI Influencers + their profile metadata. Read-heavy; Redis-cached for read latency (per the API contract's `GET /api/v1/influencers` Cache-Control 300s + the `GET /api/v1/influencers/{id}` per-influencer caching note). NOT to be confused with E7 (which is specifically yral-billing's 60s access-check cache).

Full agent definition: `.claude/agents/session-4-orchestrator.md`.

## LAST THING I DID

**2026-05-18 — Day 4 Soul File Library PR opened.** First stateful v2 service for Session 4. Single `soul_file_layers` table (per A2.1 — one table for all 4 layers) + Alembic migration with seeds for L1+L2+L4 (L3 deferred to Day-4.5 data port per A4 — ALL data MUST port from chat-ai) + asyncpg-backed repository + 4-layer composer with byte-stable prefix + FastAPI `GET /composed-prompt` route + testcontainers-postgres pytest suite. **20/20 tests PASSED in 3.81s** on Python 3.12.13 inside `python:3.12-slim` with Docker-managed Postgres 17. Byte-identity contract verified across 5 reps. Alembic upgrade ↔ downgrade round-trips cleanly.

Two pushbacks raised before code per I6:
1. F2 citation drift — CONSTRAINTS F2 is the hetzner-template-freeze row, not Soul-File. PR body cites E8/F8/A4/F3/B4/A2.1/C7/D8 — note: PR body originally cited F11 here; that was wrong (F11 = feature flags); corrected in PR-#104 round-3 fixup to A4 (data port) instead; DEP-005 raised for coordinator clarification.
2. Schema spec gap — added `archetype TEXT NULL` column to bridge L3 → L2 composer lookup; smallest delta from directive's spec.

Empirical proof:
- pytest: 20/20 PASSED in 3.81s (asyncio.AUTO mode, function-scope loop)
- Byte-identity ×5 reps of `compose(influencer_id, 'new')` — all bytes-equal
- Golden-file diff: matches `tests/fixtures/composer_golden_layer_output.txt` verbatim
- Alembic up → down → up cycle clean
- Partial-unique-index correctly rejects dual-current via `asyncpg.UniqueViolationError`
- HTTP shape matches `interface-contracts/01-internal-rpc-contracts.md` (3 fields: layered_prompt / version_pin / cache_hit)

## CURRENT TASK

Day-4 PR open + awaiting CI + Codex + Rishi-YES. NOT auto-merge eligible under I14 (adds Postgres schema + Python code + tests; fails the ".md / test / lint / comment-only" gate). Base = `main` per directive (different service folder than orchestrator; no dep on PR #96/#100).

Progress: Day 1 → 100% (PR #95 merged); Day 2 → 100% (PR #96 open); Day 3 → 100% (PR #100 open, based on #96); Day 4 → 100% (PR opened this turn, base=main).

## NEXT 3 PLANNED ACTIONS

1. Day 4.5 — A4 data port: migrate chat-ai's `ai_influencers.system_prompt` → `soul_file_layers` Layer 3 rows. Requires Rishi YES per A14 (live chat-ai read). Likely a separate small PR.
2. Day 5 — Orchestrator wires real LLM calls (Tara → OpenRouter; default → Gemini Flash; per A10 `is_nsfw=true` → OpenRouter; per H4 crisis → Claude with Anthropic safety system). Real LLM flows THROUGH the Day-3 safety stack unchanged. Day-2 stub stays accessible in non-prod for diagnostics.
3. Day 6 — Influencer directory (yral-rishi-agent-influencer-and-profile-directory): Postgres schema + endpoints + Redis-cached reads per E7. Different service folder; orthogonal to soul-file-library.

## BLOCKERS

None hard. DEP-004 (interface-contracts SSE→JSON) + DEP-005 (F2 citation drift) both open, both coordinator-handled, both non-blocking.

## PENDING PRs (mine)

- `session-4/day-4-soul-file-library-postgres-schema-and-composer` — opens this turn (Day-4 Soul File Library). Base=`main`. 20/20 tests green incl. byte-identity × 5 reps. Not auto-merge eligible.
- `session-4/day-3-safety-stack-middleware` — PR #100 (Day-3 safety stack). Base=PR #96 branch. 19/19 tests green.
- `session-4/orchestrator-run-turn-rpc-handler` — PR #96 (Day-2 run_turn skeleton). Base=`main`. 9/9 tests green.
- `session-4/spawn-three-services-from-template` — **MERGED 2026-05-18** as PR #95 (Day-1 spawn bundle).
**2026-05-18 — Day 2 `POST /v1/turn` PR opened.** Implemented the orchestrator's `run_turn` skeleton on `session-4/orchestrator-run-turn-rpc-handler`. Schema-valid `MessageDto` stub (NOT SSE — per A16 parity + agent def + Rishi green-light), behind two safety gates (`environment != production` AND `enable_run_turn_stub=true`). 9 tests cover 5 happy + 4 error paths; all green on Python 3.12.13 inside `python:3.12-slim`. DEP-004 raised to coordinator to update `interface-contracts/01-internal-rpc-contracts.md`'s stale SSE description.

Empirical proof:
- pytest: 9/9 PASSED in 0.04s (rootdir=/work, pytest-8.3.4, asyncio mode strict)
- FastAPI app-import: `/v1/turn POST` registered alongside the default OpenAPI routes
- Python syntax: all 4 new + 2 modified Python files compile
- Net new strict-code: ~80 lines across `run_turn.py` + `models/turn.py` + 1-field config addition (well under A2.1's 100-line check-in threshold)

## CURRENT TASK

PR open + awaiting CI + Codex + Rishi-YES. NOT auto-merge eligible under I14 (code files added: run_turn.py + models/turn.py + tests/* + config.py modification — fails the ".md / test / lint / comment-only" gate even if under 200 strict-code lines).

Progress: Day 1 → 100% done (PR #95 merged); Day 2 → 100% done (PR opened); Day 3 → 0%.

## NEXT 3 PLANNED ACTIONS

1. Day 3 — Safety stack BEFORE any real LLM call. ORDER per agent def: H5 prompt-injection defense classifier (rule-based for Phase 1; ML for Phase 2) → H4 crisis-detection routing (to Claude w/ Anthropic safety system) → A10 NSFW routing (`is_nsfw=true` → OpenRouter). Each wired as middleware in front of `POST /v1/turn`; each writes its decision to Langfuse trace metadata; default-deny posture.
2. Day 4 — Soul-File library: Postgres schema (`soul_file` table) + Alembic migration + CRUD endpoints (`GET` + `PATCH /soul-files/{influencer_id}`). Tests: insert+read fixture roundtrip; PATCH rejects non-creator; version bumps correctly.
3. Day 5 — Orchestrator wires real LLM calls (Tara + Gemini paths, behind the Day-3 safety stack). Day-2 stub disappears behind the feature flag (flag stays off in production forever; the stub remains accessible in non-prod for diagnostics).

## BLOCKERS

None hard. DEP-004 raised to coordinator (interface-contracts/01-internal-rpc-contracts.md SSE→JSON update) is non-blocking — Session 3 can read `app/run_turn.py` + `app/models/turn.py` directly to see the real shape.

## PENDING PRs (mine)

- `session-4/orchestrator-run-turn-rpc-handler` — opens this turn (Day-2 `POST /v1/turn` skeleton). Includes 9 tests, all green locally. Not auto-merge eligible (adds code; fails I14 doc/test/lint-only gate). Coordinator review expected.
- `session-4/spawn-three-services-from-template` — **MERGED 2026-05-18** as PR #95 (Day-1 spawn bundle). Codex flagged 2 BLOCKER/CONCERN, coordinator confirmed both are template-inherited (not Session 4's introductions); coordinator queuing as DEPs against Session 2.

## CROSS-SESSION DEPS (mine)

- **Inbound expected:** Session 3 will likely raise a DEP-xxx around Day 4 asking for my `run_turn` RPC stub.
- **Outbound expected (Day 10+):** Session 5's user-memory service for conversation-context lookups (last N messages, persona prefs).
- No open deps yet.

## RESUME PROTOCOL REMINDER (every session start)

Per I12 + my agent definition Step B:
1. Read this STATE file
2. Read last 50 lines of SESSION-4-LOG.md
3. Read cross-session-dependencies.md filtered to Session 4 / orchestrator / soul-file / influencer
4. Read MASTER-STATUS.md for cluster-wide context
5. Print CONFIRM-TO-RISHI sentence (template in agent definition)
6. WAIT for Rishi to type `continue` before any Auto-mode action
