# Mobile Client Change Log — yral-mobile changes for v2 agent platform

**Purpose:** authoritative log of every change made to `~/Claude Projects/yral-mobile/` in support of v2. Used (1) as Rishi's review doc, (2) as the final package to walk Sarvesh through on a call, (3) as the basis for PRs into YRAL Alpha.

**Workflow rule** (per `feedback_mobile_change_workflow_one_at_a_time_local_first.md` / CONSTRAINTS row A12):
- One change at a time
- Never pushed to origin until Rishi signs off AND Sarvesh is briefed
- Each change documented in the row table below BEFORE moving to the next change
- Tested on Rishi's Motorola (real device) before marking "working"

---

## Summary

| Total changes attempted | 0 |
| Changes working on device | 0 |
| Changes regressed / backed out | 0 |
| Changes currently in progress | 0 |
| Status | Awaiting first-change selection |

---

## Change Log (newest at top)

_No changes yet._

### Template for each change entry (copy + fill)

```
### Change #N — <short descriptive title>

**Date:** YYYY-MM-DD
**Status:** in-progress / working / regressed / reverted / shipped
**Depends on:** Change #M (if any)
**Estimated diff size:** ~N lines across M files

**1. What the change is**
<files touched, summary of edits>

**2. Why we want it**
<which v2 feature this enables; link to plan/capability>

**3. How v2 consumes it**
<server-side pattern that relies on this mobile change; example flow>

**4. What it might affect or break**
<regression analysis: existing code paths touched, feature flags that gate it, fallback behavior if v2 server is down>

**5. Test evidence on Rishi's Motorola**
- Build: <debug APK built successfully? Yes/No, commit SHA, gradle output summary>
- Install: <adb install succeeded? Yes/No>
- Exercised: <exactly what Rishi did: opened chat, sent message, observed X>
- Regression check (flag OFF / existing behavior): <tested? existing behavior unchanged? Y/N>
- Regression check (flag ON / new behavior): <tested? new behavior works? Y/N>
- Observed latency (if relevant): <numbers>
- Crashes / errors: <any? screenshots if yes>
- Subjective UX (Rishi's words): <"feels natural" / "feels laggy" / etc.>

**6. Sign-off**
- Rishi reviewed code diff: Y/N
- Rishi tested on Motorola: Y/N
- Rishi approved to move to next change: Y/N

**7. Future Sarvesh conversation talking points**
- Elevator pitch: <one sentence for Sarvesh>
- Risks to flag: <anything Sarvesh would want to know before merging>
- Bundle-ability: <can this ship alone, or does it need changes #X/#Y alongside?>

**8. Files diff summary** (for eventual Sarvesh PR)
```diff
# File 1: path/to/file.kt
- old line
+ new line
# File 2: ...
```

```

---

## Guardrails for me (the agent) while executing this workflow

1. Before editing ANY file in `~/Claude Projects/yral-mobile/`, check this log for the currently-in-progress change. Only work on that one.
2. Before starting a new change, verify the previous change's status is "working" in this log.
3. If I need to run `git` in yral-mobile: only local operations (`git status`, `git diff`, `git add`, `git commit` to a local branch). NEVER `git push`, `git push --force`, `gh pr create`, or anything that touches origin.
4. If a change breaks something, REVERT it cleanly (git restore / reset to HEAD) and note the revert in the log.
5. If I'm uncertain whether a change fits the workflow, STOP and ask Rishi.

---

## Sarvesh handoff template (used ONLY when all changes are validated)

When Rishi gives the "ready for Sarvesh" signal, I produce a clean version of this log formatted as a briefing doc:

```
# yral-mobile v2 Preparation — Proposed Changes for Review

## Context (for Sarvesh)
<plain-English explanation of what v2 is and why these mobile changes are needed>

## Change 1: <title>
- What: <summary>
- Why: <feature enabled>
- Risk: <regression analysis + mitigation>
- Tested: <how Rishi validated on device>
- PR: will be sent after your call-walkthrough
- Depends on: none / change N

## Change 2: <title>
[same template]

...

## Proposed merge order (alpha → main)
1. ...
2. ...

## Rollback plan per change
- ...
```

Rishi walks Sarvesh through this on a call; PRs go one-at-a-time; alpha first; production after alpha validation.
