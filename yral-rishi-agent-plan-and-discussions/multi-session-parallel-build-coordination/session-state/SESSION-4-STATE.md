# Session 4 STATE — Orchestrator + Soul File + Influencer Directory

> Updated: 2026-05-18 (Day-4 Soul File Library PR opened — first stateful v2 service for Session 4; 20/20 tests green incl. byte-identity × 5 reps).

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
