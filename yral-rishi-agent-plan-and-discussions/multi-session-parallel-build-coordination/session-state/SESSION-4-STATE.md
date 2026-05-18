# Session 4 STATE — Orchestrator + Soul File + Influencer Directory

> Updated: 2026-05-18 (initial scaffold by coordinator before Session 4's first work; Session 4 maintains from here).

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 4. I own **three services** that together implement the conversation-turn business logic of v2:

1. **yral-rishi-agent-conversation-turn-orchestrator** — runs the actual LLM turn for each chat message. Session 3 calls my `run_turn(...)` RPC; I do the safety stack (H5 prompt-injection defense → H4 crisis routing → A10 NSFW routing) → LLM call → return JSON `MessageDto` (NOT SSE on the v1 path per A16 parity).
2. **yral-rishi-agent-soul-file-library** — Postgres-backed Soul File store. CRUD endpoints for AI Influencers' personality definitions. Per B4 product vocab: NEVER say "system prompt" in code/comments/internal naming (only the API path `/system-prompt` is kept for chat-ai parity).
3. **yral-rishi-agent-influencer-and-profile-directory** — Postgres-backed catalog of AI Influencers + their profile metadata. Read-heavy; Redis-cached for read latency (per the API contract's `GET /api/v1/influencers` Cache-Control 300s + the `GET /api/v1/influencers/{id}` per-influencer caching note). NOT to be confused with E7 (which is specifically yral-billing's 60s access-check cache).

Full agent definition: `.claude/agents/session-4-orchestrator.md`.

## LAST THING I DID

**2026-05-18 — Session 4 first-launched.** Pre-work (Step A onboarding + Step B I12 resume protocol) complete. CONFIRM-TO-RISHI printed. Idle pending Rishi's `continue` to start Day 1.

(Coordinator scaffolded this file on 2026-05-18 before Session 4's first PR per the agent definition's "initially scaffolded by coordinator on first launch" clause.)

## CURRENT TASK

Awaiting Rishi's `continue` to start Day 1: spawn the three services from Session 2's template via `new-service.sh`:
1. `bash yral-rishi-agent-new-service-template/scripts/new-service.sh conversation-turn-orchestrator`
2. `bash yral-rishi-agent-new-service-template/scripts/new-service.sh soul-file-library`
3. `bash yral-rishi-agent-new-service-template/scripts/new-service.sh influencer-and-profile-directory`

Progress: 0% (not yet started)
ETA to first PR-ready: ~60-90 min after `continue` (three spawn operations; can bundle into one PR per A2.1 since they share the same shape).

## NEXT 3 PLANNED ACTIONS

1. Day 1 — Spawn the 3 services from template + STATE/LOG seeding entries.
2. Day 2 — Orchestrator `run_turn(...)` RPC handler skeleton. **Return shape: plain JSON MessageDto matching chat-ai parity contract — NOT SSE on the v1 path.** Stub returns SCHEMA-VALID MessageDto behind a feature flag (not for production traffic).
3. Day 3 — Safety stack BEFORE any real LLM call: H5 prompt-injection defense classifier → H4 crisis-detection routing (Claude with Anthropic safety system) → A10 NSFW routing (`is_nsfw=true` → OpenRouter). All three wired in middleware order before Day-5's real LLM enablement.

## BLOCKERS

None hard. Session 3 launches in parallel; coordination happens via cross-session-dependencies.md when Session 3 needs my `run_turn` RPC (expected Day 4).

## PENDING PRs (mine)

(none yet)

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
