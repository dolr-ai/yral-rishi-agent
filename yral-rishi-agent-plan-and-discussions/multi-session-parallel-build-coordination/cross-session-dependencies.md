# Cross-Session Dependencies (kanban)
> Sessions raise OPEN deps; coordinator moves to RESOLVED when fixed. RESOLVED stays forever (audit trail).

## OPEN

### DEP-001 — Session 1 needs scope-lint paths corrected to match real folder layout
Raised: 2026-05-04 by Session 1
What:    Three CI lint paths in `.github/workflows/lint-scope-violations.yml`
         and `.github/workflows/lint-state-hygiene.yml` do not match the real
         folder paths Session 1 must write to per `.claude/agents/session-1-infra-cluster.md`:

         (a) Latency-baseline scripts folder:
             - Spec / lint expects: `latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/`
             - Actual folder lives at: `yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/`
             - Coordinator-decided fix: either prepend the prefix in `SESSION_PATHS[1]`
               (one-line workflow edit) OR move the folder up to monorepo root
               (needs Rishi YES per A1 — moving an existing artifact).

         (b) Session 1's own log + state file paths:
             - Agent spec line 34 says Session 1 may write to "Your own session log + state file"
             - Real paths: `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md`
                          `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-1-STATE.md`
             - Neither path is in `SESSION_PATHS[1]` — so any PR that updates them
               will fail scope-lint.
             - Same applies to `cross-session-dependencies.md` (this very file)
               which sessions are expected to write OPEN entries to per I11.

         (c) `YRAL_SESSION_ID` env var is not set in this Claude Code session,
             so `.claude/hooks/post-tool-use.sh` (which reads it via
             `${YRAL_SESSION_ID:-coordinator}`) will write commit-trigger
             diary entries to `SESSION-coordinator-LOG.md` instead of
             `SESSION-1-LOG.md`. Until the session is restarted with the
             env var set, Session 1 is writing manual milestone entries
             directly to its own log.

Why:     PR #<TBD> (Day 0.5 Sentry baseline pull) currently includes:
         - Code in (a) — will fail `lint-scope-violations`
         - Diary entry in (b) under SESSION-1-LOG — will fail `lint-scope-violations`
         - Required state-hygiene update in (b) — will fail BOTH lints if
           SESSION-1-LOG is required by `lint-state-hygiene.yml` AND blocked
           by `lint-scope-violations.yml`. Catch-22 until coordinator fixes.

Blocks:  Session 1 PR for sentry-baseline-cron Day 0.5 deliverable. Also
         blocks every future Session 1 PR until paths are reconciled.

ETA needed: Before PR #<TBD> can be merged. Suggested fix is a 5-line
         edit to `.github/workflows/lint-scope-violations.yml` —
         coordinator can do this in their own branch.

Suggested
resolution: Update `SESSION_PATHS[1]` in lint-scope-violations.yml to:
         ```
         SESSION_PATHS[1]="bootstrap-scripts-for-the-v2-docker-swarm-cluster/|yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/|yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md|yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-1-STATE.md|yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md"
         ```
         Apply analogous fix for SESSION_PATHS[2..5]. Separately, set
         `YRAL_SESSION_ID=N` in each session's launch environment so
         the post-tool-use hook routes diary entries correctly.

---

## RESOLVED

<none yet>

---

## How to use

### Raising a dependency (session-author writes this)
```markdown
### DEP-<3-digit-number> — <short title>
Raised: YYYY-MM-DD by Session N
What:    <specific thing needed, with technical detail>
Why:     <how it unblocks or improves my work>
Blocks:  <which PRs/tasks of mine are blocked, or "no hard block">
ETA needed: <date>
```

### Resolving a dependency (coordinator writes this when fixed)
Move the entry to RESOLVED section, append:
```markdown
Resolved: YYYY-MM-DD by <who> (PR/decision link)
Resolution: <1-line: how it was answered>
```
