# Auto-Mode Guardrails

> Auto-mode = Claude Code's most-permissive permission setting where the agent runs commands without per-call approval. Speed comes at the cost of less human-in-the-loop. The guardrails below are what keeps Auto-mode safe.

## What Auto-mode means in practice

Without Auto-mode (default Claude Code): every shell command, every file write, every git push asks Rishi YES/NO. Slow but safe.

With Auto-mode (this plan): the session runs commands per its scope file without prompts. Fast. But ONLY for the operations enumerated below.

## ✅ Auto-allowed operations

```
   FILE OPERATIONS
   ───────────────
   ✅ Read any file in the monorepo
   ✅ Write/edit files inside this session's scope
   ✅ Create new files inside this session's scope
   ✅ git add / commit on this session's branch (session-N/*)
   ✅ git push to this session's branch on origin

   PROCESS OPERATIONS
   ──────────────────
   ✅ Run pytest, gradle, npm, cargo (any local test runner)
   ✅ docker compose up/down/build (laptop only)
   ✅ docker stack deploy on rishi-4/5/6 (Day 8+, only this session's stack)
   ✅ Run any script inside this session's scope folder
   ✅ Spawn local subprocesses (linters, formatters, generators)

   NETWORK OPERATIONS
   ──────────────────
   ✅ HTTP/HTTPS requests to docs / Stack Overflow / GitHub (lookup)
   ✅ HTTP requests to localhost on laptop (own services)
   ✅ HTTP requests to https://agent.rishi.yral.com after Day 8
   ✅ HTTP requests to sentry.rishi.yral.com Sentry API (pre-authorized A14)
   ✅ SSH to rishi-4/5/6 with `deploy` user (Session 1 only; Sessions 3-5
       deploy via CI/CD, not direct SSH)
   ✅ git fetch / git pull from origin

   PR OPERATIONS
   ─────────────
   ✅ gh pr create against main (CI takes over from there)
   ✅ gh pr comment (responding to Codex feedback on own PR)
   ✅ gh pr checks (verify own PR's CI is green)
```

## 🛑 Auto-forbidden operations

These ALWAYS require Rishi to type explicit YES, even in Auto-mode. The session must STOP and ask through the coordinator.

```
   DATA OPERATIONS
   ───────────────
   🛑 SSH to rishi-1, rishi-2, rishi-3 for ANY purpose other than the
       explicitly authorized Caddy snippet add (Session 1, Day 7 only,
       and that goes through PR to yral-rishi-hetzner-infra-template,
       NOT direct SSH from auto-mode)
   🛑 Reading row-level data from live yral-chat-ai DB (per A14)
   🛑 pg_dump or psql with SELECT against rishi-1/2/3 production DBs
   🛑 Any operation that modifies live chat-ai data (read-only is also
       blocked by A14 unless Rishi-approved per-operation)

   GIT OPERATIONS
   ──────────────
   🛑 git push to main directly (PRs only)
   🛑 git push --force on any branch
   🛑 git push to yral-mobile origin (per A12 — never)
   🛑 Deleting any branch, tag, or remote ref
   🛑 git rebase --interactive (interactive ops blocked in auto)
   🛑 Modifying commits not authored by this session

   FILE OPERATIONS
   ───────────────
   🛑 Editing files outside this session's scope
   🛑 Editing CONSTRAINTS.md, README.md, TIMELINE.md (coordinator only)
   🛑 Editing other sessions' SESSION-N-LOG.md
   🛑 Editing memory files in ~/.claude/projects/-Users-rishichadha/memory/
   🛑 Editing files in shared-library-code-used-by-every-v2-service/
       (request via PR to coordinator)
   🛑 Editing .github/workflows/ (coordinator only)
   🛑 Deleting ANY file (per A1 — no-delete covenant)

   INFRASTRUCTURE OPERATIONS
   ─────────────────────────
   🛑 Provisioning new Hetzner servers
   🛑 Modifying DNS records (Cloudflare or otherwise)
   🛑 Adding new GitHub repos under dolr-ai org
   🛑 Modifying GitHub repo settings, branch protection, or secrets
   🛑 Rotating any production secret (chat-ai, billing, auth)
   🛑 docker stack rm of any production stack
   🛑 Anything that affects rishi-1/2/3 beyond reading log files

   COMMUNICATION OPERATIONS
   ────────────────────────
   🛑 Sending Slack/Discord/email to anyone (Saikat, Sarvesh, Shivam, etc.)
   🛑 Filing GitHub issues against repos outside this session's owned scope
   🛑 Tagging or notifying users on PRs (let coordinator handle)
   🛑 Posting to any external service (Twitter, Discord, etc.)

   FINANCIAL OPERATIONS
   ────────────────────
   🛑 Adding paid dependencies > $10/month without Rishi YES
   🛑 Subscribing to any new SaaS
   🛑 Provisioning paid resources (cloud DBs, hosted vector stores)

   CUTOVER OPERATIONS
   ──────────────────
   🛑 Discussing, planning, or implementing v2 cutover (per A6)
   🛑 Modifying chat-ai routing on rishi-1/2 to point at v2
   🛑 Anything that could move user traffic from chat-ai to v2

   META-OPERATIONS
   ───────────────
   🛑 Modifying these guardrails
   🛑 Modifying Auto-mode permission settings
   🛑 Spawning new sub-agents that have looser permissions
   🛑 Disabling CI checks
   🛑 Skipping pre-commit hooks (--no-verify)
```

