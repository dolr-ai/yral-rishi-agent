# Coordinator Session Playbook

> The coordinator session is what you (Rishi) and Claude (this session) operate together. It is NOT one of the 5 building sessions — it owns plans, constraints, integration, conflict resolution, and Rishi-approval routing.

## Daily rhythm

```
  MORNING  (~10 min)
  ──────────────────
  1. Read each SESSION-N-LOG.md from yesterday
  2. Identify blockers across sessions
  3. Identify cross-session conflicts (two sessions need same change)
  4. Update interface-contracts/ if any new boundary emerged

  THROUGHOUT THE DAY  (~5 min per PR)
  ───────────────────────────────────
  5. PR notification arrives (5 sessions × 1-2 PRs/day = ~10 PRs/day)
  6. Read Claude's PR description + diff
  7. Read Codex review comments
  8. Write 1-paragraph summary for Rishi as PR comment
  9. Tag Rishi for YES/NO

  EVENING  (~10 min)
  ──────────────────
  10. Update CONSTRAINTS.md if new constraints emerged
  11. Update TIMELINE.md if sequence changed
  12. Write coordinator log entry: today's decisions + tomorrow's focus
  13. Update memory files in ~/.claude/projects/-.../memory/ if needed
```

## What the coordinator owns (write-allowed scope)

```
yral-rishi-agent-plan-and-discussions/
├── CONSTRAINTS.md
├── README.md
├── TIMELINE.md
├── V2_INFRASTRUCTURE_AND_CLUSTER_ARCHITECTURE_CURRENT.md
└── multi-session-parallel-build-coordination/
    ├── 00-MASTER-PLAN.md
    ├── 01-SESSION-SHARDING-AND-OWNERSHIP.md
    ├── 02-AUTO-MODE-GUARDRAILS.md
    ├── 03-CODEX-REVIEW-WORKFLOW.md
    ├── 04-COORDINATOR-SESSION-PLAYBOOK.md  (this file)
    ├── 05-GETTING-STARTED-TOMORROW.md
    ├── interface-contracts/
    │   ├── api-contract.md           ← what public-api exposes
    │   ├── internal-rpc-contracts.md ← service ↔ service
    │   └── db-schema-ownership.md    ← who owns which Postgres schema
    └── session-logs/                 ← coordinator reads, doesn't write
        ├── SESSION-1-LOG.md
        ├── SESSION-2-LOG.md
        └── ... (each session writes its own)

shared-library-code-used-by-every-v2-service/   ← coordinator curates

.github/
├── workflows/
│   ├── pr-codex-review.yml
│   ├── lint-naming-and-comments.yml
│   ├── lint-no-hardcoded-ips.yml
│   ├── lint-scope-violations.yml
│   └── deploy.yml.template
├── scripts/
│   ├── codex-review.py
│   ├── post-codex-review.py
│   └── codex-prompt.txt
└── PULL_REQUEST_TEMPLATE.md

~/.claude/projects/-Users-rishichadha/memory/   ← coordinator-only writes
```

## What the coordinator does NOT do

- Write service code (Sessions 1-5)
- Edit other sessions' SESSION-N-LOG.md
- Run docker compose for service stacks (Sessions own their stacks)
- SSH to rishi-4/5/6 (Session 1 owns infra ops)
- Open PRs for service code (Sessions do that)

The coordinator is a **planner + integrator + Rishi-interface**. Not a builder.

## Conflict resolution patterns

### Pattern 1: Two sessions need the same change

Example: Session 3 (public-api) needs a helper function that Session 4 (orchestrator) also needs. Where does it live?

**Coordinator decision tree:**
```
   Is the helper specific to ONE service?
     YES → it lives in that service's code, the other session calls
            it via HTTP (not by importing)
     NO  → it lives in shared-library-code-used-by-every-v2-service/
            Coordinator writes it; sessions consume via package import
```

### Pattern 2: Interface contract change mid-build

Example: Session 3 designed a `/v2/conversations/{id}/messages` endpoint shape; Session 4 (orchestrator) needs different fields.

**Coordinator decision:**
1. Look at chat-ai's existing shape (parity is the floor per A8 + A16)
2. Pick the shape that's a strict superset
3. Update `interface-contracts/api-contract.md`
4. Tell both sessions to align with the contract
5. If a session's existing code conflicts, coordinator opens a "contract reconciliation" PR

### Pattern 3: Codex disagrees with Claude on substance

