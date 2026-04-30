# Getting Started Tomorrow — Step-by-Step

> When Rishi is ready to launch the 5-session build, follow this. Otherwise, skip.

## Prerequisites checklist (do these BEFORE launching sessions)

- [ ] OpenAI Codex API key created at platform.openai.com
- [ ] GitHub repo secret `OPENAI_CODEX_API_KEY` set on `dolr-ai/yral-rishi-agent`
- [ ] Sentry API key on `sentry.rishi.yral.com` placed at `~/.config/dolr-ai/sentry-api-key`
- [ ] GitHub branch protection on `main`: require PR + 1 approval + CI green
- [ ] Cloudflare credentials still working (DNS for `*.rishi.yral.com`)
- [ ] Saikat sign-off on Phase 0 cluster provisioning (Days 4-7)
- [ ] Saikat sign-off on rishi-1/2 Caddy snippet add (Day 7)
- [ ] Rishi has 30 min/day for PR-merge approvals

If any of these aren't ready, the launch can wait — better to set up cleanly than rush.

## Launch sequence

### Step 1: Coordinator session warm-up (this session, ~45 min)

Rishi opens THIS session (the coordinator) and types: "build"

Coordinator (me) does:

**A. CONSTRAINTS — already done in plan-only phase:**
- I8, I9, I10, I11, I12, I13, I14 rows already locked in CONSTRAINTS.md.

