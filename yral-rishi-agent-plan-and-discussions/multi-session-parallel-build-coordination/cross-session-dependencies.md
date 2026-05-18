# Cross-Session Dependencies (kanban)
> Sessions raise OPEN deps; coordinator moves to RESOLVED when fixed. RESOLVED stays forever (audit trail).

## OPEN

### DEP-005 — Session 2 needs to mirror `/health/{live,ready,deep}` in the template (per F9)

Raised: 2026-05-18 by Session 3

What:    Session 3's Day-2 PR ships `app/api/health_routes.py` locally
         in `yral-rishi-agent-public-api/` because the template
         (`yral-rishi-agent-new-service-template/app/`) does not yet
         include health endpoints. F9 requires the three-tier split
         (`/health/live` cheap, `/health/ready` deps-aware,
         `/health/deep` real round-trip) on EVERY service. Codex
         flagged the gap on Session 3 Day-1 PR #94; coordinator
         confirmed it's template-inherited, not Session 3-introduced.

         The local file Session 3 just shipped is intentionally a
         bridge — same FastAPI APIRouter + handler signatures the
         template should adopt. Copy-paste should work:

         `yral-rishi-agent-public-api/app/api/health_routes.py` →
         `yral-rishi-agent-new-service-template/app/health_routes.py`
         (drop the `/api/` subfolder since the template lacks one;
         re-add the subfolder when the template's `app/api/`
         submodule is built — likely never, since the template stays
         minimal per A2.1).

         OR — keep my file in the spawned copy as the canonical
         version and have the template ship `health_routes.py` at the
         top of `app/` with identical shape. Either way, all 13 v2
         services need these endpoints by the time Day-5 cluster
         deploy lands (per I2 + the Swarm rolling-update health gate
         + the rishi-1/2 Caddy `health_uri /health/ready` per C10).

Why:     Without health endpoints on every v2 service:
         - Swarm rolling-update treats every replica as "unhealthy"
           and auto-rolls-back on first deploy (per I2).
         - rishi-1/2 Caddy's `reverse_proxy ... { health_uri /health/ready }`
           (per C10) marks the upstream dead → 502s the request.
         - Uptime Kuma (per D5) shows the service down forever.

         Bridge in place for `yral-rishi-agent-public-api` so my
         Day-5 deploy isn't blocked, but Session 4's three services
         (orchestrator + soul-file-library + influencer-and-profile-
         directory) + Session 5's user-memory-service + the other 8
         deferred services need the template fix.

Blocks:  NOT a hard block on Session 3 (the local bridge works). DOES
         block all OTHER sessions' first cluster deploys until they
         either re-spawn from a fixed template OR back-fill the local
         bridge in each service folder.

