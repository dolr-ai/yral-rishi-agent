# State Persistence + Resume Strategy

> The single most important non-code thing we build before launch. Designed for non-programmer + ADHD with multi-session work that must survive laptop crashes.

## Why this exists even though Claude Code has built-in agent teams

Anthropic shipped an experimental "agent teams" feature in v2.1.32+ (`code.claude.com/docs/en/agent-teams`). It includes shared task lists, mailbox messaging, and lifecycle hooks. We adopt some of its STABLE primitives (subagent definitions per I15, hooks per I16, tmux display per I17, 3-5 teammate sweet spot) but **NOT the experimental wrapper itself** because:

> "**No session resumption with in-process teammates**: `/resume` and `/rewind` do not restore in-process teammates. After resuming a session, the lead may attempt to message teammates that no longer exist."
> — Anthropic's agent-teams docs, "Limitations" section

For our 6-week build that must survive laptop crashes, day-offs, and accidental terminal closures, this gap is fatal. Our 5-layer state architecture is **explicitly designed to fill this gap**. When Anthropic resolves resume + moves agent teams out of experimental, we revisit (per I18). Until then: this design holds.

## Why this matters

You will:
- Close terminals (intentionally or not)
- Restart your laptop
- Step away for hours/days
- Need to know "where are we?" in <30 seconds
- Need to recover when something goes wrong

Without a state strategy, sessions become amnesiac after any interruption. With it, you pick up exactly where you left off.

## The 5-layer state architecture

```
   LAYER 5 — DECISION LOG          decision-log.md
              (forever, append-only, why-we-chose-X)

   LAYER 4 — DAILY REPORTS         daily-reports/YYYY-MM-DD.md
              (rolling, end-of-day per-session summary)

   LAYER 3 — MASTER STATUS         MASTER-STATUS.md
              (live, top of repo, your one-file morning view)

   LAYER 2a — DEPENDENCIES          cross-session-dependencies.md
              (open asks between sessions, kanban-style)

   LAYER 2b — STATE                session-state/SESSION-N-STATE.md
              (per-session "where am I now" snapshot)

   LAYER 1 — LOG                   session-logs/SESSION-N-LOG.md
              (per-session append-only diary, raw record)
```

## What each layer is for

### Layer 1 — SESSION-N-LOG.md (the diary)

Append-only record of every meaningful action a session takes. Updated by:
- **Auto-append on every git commit** via Claude Code PostToolUse hook (most entries come from here)
- **Manual milestone entries** when a session crosses a significant boundary (e.g., "Phase 0 cluster bootstrap COMPLETE")

Format per entry:
```markdown
## 2026-05-02 14:32 IST — abc123ef
### Action
Configured Patroni replica role on rishi-5 in bootstrap-scripts-.../patroni/

### Files touched
- bootstrap-scripts-.../patroni/replica-config.yaml (new)
- bootstrap-scripts-.../patroni/test-replica.sh (new)

### Why
Day 5 of Phase 0 — replica is one of two needed for sync-commit quorum.

### Test evidence
- Smoke test passed: replica caught up within 4s of leader writes
- etcd discovery: rishi-5 visible to leader
```

Never edit past entries. If something was wrong, write a new entry that corrects it. The diary is history.

### Layer 2a — cross-session-dependencies.md (the kanban)

When Session A needs something from Session B, it's an OPEN dependency. Format:

```markdown
## OPEN

### DEP-001 — Session 3 needs Soul File contract from Session 4
Raised: 2026-05-09 by Session 3
What:    The exact JSON shape of `GET /soul-file/{influencer_id}` response
Why:     Session 3 building public-api → orchestrator forward; needs
         to know what to expect back so it can shape its API layer.
Blocks:  Session 3 PR #58 (public-api routing for chat turns)
ETA needed: 2026-05-10 EOD

## RESOLVED

### DEP-000 — (example) Session 1 needed Caddy config pattern from Rishi
Raised: 2026-05-07
Resolved: 2026-05-07 (Rishi confirmed via Coordinator: use snippet
   pattern from yral-rishi-hetzner-infra-template's chat-ai.caddy)
```

Sessions WRITE to OPEN section in their own scope (one entry per dep). Coordinator MOVES entries to RESOLVED when fixed. Closed entries stay forever (audit trail).

### Layer 2b — SESSION-N-STATE.md (the resume file)

The "if I open this session right now, where am I?" snapshot. Updated:
- **Automatically** on every git commit (hook regenerates)
- **Manually** when a session shifts to a new task