**B. Interface contracts (~10 min):**
- Write initial `interface-contracts/api-contract.md` (parity contract from chat-ai's 21 endpoints)
- Write initial `interface-contracts/internal-rpc-contracts.md`
- Write initial `interface-contracts/db-schema-ownership.md`

**C. GitHub Actions + scripts (~15 min):**
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/workflows/pr-codex-review.yml`
- `.github/workflows/lint-scope-violations.yml`
- `.github/workflows/lint-naming-and-comments.yml`
- `.github/workflows/lint-state-hygiene.yml` (NEW per I11)
- `.github/scripts/codex-review.py`
- `.github/scripts/post-codex-review.py`
- `.github/scripts/codex-prompt.txt`

**D. State persistence infrastructure (~10 min, NEW per I11/I12/I13):**
- `.claude/hooks/post-tool-use.sh` — auto-appends to SESSION-N-LOG on git commit
- `.claude/scripts/regenerate-master-status.sh` — rebuilds MASTER-STATUS every 15 min
- `~/Library/LaunchAgents/com.yral.rishi.agent.master-status.plist` — launchd job
- Stub `SESSION-1-STATE.md`, `SESSION-2-STATE.md`, `SESSION-5-STATE.md`
- Stub `SESSION-1-LOG.md`, `SESSION-2-LOG.md`, `SESSION-5-LOG.md`
- (Sessions 3 + 4 stubs created Day 9 when they launch)

**E. Subagent definitions per I15 (NEW per agent-teams adoption decision, ~15 min):**
- `.claude/agents/session-1-infra-cluster.md` (Session 1's role)
- `.claude/agents/session-2-template.md` (Session 2's role)
- `.claude/agents/session-5-etl-and-tests.md` (Session 5's role)
- (Sessions 3 + 4 definitions created before Day 9 launch)
- Each contains frontmatter (name, description, tools, model) + body with: scope, resume protocol pointer, first-week deliverables, OUT-OF-SCOPE list, branch naming convention

**F. Task-lifecycle hooks per I16 (NEW per agent-teams adoption decision, ~10 min):**
- `.claude/hooks/task-created.sh` — reject task names violating B1/B5/B6
- `.claude/hooks/task-completed.sh` — reject completion if SESSION-N-LOG wasn't appended for this task
- `.claude/hooks/teammate-idle.sh` — ping coordinator if session idle >30 min
- All exit code 2 on violation, sending feedback to the session

**E. Test the wiring (~10 min):**
- Open a tiny doc-only PR (e.g., add a glossary entry)
- Watch GitHub Actions: lints fire, Codex review fires
- Verify Codex review comment lands on PR
- Verify auto-merge per I14 fires (low-risk doc-only PR)
- Verify decision-log gets the auto-merge entry
- Verify MASTER-STATUS regenerates within 15 min

**G. Report back to Rishi:** "infrastructure ready, sessions can launch."

### Step 1.5: Tmux pane setup (per I17, ADHD-friendly multi-session view)

Once warm-up is done:
1. `brew install tmux` (one-time)
2. Open iTerm2
3. Start tmux session: `tmux -CC new -s yral-v2-build`
4. iTerm2 will open a new window with the tmux session attached
5. Layout: top pane = coordinator; bottom split into 3 panes for Sessions 1, 2, 5
   - `Ctrl+B "` to split horizontally (top + bottom)
   - `Ctrl+B %` in the bottom pane to split vertically into 3
6. To navigate: `Ctrl+B <arrow>` or click into a pane in iTerm2
7. To detach (laptop survives): `Ctrl+B d` — tmux keeps everything running
8. To reattach: `tmux -CC attach -t yral-v2-build`

If tmux setup is painful: skip it. Use 4 separate iTerm2 windows. Doesn't break the plan; only loses the at-a-glance view.

### Step 2: Launch Session 1 (Infra & Cluster) in its tmux pane

Click into the bottom-left pane in tmux. Run `claude`. Paste this short prompt:

```
You are Session 1 (Infra & Cluster) for the yral-rishi-agent v2 build.

Use the subagent definition at:
  /Users/rishichadha/Claude Projects/yral-rishi-agent/.claude/agents/session-1-infra-cluster.md

That file has your full role description, scope, resume protocol, and
first-week deliverables. Read it now and follow it.

Then run the I12 resume protocol:
1. Read multi-session-parallel-build-coordination/session-state/SESSION-1-STATE.md
2. Read last 50 lines of session-logs/SESSION-1-LOG.md
3. Filter cross-session-dependencies.md to my section
4. Scan MASTER-STATUS.md for context
5. Print the pre-written CONFIRM-TO-RISHI sentence
6. WAIT for me to type "continue"
```

Rishi reads Session 1's CONFIRM line. If it looks right, types "continue". Session 1 enters Auto-mode.

**Why this is shorter than before:** the subagent definition (created during warm-up per I15) holds the full context. The session prompt is just a pointer + the resume protocol.

### Step 3: Launch Session 2 (Template & Hello-World), in parallel

Same pattern as Session 1, with Session 2's prompt (swap the session number + scope reference).

Sessions 1 and 2 work in parallel for Days 1-3:
- Session 1: Day 0.5 Sentry script, then waits for cluster-provision approval
- Session 2: Days 1-3 template scaffolding

### Step 4: Launch Session 5 (ETL & Tests skeleton), in parallel

Session 5 starts early to write contract tests and the ETL plan even though the actual ETL run waits for Day 9.

### Step 5: Wait for Day 8 milestone

Sessions 3 and 4 (which depend on cluster + template being live) DON'T launch until:
- Session 1 completes Phase 0 cluster provisioning
- Session 2 completes template + hello-world verification
- Coordinator's Day 8 checkpoint #0C confirms Motorola hits real cluster

Why wait: Sessions 3+4 need both the cluster (to deploy to) and the template (to spawn from). Launching them earlier = idle work. **This is the locked I8 sequencing — 3 sessions Day 1, 5 sessions Day 9.**

### Step 6: Launch Sessions 3 + 4 on Day 9

Same pattern as Step 2. Coordinator creates their stub state/log files when they launch. By Day 9, all 5 sessions are running.

---

## Resume protocol (when laptop crashes / terminal closes)

Per CONSTRAINTS I12. Every session, EVERY time it's started, runs this protocol BEFORE doing any work:

```
RESUME PROTOCOL (built into session startup prompt):

1. Read /Users/rishichadha/Claude Projects/yral-rishi-agent/
   yral-rishi-agent-plan-and-discussions/
   multi-session-parallel-build-coordination/
   session-state/SESSION-N-STATE.md

2. Read last 50 lines of session-logs/SESSION-N-LOG.md

3. Filter cross-session-dependencies.md to my section

4. Quick scan of MASTER-STATUS.md for context

5. Print to terminal:
   "I'm resuming Session N. [pre-written CONFIRM-TO-RISHI sentence
   from STATE.md]. Ready to continue?"

6. WAIT for Rishi to type "continue".

After "continue", proceed in Auto-mode from where state file says.
```

**Recovery time after laptop crash: ~5 minutes for full 3-session cluster restoration.** See `06-STATE-PERSISTENCE-AND-RESUME.md` for full recovery scenarios.

## What to expect on Day 1 of multi-session

```
   Hour 0-2:   Coordinator session runs the warm-up. CI workflows
               land. Test PR confirms Codex review works.
   
   Hour 2-3:   Rishi launches Session 1. Session reads context,
               confirms scope, plans Day 0.5.
   
   Hour 3:     Rishi types "build" in Session 1. Auto-mode begins.
   
   Hour 3-6:   Session 1 writes pull-sentry-baseline.py, sets up
               launchd, opens first PR. Codex reviews. Coordinator
               summarizes for Rishi.
   
   Hour 5:     Rishi launches Session 2 (Template). Same warm-up.
   
   Hour 6:     Rishi types "build" in Session 2. Auto-mode begins.
   
   Hour 6-12:  Session 2 writes template skeleton.
   
   Hour 8:     Rishi launches Session 5 (ETL/Tests skeleton).
   
   Day 1 end:  3 sessions running, ~3-5 PRs opened, ~2 merged.
               Rishi's PR-merge cycle is the bottleneck — that's OK,
               we tune the cadence.
```

## Common first-day issues + handling

```
   ┌─────────────────────────────────────────────────────────────────┐
   │  ISSUE: Session opens a PR, Codex says "scope violation"         │
   │  CAUSE: Session edited a file outside its owned subfolder        │
   │  FIX:   Session reverts that file, re-PRs                        │
   │  PREVENT: tighter scope in startup prompt; CI lint catches it    │
   └─────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────┐
   │  ISSUE: Two sessions both want to add the same shared util      │
   │  CAUSE: Forgot to look in shared-library/ first                  │
   │  FIX:   Coordinator decides where it lives, sessions align       │
   │  PREVENT: interface-contracts/ gets a "shared utils" section     │
   └─────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────┐
   │  ISSUE: Session hits a forbidden op (e.g., needs to pull live   │
   │         chat-ai data)                                             │
   │  CAUSE: A14 — every live data pull needs Rishi YES               │
   │  FIX:   Session writes a request, coordinator surfaces to Rishi, │
   │         Rishi types YES, session resumes                         │
   │  PREVENT: planned data needs are pre-approved at phase start     │
   └─────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────┐
   │  ISSUE: Codex review takes a long time (>5 min per PR)           │
   │  CAUSE: Big diff or Codex API slow                                │
   │  FIX:   Session breaks PR into smaller pieces                    │
   │  PREVENT: PR template asks for diff-size estimate                │
   └─────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────┐
   │  ISSUE: Rishi can't keep up with PR approvals                    │
   │  CAUSE: 5 sessions × 1-2 PRs = 5-10/day, Rishi's bottleneck      │
   │  FIX:   Drop to 3 sessions until rhythm settles, OR              │
   │         Rishi delegates "low-risk" PR approval to coordinator    │
   │  PREVENT: defined "low-risk" PR class (doc-only, lint fixes)    │
   │           where coordinator can merge without Rishi YES          │
   └─────────────────────────────────────────────────────────────────┘
```

## When to pause and re-evaluate

- After Day 1: are the sessions actually running smoothly? If 2+ are stuck or repeatedly hitting forbidden ops, coordinator pauses, re-tunes, restarts.
- After Day 3: is the template solid enough to spawn services from? Session 2's hello-world is the proof.
- After Day 8: did Motorola hit the real cluster? If not, NOTHING phase-1 starts until #0C is green.
- After Day 14: phase-1 health check. Are we on track for Day 25 parity? If not, re-shard.

## Success criteria for multi-session

```
   Phase 0 (Days 1-8) success:
     • Sentry baseline script running daily
     • Template proven via hello-world
     • Cluster provisioned, chaos tests green
     • rishi-1/2 Caddy snippet live
     • Motorola debug APK hits agent.rishi.yral.com → 200 OK
     • All 5 sessions producing PRs at expected cadence
     • Codex review catching real issues, not just nits
     • Rishi feels in control of merges (not drowning in approvals)
   
   Phase 1 (Days 9-25) success:
     • Every chat-ai endpoint has a v2 contract test, GREEN
     • Motorola debug APK does end-to-end chat against v2
     • Latency baseline shows v2 ≤ chat-ai (50%-faster target may not
       hit until streaming arrives in Phase 3)
     • All Sentry errors triaged
     • All 5 sessions still healthy and producing
```

## Tomorrow morning's first message to coordinator

If tomorrow you're ready, your first message to me (this coordinator session) is just:

> "build"

I take it from there: add the I8/I9/I10 rows, write interface contracts, set up CI workflows, run the test PR. ~30 min of warm-up, then you launch Session 1.

If you want to slow it down or change the plan, tell me what to adjust before "build".