## How forbidden operations are enforced

Defense in depth:

```
   LAYER 1 — Session prompt
   ────────────────────────
   Each session's startup prompt explicitly tells it the forbidden list.
   Claude reads, acknowledges, internalizes.

   LAYER 2 — File-system permissions
   ─────────────────────────────────
   Memory files in ~/.claude/projects/ are technically writable but
   sessions are instructed never to touch them. Coordinator monitors
   for accidental writes.

   LAYER 3 — CI lint workflow
   ──────────────────────────
   .github/workflows/lint-scope-violations.yml runs on every PR.
   Inspects diff: every file changed must be inside the PR's owning
   session scope (inferred from branch name: session-N/*).
   PR fails if scope violated.

   LAYER 4 — Branch protection on main
   ───────────────────────────────────
   No direct pushes to main. PRs only. PRs require:
     • CI green (lints + tests + Codex review)
     • Coordinator approval comment
     • Rishi YES comment
   Configured in GitHub repo settings (manual, by Rishi).

   LAYER 5 — Codex review
   ──────────────────────
   Codex reads every PR independently. Flags scope violations,
   constraint breaches, suspicious patterns.

   LAYER 6 — Coordinator session
   ─────────────────────────────
   Reads all PR diffs and Codex reviews before recommending merge.
```

## When a session hits a forbidden operation

The session MUST:
1. STOP whatever it's doing
2. Write to its SESSION-N-LOG.md: "Hit forbidden op X at time T while trying to do Y"
3. Open a small "blocker PR" or comment on the next regular PR explaining what it needs
4. Wait for coordinator (you+me) to surface the blocker to Rishi
5. Resume only after Rishi types YES

The session must NOT:
- Try to work around the restriction
- Find a clever way to do the same thing through a different path
- Silently skip the work that needed the forbidden op
- Disable the guardrail

## Auto-mode kill-switch

If something goes wrong (a session goes haywire, files appear in wrong places, billing alerts fire):

```
   1. Coordinator (you+me) types in any session: "STOP AUTO-MODE"
   2. That session immediately drops back to per-command approval
   3. Coordinator inspects what happened
   4. Session resumes only after coordinator says so

   Universal kill-switch:
   5. Rishi types "STOP ALL SESSIONS" in coordinator
   6. Coordinator pings each session, telling it to halt
   7. All sessions drop to per-command approval until Rishi resumes
```

## What Auto-mode actively gives up

By choosing Auto-mode for sessions 1-5, we accept:

- Less visibility into individual command decisions (mitigated by SESSION-N-LOG.md)
- Higher trust placed in the session's reasoning (mitigated by Codex review)
- Faster iteration but harder real-time intervention (mitigated by kill-switch)
- A learning curve: first week may need tuning of guardrails as edge cases surface

The trade-off is throughput. We're betting it's worth it for a 6-week build instead of a 6-month one.
