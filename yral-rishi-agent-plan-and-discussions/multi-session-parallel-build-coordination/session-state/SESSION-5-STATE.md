# Session 5 STATE — ETL + Tests + Memory
> Updated: 2026-04-29 (PRE-LAUNCH stub by coordinator). Session not yet running.

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 5. I own the bridge between OLD chat-ai (live with all data) and NEW v2 (greenfield, needs to mirror it). I write contract tests that prove v2 is a drop-in replacement, draft the Day 9 ETL plan for Rishi's YES, and eventually (Phase 2) build the user-memory-service with pgvector.

## LAST THING I DID
(none yet — pre-launch stub)

## CURRENT TASK
Awaiting first launch.

## NEXT 3 PLANNED ACTIONS
1. Read pre-work per `.claude/agents/session-5-etl-tests-memory.md`
2. Print CONFIRM-TO-RISHI
3. After "continue": Day 1 — contract test scaffolding in `tests/contract/`

## BLOCKERS
- None for Day 1 work
- Day 9 ETL run BLOCKED until Rishi types YES per A14 (will surface via cross-session-dependencies.md as DEP-001 closer to Day 9)

## PENDING PRs (mine)
None yet.

## CROSS-SESSION DEPS (mine)
- Will need: ETL approval (Rishi YES) on Day 9 per A14 — drafting plan Day 4

## CONFIRM TO RISHI (pre-written for resume)

```
I'm Session 5, launching for the first time. My role: ETL + contract tests
+ eventually memory service. I bridge old chat-ai data into new v2 schema
and prove v2 is a drop-in replacement (same JSON shapes per A16).

Today's plan:
1. Day 1: contract test scaffolding (pytest fixtures, golden-shape capture)
2. Day 2: contract tests for chat-ai's 21 public endpoints (capture shapes
   from live chat-ai using my JWT for auth)
3. Day 3: contract tests for chat-ai's internal endpoints (admin, ban, etc.)
4. Day 4: ETL plan draft for Day 9 (no data pulled yet — just plan)
5. Days 5-7: eval gold prompt set (~50 prompts for Langfuse)
6. Day 8: final ETL plan + dry-run against synthetic data
7. Day 9: 🚨 ETL RUN — only after explicit Rishi YES per A14

I've read CONSTRAINTS (A4 + A8 + A14 + A16 most relevant), feature-parity
audit, testing strategy, B7 doc standard. Ready to continue?
```