Coordinator reads both views, presents to Rishi as:
> "Codex says X because A. Claude says Y because B. Both are valid. My recommendation: [X or Y] because [tiebreaker reason]. Your call."

### Pattern 4: A session is moving slower than expected

Coordinator assesses:
- Is the session blocked by a coordinator-owned dependency? → coordinator unblocks
- Is the session blocked by another session? → coordinate handoff
- Is the session in over its head? → re-shard scope, possibly split into 2 sessions
- Is Auto-mode hitting too many forbidden ops? → tune the guardrail (with Rishi)

## Coordinator → Rishi communication patterns

### When to surface to Rishi (interrupt)

- A blocker that can't wait until end of day
- A forbidden-op request from a session
- A PR ready for YES/NO decision
- A constraint conflict (two rules contradict, need Rishi to pick)
- A scope re-shard proposal

### When NOT to surface (handle in coordinator)

- Routine PR review summaries (batch into morning + evening reads)
- Documentation cleanup
- Cross-session scheduling
- CI flakiness debugging
- Minor session-log questions

### Format for Rishi-facing summaries

Three lines max for routine asks. Long-form only when Rishi asks for it.

```
   ROUTINE PR SUMMARY (good):
   ─────────────────────────
   "Session 3 PR #42: JWT validation, Codex 1 nit (line 47 missing
    role-comment), latency p95 12ms (target met). Recommend YES merge."

   ROUTINE PR SUMMARY (bad — too long):
   ────────────────────────────────────
   "Session 3 has done some really thoughtful work on JWT validation.
    They've implemented JWKS caching with 1h TTL via Redis, which Codex
    initially questioned but I think the rationale Claude gave is solid
    because rotation matters and..." [10 more sentences]
   ↑ Save this for when Rishi asks "tell me more"
```

## Coordinator's recurring obligations

### Weekly (every 7 days)

- Update CONSTRAINTS.md "Last reviewed" timestamp
- Audit interface-contracts/ against actual code (drift check)
- Read all 5 SESSION-N-LOG.md cumulatively, summarize for Rishi
- Run a health check: are sessions hitting forbidden ops more than rare?
- Run a velocity check: which sessions are ahead/behind?

### Bi-weekly (every 14 days)

- Re-evaluate the shard plan: still right? need re-shard?
- Re-evaluate the Codex review prompt: catching the right things?
- Re-evaluate Auto-mode guardrails: too tight? too loose?

### When a phase ends

- Write a phase-close memo: what shipped, what didn't, why
- Plan next phase shard (might be different shape than current 5)
- Rishi-test on Motorola, document feedback
- Update memory files with phase learnings

## Emergency protocols

### Kill-switch invocation

If something is going wrong:
1. Coordinator types in any session: "STOP AUTO-MODE"
2. That session immediately drops to per-command approval
3. Coordinator inspects the SESSION-N-LOG, recent commits, PR comments
4. Coordinator surfaces to Rishi
5. Rishi decides: resume auto, keep semi-manual, kill the session entirely

### Universal halt

If multiple sessions are misbehaving:
1. Rishi types "STOP ALL SESSIONS" in coordinator
2. Coordinator pings each session with "halt now"
3. All sessions complete current command, then drop to per-command
4. We diagnose, then resume one by one as fixes land

### Rollback

If a merged PR causes a regression:
1. Coordinator runs: `gh pr revert <pr-number>` to create revert PR
2. That PR goes through normal Codex review + Rishi YES
3. Merged → auto-deploy reverts → fix forward in next session-N PR
4. Never hand-edit history; never force-push

## What the coordinator reads when context is unclear

In order of preference:
1. The CONSTRAINTS.md row(s) cited
2. The relevant memory file in `~/.claude/projects/-.../memory/`
3. The session's SCOPE file (01-SESSION-SHARDING-AND-OWNERSHIP.md)
4. The interface contract for the boundary in question
5. The actual code in the diff
6. Ask Rishi (last resort, when memory + docs don't cover it)

## When the coordinator is not enough

Sometimes a problem needs more than coordinator cycles:
- Real-time pair-programming with a session (rare)
- Re-architecture of a service mid-build (very rare)
- Outage debugging on rishi-4/5/6 (Session 1's domain, coordinator helps)

In those cases, coordinator joins as a participant, not just an integrator. Rishi explicitly invokes this with: "coordinator, jump into Session N for X."