Always answers:
- WHO am I? (Session N — title)
- WHAT did I just do? (last commit + 1-line description)
- WHAT am I working on now? (current task + % progress)
- WHAT'S next? (next 3 actions)
- WHAT'S blocking me? (none, or specific blockers)
- WHICH PRs of mine are open? (numbers + status)
- WHAT do I owe other sessions? (cross-deps where I'm assignee)

Plus a **"CONFIRM TO RISHI"** block — pre-written sentence the session prints when resumed:
```
"I'm resuming Session 1. Last work was Patroni leader (PR #43,
awaiting your YES). Currently mid-replica-config on rishi-5,
~40% done. No new blockers. Ready to continue?"
```

### Layer 3 — MASTER-STATUS.md (your morning view)

Auto-generated by coordinator every 15 min while sessions are active. The ONE file you open to know everything.

Sections (in order of importance for ADHD reading):
1. **❓ AWAITING RISHI** — anything that needs your attention TODAY
2. **📊 SESSION HEALTH** — 4-line summary per session with emoji status
3. **🤖 AUTO-MERGED IN LAST 24H** — what coordinator merged without you
4. **🔗 OPEN CROSS-SESSION DEPENDENCIES** — non-blocked summary
5. **📈 LATENCY BASELINE** — yesterday's number to beat (E1)
6. **💰 SPEND THIS WEEK** — Codex API + cloud costs
7. **⏰ TODAY'S CRITICAL PATH** — what must happen today

If you only read one section: read **❓ AWAITING RISHI**. Everything blocking is there.

### Layer 4 — daily-reports/YYYY-MM-DD.md (end-of-day)

Coordinator writes one per day at ~6pm IST. Captures:
- What each session shipped today (PRs merged, milestones hit)
- What didn't ship and why
- Tomorrow's per-session focus
- Any constraints touched or considerations raised
- Latency baseline trend (chat-ai vs target)
- Any forbidden-op requests during the day

Used for:
- Weekly retrospectives
- Phase-end summaries
- Onboarding (if Yoa joins, this is where they catch up)

### Layer 5 — decision-log.md (forever)

Append-only. Every cross-cutting decision gets one entry. Format:

```markdown
## 2026-05-04 — Use cosine similarity (not L2 distance) in pgvector

### Decision
Use cosine similarity for semantic memory search.

### Why
Codex flagged on PR #67: cosine is more standard for sentence embeddings;
L2 distance is for spatial data. Semantic facts are sentence-level.

### Alternative considered
L2 distance (Claude's original choice). Both work; cosine is the more
documented/standard pattern for this use case.

### Decided by
Coordinator + Rishi (Rishi typed YES on coordinator's recommendation)

### Affects
- yral-rishi-agent-user-memory-service code
- All future memory retrieval queries

### Reversibility
Yes — can re-index existing embeddings to L2 if needed. ~1hr work.
```

Never edited. Mistakes become new entries. The historical record.

---

## Implementation: how state gets WRITTEN reliably

```
   ┌───────────────────────────────────────────────────────────────┐
   │  TRIGGER 1 — Claude Code PostToolUse hook                      │
   │  ────────────────────────────────────                          │
   │  After every Edit / Write / Bash tool use, a hook runs:        │
   │                                                                  │
   │    .claude/hooks/post-tool-use.sh                              │
   │                                                                  │
   │  This hook:                                                      │
   │    • Detects which session is running (from environment var    │
   │      YRAL_SESSION_ID set in session startup prompt)             │
   │    • If a git commit happened: appends entry to                 │
   │      session-logs/SESSION-N-LOG.md with commit SHA + files     │
   │    • Regenerates session-state/SESSION-N-STATE.md from log     │
   │                                                                  │
   │  No session-author intervention needed. Always runs.            │
   └───────────────────────────────────────────────────────────────┘

   ┌───────────────────────────────────────────────────────────────┐
   │  TRIGGER 2 — Manual milestone entries                          │
   │  ───────────────────────────────────                           │
   │  When the session author crosses a meaningful boundary, they   │
   │  explicitly write to the log:                                   │
   │                                                                  │
   │    "Add a SESSION-N-LOG entry: Phase 0 cluster bootstrap done" │
   │                                                                  │
   │  Hooks don't catch boundary semantics — only commits. Author   │
   │  marks the meaningful ones.                                     │
   └───────────────────────────────────────────────────────────────┘

   ┌───────────────────────────────────────────────────────────────┐
   │  TRIGGER 3 — Coordinator runs every 15 min                     │
   │  ────────────────────────────────────                          │
   │  Coordinator session has a tiny periodic task:                  │
   │  read all 5 SESSION-N-STATE.md, regenerate MASTER-STATUS.md.    │
   │                                                                  │
   │  Tactically: a launchd job on Rishi's laptop runs:              │
   │    .claude/scripts/regenerate-master-status.sh                 │
   │  every 15 min, which reads state files + git history and       │
   │  rebuilds MASTER-STATUS.md. No Claude session involvement       │
   │  needed for this regeneration — just a shell script.            │
   └───────────────────────────────────────────────────────────────┘

   ┌───────────────────────────────────────────────────────────────┐
   │  TRIGGER 4 — End-of-day daily report                           │
   │  ──────────────────────────────                                │
   │  Coordinator session at ~6pm IST runs:                          │
   │    "generate today's daily-reports entry"                       │
   │  Reads all logs from past 24h, writes daily-reports/<date>.md  │
   │                                                                  │
   │  Could be automated via launchd, but better to keep it manual   │
   │  so coordinator can add interpretive notes (Codex didn't catch  │
   │  X; trend Y emerging; etc.)                                     │
   └───────────────────────────────────────────────────────────────┘

   ┌───────────────────────────────────────────────────────────────┐
   │  TRIGGER 5 — CI lint enforces hygiene                          │
   │  ──────────────────────────────────                            │
   │  GitHub Action `lint-state-hygiene.yml` runs on every PR:      │
   │    • Was SESSION-N-LOG.md updated for this PR's session?      │
   │    • If not, fail the CI                                        │
   │    • Also: was an entry added to decision-log if PR has design │
   │      implications (Codex tags this)                             │
   └───────────────────────────────────────────────────────────────┘
```

---

## Implementation: how state gets READ reliably

### When a session is RESUMED (terminal closed, laptop restart, etc.)

The session's startup prompt includes this resume protocol:

```
RESUME PROTOCOL — run this on every session start:

1. Read these files in order:
   • /Users/rishichadha/Claude Projects/yral-rishi-agent/
     yral-rishi-agent-plan-and-discussions/
     multi-session-parallel-build-coordination/
     session-state/SESSION-N-STATE.md
   • /Users/rishichadha/Claude Projects/yral-rishi-agent/
     yral-rishi-agent-plan-and-discussions/
     multi-session-parallel-build-coordination/
     session-logs/SESSION-N-LOG.md (last 50 lines only)
   • /Users/rishichadha/Claude Projects/yral-rishi-agent/
     yral-rishi-agent-plan-and-discussions/
     multi-session-parallel-build-coordination/
     cross-session-dependencies.md (filter to my section)

2. Read MASTER-STATUS.md — quick scan to know what other sessions
   have been doing while I was away.

3. Print to terminal:
   "I'm resuming Session N. [pre-written CONFIRM TO RISHI block from
   STATE.md]. Ready to continue?"

4. WAIT for Rishi to type "continue".

5. After "continue", proceed in Auto-mode from where state file says.
```

This is enforced via the session prompt template. Sessions that skip resume protocol get pinged by coordinator on next interaction.

### When Rishi opens to "what's going on?"

Rishi opens MASTER-STATUS.md. 30 seconds later, oriented.

If he wants more depth on something specific, he reads:
- `daily-reports/<yesterday>.md` for context
- `decision-log.md` to remember why we chose something
- `cross-session-dependencies.md` if a blocker is mentioned
- Specific session's log/state if he wants to dig

ADHD-friendly: he has a hierarchy. Top of pyramid (MASTER-STATUS) for fast orientation. Tiers below for depth when needed. Never has to start from raw logs.

---

## ADHD-friendly design principles built in

1. **ONE source of truth for "where are we?"** = `MASTER-STATUS.md`. No hunting.

2. **Proactive surfacing** = sessions raise blockers in `cross-session-dependencies.md` AND coordinator promotes them to `❓ AWAITING RISHI` in MASTER-STATUS. You don't have to know to ask.

3. **No buried info** = anything needing Rishi action is at the TOP of MASTER-STATUS, not deep in a session log.

4. **Resume friction = zero** = sessions print pre-written CONFIRM-TO-RISHI sentences. You read 1 line, type "continue".

5. **Progress visibility** = SESSION-N-STATE.md tracks % progress + ETA on current task. You can see momentum.

6. **Consistent format** = every layer follows a template. No surprises in structure.

7. **Undoable** = decision-log + git history mean any decision can be looked up + reversed. No silent changes.

8. **Visual cues everywhere** = emoji status (🟢 🟡 🔴 ⚪), section dividers (═══), code-block borders. Reads like a dashboard, not prose.

---

## Recovery scenarios — pre-written

### Scenario A: Laptop crashes overnight
1. Boot laptop, open 3 terminals (or however many sessions running)
2. In each: `claude` → paste session startup prompt with "resume" flag
3. Session reads STATE+LOG, prints CONFIRM, waits
4. You type "continue" in each
5. Coordinator: `claude` → ask for fresh MASTER-STATUS regeneration
6. Open MASTER-STATUS.md, catch up in 30s
7. Approve any pending PRs
8. Day continues

Recovery time: ~5 minutes for full cluster.

### Scenario B: Bad merge to main
1. Notice on Motorola: API returning errors
2. Open coordinator session, ask: "what merged in last 24h?"
3. Coordinator reads decision-log + git, identifies suspect PR
4. Run: `gh pr revert <number>` (in coordinator session)
5. Revert PR auto-merges per I14 (revert = low-risk by definition)
6. Auto-deploys, errors clear
7. Coordinator pings offending session for post-mortem entry
8. Decision-log gets new entry: what went wrong, what we fixed

Recovery time: 15-30 minutes.

### Scenario C: Session goes silent (Auto-mode hung)
1. Coordinator notices no commits from Session N for >2 hours during active hours
2. Pings via coordinator: "Session N status?"
3. Coordinator reads SESSION-N-STATE.md last update timestamp
4. If state is genuinely stale: type "STOP AUTO-MODE" in Session N
5. Drops to per-command approval; investigate what happened
6. Resume after fix

### Scenario D: Forbidden op attempted
1. Session hits a forbidden op, STOPs per I9
2. Writes to its log: "Hit forbidden op X at T"
3. Writes a request entry to cross-session-dependencies.md
4. Coordinator promotes to AWAITING RISHI in next MASTER-STATUS
5. You see it on next morning scan, decide
6. Type YES or NO via coordinator
7. Session resumes (with whatever was approved)

### Scenario E: State files corrupted (rare)
1. Coordinator runs: read all logs vs `git log` for each session branch
2. Reconstruct state from git history (the ultimate truth)
3. Re-write SESSION-N-STATE.md from reconstruction
4. Document the recovery in decision-log
5. Add the corruption pattern to "things to defend against" list

---

## File structure (locked)

```
yral-rishi-agent-plan-and-discussions/
└── multi-session-parallel-build-coordination/
    ├── 00-MASTER-PLAN.md
    ├── 01-SESSION-SHARDING-AND-OWNERSHIP.md
    ├── 02-AUTO-MODE-GUARDRAILS.md
    ├── 03-CODEX-REVIEW-WORKFLOW.md
    ├── 04-COORDINATOR-SESSION-PLAYBOOK.md
    ├── 05-GETTING-STARTED-TOMORROW.md
    ├── 06-STATE-PERSISTENCE-AND-RESUME.md   ← THIS FILE
    ├── MASTER-STATUS.md                      ← Layer 3, top of folder
    ├── decision-log.md                       ← Layer 5, append-only
    ├── cross-session-dependencies.md         ← Layer 2a
    ├── session-state/
    │   ├── SESSION-1-STATE.md                ← Layer 2b
    │   ├── SESSION-2-STATE.md
    │   └── ... (one per session)
    ├── session-logs/
    │   ├── SESSION-1-LOG.md                  ← Layer 1
    │   ├── SESSION-2-LOG.md
    │   └── ... (one per session)
    ├── daily-reports/
    │   └── YYYY-MM-DD.md                     ← Layer 4
    └── templates/
        ├── SESSION-N-STATE-TEMPLATE.md       (skeleton for new sessions)
        ├── SESSION-N-LOG-TEMPLATE.md
        └── DAILY-REPORT-TEMPLATE.md
```

---

## What I'll create at "build" warm-up

In addition to the items in 05-GETTING-STARTED-TOMORROW.md, the warm-up adds:

- Initial empty `MASTER-STATUS.md` with "PRE-LAUNCH" placeholder
- Initial empty `decision-log.md`
- Initial empty `cross-session-dependencies.md`
- Stub `SESSION-N-STATE.md` and `SESSION-N-LOG.md` for each session that's launching
- Templates folder
- `.claude/hooks/post-tool-use.sh` script that auto-appends to logs on commit
- `.claude/scripts/regenerate-master-status.sh`
- launchd plist for the 15-min MASTER-STATUS regeneration
- GitHub Action `.github/workflows/lint-state-hygiene.yml` (CI: was log updated?)

---

## What this WILL NOT solve

- Sessions writing to wrong log file (mitigated: hook detects session ID from env)
- State files getting too big (mitigated: rotate weekly, archive)
- Coordinator's MASTER-STATUS being stale if regen-script fails silently (mitigated: header timestamp shows last update; >30 min stale = alarm)
- Two sessions writing to cross-session-dependencies.md at same instant (rare; git resolves; coordinator audits)

These are tail-risk failures we accept, with monitoring.