ETA needed: Before Day 5 cluster deploy for any service (so Session 4's
         services don't all hit auto-rollback on first deploy attempt).
         Generous estimate: 1-2 days of Session 2 work.

Suggested
resolution: Session 2 adds `app/health_routes.py` to the template
         (mirror of `yral-rishi-agent-public-api/app/api/health_routes.py`,
         minus the `api/` package nesting) + wires
         `app.include_router(health_router)` in the template's
         `app/main.py`. Spawned services pick it up at next spawn.
         For already-spawned services (Session 3 public-api +
         Session 4's three services), back-fill the local bridge OR
         re-spawn fresh.

---

---

## RESOLVED

### DEP-003 — Session 2 needs Session 1 to confirm the three cluster overlay network names match the template's `docker-compose.swarm.yml`

Raised: 2026-05-13 by Session 2
Resolved: 2026-05-14 by Session 1 (PR `session-1/align-overlay-names-with-constraints-c3`, Rishi typed YES on Option (a) the same morning)
Resolution: Session 2 was right — Session 1's Day-1-2 drafts (PR #9 + PR #10) used non-CONSTRAINTS overlay names pulled from `V2_INFRASTRUCTURE_AND_CLUSTER_ARCHITECTURE_CURRENT.md` (`yral-agent-public-web-overlay`, `yral-agent-internal-service-to-service-overlay`, `yral-agent-data-plane-overlay`). CONSTRAINTS C3 verbatim is `yral-v2-public-web`, `yral-v2-internal`, `yral-v2-data-plane`, and per the CURRENT-TRUTH.md authority chain CONSTRAINTS wins. Renamed across 5 files (`node-bootstrap.sh`, `patroni-stack.yml`, `redis-sentinel-stack.yml`, `langfuse-stack.yml`, `caddy-swarm-service.yml`) plus an in-script role-comment capturing the doc-vs-CONSTRAINTS divergence for future re-readers. The live cluster's wrong-named overlays on rishi-4/5/6 are removed under a narrow A1 carve-out (Rishi typed YES same morning; scope = exactly those 3 names, after verifying zero containers attached) and recreated with the CONSTRAINTS names + `encrypted=true` confirmed cluster-wide. Names now match Session 2's `docker-compose.swarm.yml` exactly; hello-world spawn unblocked.

What:    `yral-rishi-agent-new-service-template/docker-compose.swarm.yml`
         (PR #18, branch `session-2/template-skeleton-compose`) declares
         three `external: true` overlay networks the template's spawned
         services attach to:

         - `yral-v2-public-web`   (edge → service traffic)
         - `yral-v2-internal`     (service → service RPC)
         - `yral-v2-data-plane`   (service → Postgres/Redis/Langfuse)

         These names come straight from CONSTRAINTS C3 (Saikat
         directive 2026-04-23, captured in V2_TEMPLATE_AND_CLUSTER_PLAN
         §1.7). I'm declaring them as `external: true` so the deploy
         fails fast if they don't exist — but that means Session 1's
         cluster-bootstrap stack MUST create them with exactly these
         three names before any service spawned from the template can
         deploy.

         Asking Session 1 to (a) confirm the names are correct in their
         bootstrap scripts, or (b) flag any drift so I can update the
         template before Day 3 spawns hello-world against the cluster.

Why:     Day 3 hello-world spawn-and-verify needs the cluster to have
         these overlays ready. If the names don't match exactly,
         `docker stack deploy` fails with an unhelpful
         "network yral-v2-public-web not found" error. Better to
         reconcile names before Day 3 than to debug it at deploy time.

Blocks:  Day 3 hello-world deploy verification (per template-and-hello-
         world role spec). NOT blocking template-folder PRs (PRs #17, #18,
         and the upcoming PR #19 for configs); those just declare the
         names and don't actually deploy.

ETA needed: Before Day 3 hello-world spawn (~2-3 days from now).

Suggested
resolution: Session 1 grep their bootstrap-scripts-for-the-v2-docker-
         swarm-cluster/ folder for these three overlay names and either
         (a) reply RESOLVED with "names match", or (b) propose new
         names + I update the template's swarm.yml + project.config.

---

### DEP-001 — Session 1 needs scope-lint paths corrected to match real folder layout
Raised: 2026-05-04 by Session 1
Resolved: 2026-05-04 by Coordinator (https://github.com/dolr-ai/yral-rishi-agent/pull/5, merge commit `6093004`)
Resolution: Coordinator updated `SESSION_PATHS[1..5]` in `.github/workflows/lint-scope-violations.yml` so each session's regex now includes the `yral-rishi-agent-plan-and-discussions/` prefix for both the relevant content folders and the per-session log/state/deps files. Session 1 rebased `session-1/sentry-baseline-cron` onto the fix; PR #4 scope-lint should pass on the next run.

What:    Three CI lint paths in `.github/workflows/lint-scope-violations.yml`
         and `.github/workflows/lint-state-hygiene.yml` did not match the real
         folder paths Session 1 must write to per `.claude/agents/session-1-infra-cluster.md`:

         (a) Latency-baseline scripts folder:
             - Spec / lint expected: `latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/`
             - Actual folder lives at: `yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/`
             - Coordinator-decided fix: either prepend the prefix in `SESSION_PATHS[1]`
               (one-line workflow edit) OR move the folder up to monorepo root
               (needs Rishi YES per A1 — moving an existing artifact).

         (b) Session 1's own log + state file paths:
             - Agent spec line 34 says Session 1 may write to "Your own session log + state file"
             - Real paths: `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md`
                          `yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-1-STATE.md`
             - Neither path was in `SESSION_PATHS[1]` — so any PR that updated them
               failed scope-lint.
             - Same applied to `cross-session-dependencies.md` (this very file)
               which sessions are expected to write OPEN entries to per I11.

         (c) `YRAL_SESSION_ID` env var was not set in this Claude Code session,
             so `.claude/hooks/post-tool-use.sh` (which reads it via
             `${YRAL_SESSION_ID:-coordinator}`) would write commit-trigger
             diary entries to `SESSION-coordinator-LOG.md` instead of
             `SESSION-1-LOG.md`. Until the session is restarted with the
             env var set, Session 1 wrote manual milestone entries
             directly to its own log.

Why:     PR #4 (Day 0.5 Sentry baseline pull) included:
         - Code in (a) — would have failed `lint-scope-violations`
         - Diary entry in (b) under SESSION-1-LOG — would have failed `lint-scope-violations`
         - Required state-hygiene update in (b) — would have failed BOTH lints if
           SESSION-1-LOG was required by `lint-state-hygiene.yml` AND blocked
           by `lint-scope-violations.yml`. Catch-22 until coordinator fixed.

Blocks:  Session 1 PR for sentry-baseline-cron Day 0.5 deliverable. Also
         blocked every future Session 1 PR until paths were reconciled.

ETA needed: Before PR #4 could be merged. Suggested fix was a 5-line
         edit to `.github/workflows/lint-scope-violations.yml` —
         coordinator handled in their own branch (PR #5).

Suggested
resolution: Update `SESSION_PATHS[1]` in lint-scope-violations.yml to:
         ```
         SESSION_PATHS[1]="bootstrap-scripts-for-the-v2-docker-swarm-cluster/|yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/|yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md|yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-1-STATE.md|yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md"
         ```
         Apply analogous fix for SESSION_PATHS[2..5]. Separately, set
         `YRAL_SESSION_ID=N` in each session's launch environment so
         the post-tool-use hook routes diary entries correctly.

### DEP-002 — Session 1 hit a bash parser bug in `.claude/hooks/post-tool-use.sh`
Raised: 2026-05-04 by Session 1
Resolved: 2026-05-04 by Coordinator (https://github.com/dolr-ai/yral-rishi-agent/pull/5, merge commit `6093004`)
Resolution: Coordinator quoted the heredoc tag in `.claude/hooks/post-tool-use.sh` so bash no longer tries to balance apostrophes inside the heredoc body. Auto-diary append on commit now lands cleanly; no more hook-blocking errors on `git commit`.

What:    The hook failed on every git commit with:
         `post-tool-use.sh: line 80: unexpected EOF while looking for matching ')'`
         The bug was in the `NEW_ENTRY=$(cat <<ENTRY ... ENTRY)` block —
         specifically the unquoted heredoc tag `<<ENTRY`, which let bash
         try to parse single-quote pairs inside the heredoc body. The
         body contains `there's` and `'s/^/- /'`; bash's parser ended up
         hunting for an unmatched apostrophe and falling off the end.
Why:     The hook is the I11 mechanism that auto-writes commit-trigger
         diary entries to `SESSION-N-LOG.md`. With the hook broken,
         every Session 1 commit emitted a hook-blocking error, and no
         auto-diary entry was appended. Sessions were forced to write
         every entry manually (which is what I did for Session 1's
         first commit `68bc52b`).
Blocks:  Not a hard merge block — commits still succeeded. But every
         Bash tool call that ran `git commit` surfaced the hook error
         to the session, which was noisy and arguably scope-blocking
         for an Auto-mode session expected to commit unattended.
ETA needed: Coordinator fix-by-Session-2 launch (Day 1 end) so other
         sessions did not hit the same surprise on their first commit.
Suggested
resolution: Quote the heredoc tag — change `<<ENTRY` to `<<'ENTRY'` on
         the line that opens the `cat` heredoc. Quoted heredoc tags
         disable variable expansion AND fix the apostrophe-parser
         issue. Then move the `$TIMESTAMP / $COMMIT_SHA / $COMMIT_MSG /
         $FILES_CHANGED` substitutions out of the heredoc and into a
         `printf` call after the heredoc body is captured. Or rewrite
         the block as a series of `echo`/`printf` lines instead of one
         heredoc — eliminates the parsing edge case entirely.

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
