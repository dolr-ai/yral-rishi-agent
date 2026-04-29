# 🚦 MASTER STATUS — yral-rishi-agent v2 build
> Auto-updated every 15 min by coordinator (post-launch). Last update: 2026-04-27 PRE-LAUNCH.

═════════════════════════════════════════════════════════════
  ❓ AWAITING RISHI  ← read this first
═════════════════════════════════════════════════════════════

  📋 **PRE-LAUNCH CHECKLIST** — 6 items, ~30-45 min total. Do them in any order.
     Each item below has WHAT / HOW / TIME / WHY. ADHD-friendly mini-runbooks.
     When ALL checked, type "build" in coordinator session.

  ─────────────────────────────────────────────────────────────

  ☐ 1. CREATE OPENAI CODEX API KEY  (~5 min)
       WHAT: A key Codex uses to review every PR per I10.

       HOW:
         a. Open https://platform.openai.com/api-keys
         b. Sign in with the OpenAI account you want to bill from
         c. Click "Create new secret key"
         d. Name it: yral-rishi-agent-codex-review
         e. Permissions: leave default (full)
         f. Copy the key (starts with sk-...) — you only see it ONCE
         g. Open https://github.com/dolr-ai/yral-rishi-agent/settings/secrets/actions
         h. Click "New repository secret"
         i. Name: OPENAI_CODEX_API_KEY
         j. Value: paste the key
         k. Click "Add secret"

       WHY: Codex GitHub Action calls this to review every PR. ~$120/mo
            estimated cost. Without it, PR review falls back to me + you only.

  ─────────────────────────────────────────────────────────────

  ✅ 2. SENTRY API TOKEN — DONE (you set this up; stored in macOS Keychain)
       WHERE: macOS Keychain, account `dolr-ai`, service `SENTRY_AUTH_TOKEN`
       READ:  `security find-generic-password -a dolr-ai -s SENTRY_AUTH_TOKEN -w`
       SCOPE: broad read; never write/admin without asking (per memory)
       Day 0.5 baseline script will read from Keychain (not file).

  ─────────────────────────────────────────────────────────────

  ☐ 3. SET GITHUB BRANCH PROTECTION ON main  (~3 min)
       WHAT: Prevents direct pushes to main; forces PRs.

       HOW:
         a. Open https://github.com/dolr-ai/yral-rishi-agent/settings/branches
         b. Click "Add rule" (or edit existing rule for main)
         c. Branch name pattern: main
         d. Check these boxes:
              ☑ Require a pull request before merging
                  ↳ Require approvals: 1
                  ↳ Dismiss stale pull request approvals when new commits are pushed
              ☑ Require status checks to pass before merging
                  ↳ Add: "Codex Review on PR" (will appear after first PR opens)
                  ↳ Add: "lint-naming-and-comments"
                  ↳ Add: "lint-scope-violations"
              ☑ Require linear history (recommended)
              ☑ Do not allow bypassing the above settings (under "Who can bypass")
         e. Click "Create" / "Save changes"

       WHY: Enforces I10 (Codex review on every PR) + I14 (auto-merge low-
            risk only) + I9 (no direct main writes) at the GitHub level.
            Defense in depth — even if a session tries to push direct, GitHub blocks.

  ─────────────────────────────────────────────────────────────

  ☐ 4. SAIKAT SIGN-OFF  (~5 min message + wait for reply)
       WHAT: Saikat owns the shared infra. Two things need his explicit OK
             before I touch anything.

       HOW: send Saikat a single message, e.g. on Telegram / Slack / email.
       Suggested text:

       ───────────────────────────────────────────────────
       Hey Saikat — 2 quick approvals before yral-rishi-agent v2 build kicks off:

       1. Phase 0 cluster provisioning on rishi-4/5/6 (Days 4-7):
          Docker Swarm + Patroni HA Postgres + Redis Sentinel + Langfuse +
          Caddy as Swarm service. Time-limited root for ~1 week, then
          scoped sudoers (rishi-deploy user, like legacy). OK?

       2. Day 7: adding ONE Caddy snippet to yral-rishi-hetzner-infra-template
          (the repo you and I built for Caddy on rishi-1/2):
          file: caddy/conf.d/agent.rishi.yral.com.caddy
          purpose: reverse-proxy agent.rishi.yral.com → rishi-4:443 + rishi-5:443
          method: PR through the existing template pipeline; you review + merge.
          OK?

       Plan repo: https://github.com/dolr-ai/yral-rishi-agent
       Specifically CONSTRAINTS A2 (carve-out for #2) + A13 (build-mode lift).
       Thx 🙏
       ───────────────────────────────────────────────────

       WHY: A2 says rishi-1/2/3 are hands-off by default. The Caddy snippet is
            the single allowed exception. Cluster provisioning is the build-mode
            lift (A13). Both need explicit Saikat YES — without it, Day 4-7
            and Day 7 are blocked.

  ─────────────────────────────────────────────────────────────

  ☐ 5. BLOCK ~20 MIN/DAY ON CALENDAR FOR PR APPROVALS  (~2 min)
       WHAT: A daily slot to read PRs + Codex reviews + type YES.

       HOW:
         a. Open your calendar (Google / Apple / whatever you use)
         b. Add recurring event: "yral v2 PR approvals"
         c. 2 slots/day, 10 min each:
              - Morning (e.g. 10:00 IST after coffee)
              - Evening (e.g. 18:00 IST before signing off)
         d. Set notification 5 min before
         e. Repeat: weekdays for next 12 weeks

       WHY: 3 active sessions × 1-2 PRs/day = 3-6 PRs/day for you to YES.
            Auto-merge for low-risk PRs (I14) cuts this in half. So
            realistically 2-4 PRs/day = 10-20 min total. The recurring slot
            keeps you from being the bottleneck. ADHD-friendly: same time
            every day, no decision fatigue about WHEN to do it.

  ─────────────────────────────────────────────────────────────

  ☐ 6. READ 06-STATE-PERSISTENCE-AND-RESUME.md  (~10 min)
       WHAT: One-time read so you know how to recover when laptop crashes.

       HOW:
         a. Open: yral-rishi-agent-plan-and-discussions/
            multi-session-parallel-build-coordination/
            06-STATE-PERSISTENCE-AND-RESUME.md
         b. Read end-to-end. Pay attention to:
              - The 5 layers (LOG / STATE / dependencies / MASTER-STATUS /
                daily-reports / decision-log)
              - Resume protocol (~5 min recovery time)
              - The "Recovery scenarios" section near the end

       WHY: When (not if) something interrupts (laptop crash, accidental
            terminal close, bad merge), this is the playbook. Reading it
            ONCE now means zero panic later. ADHD-friendly: the future-you
            will thank present-you.

  ─────────────────────────────────────────────────────────────

  When ALL 6 boxes are checked: type **build** in this coordinator session.
  I do ~45 min of warm-up (interface contracts, GitHub Actions scripts,
  state persistence infra, subagent definitions, hooks, test PR), then
  you launch Sessions 1, 2, 5 in their tmux panes.

═════════════════════════════════════════════════════════════
  📊 SESSION HEALTH
═════════════════════════════════════════════════════════════

  ⚪ Session 1: Infra & Cluster — NOT YET LAUNCHED
     Will launch Day 1 when Rishi types "build" + opens Session 1

  ⚪ Session 2: Template & Hello-World — NOT YET LAUNCHED
     Will launch Day 1 (parallel with Session 1)

  ⚪ Session 5: ETL & Tests — NOT YET LAUNCHED
     Will launch Day 1 (third of three Day-1 sessions)

  ⚪ Session 3: Public-API & Auth — NOT YET LAUNCHED
     Scheduled Day 9 (after cluster + template are live)

  ⚪ Session 4: Orchestrator + Soul File + Influencer — NOT YET LAUNCHED
     Scheduled Day 9

═════════════════════════════════════════════════════════════
  🤖 AUTO-MERGED IN LAST 24H
═════════════════════════════════════════════════════════════
  None (build not started).

═════════════════════════════════════════════════════════════
  🔗 OPEN CROSS-SESSION DEPENDENCIES
═════════════════════════════════════════════════════════════
  None.

═════════════════════════════════════════════════════════════
  📈 LATENCY BASELINE
═════════════════════════════════════════════════════════════
  Will populate when Sentry baseline cron lands (Session 1 Day 0.5).

═════════════════════════════════════════════════════════════
  🧪 TESTING HEALTH (post-launch — populated by CI per J1-J6)
═════════════════════════════════════════════════════════════
  Pre-launch — no tests yet. Will populate Day 1 after first PRs land.

  Format (when active):
    🟢 HOT-tier services (75-80% floor):
       public-api: 78% (target 75%) ✅
       orchestrator: 76% (target 75%) ✅
       safety: 82% (target 80%) ✅
    🟢 WARM-tier services (50-60% floor): ...
    🟢 COOL-tier services (25-35% floor): ...
    Quarantined flaky tests: 0
    Codex test-review concerns last 24h: 0

═════════════════════════════════════════════════════════════
  💰 SPEND THIS WEEK
═════════════════════════════════════════════════════════════
  $0 — pre-launch.

═════════════════════════════════════════════════════════════
  ⏰ DECISIONS LOCKED 2026-04-27
═════════════════════════════════════════════════════════════
  ✅ Doc standard option (a) — heavy standard, kept as written (B7 + F8)
  ✅ Codex 1st-pass audit fixes applied (paywall 402, root README phase
     ordering, build-mode ambiguity, TIMELINE Cloudflare Tunnel)
  ✅ Codex 2nd-pass audit fixes applied (TIMELINE pre-Day-1 questions,
     mobile contract doc aligned with A16)
  ✅ Doc role boundaries codified in CURRENT-TRUTH.md
  ✅ 3-then-5 session sequencing locked (Day 1 = Sessions 1+2+5)
  ✅ Per-service secrets.yaml manifest pattern locked (D8 — 2026-04-28)
  ✅ Risk-weighted testing strategy locked (J1-J6 — 2026-04-29, Option E)
  ✅ Skipping GitHub Issues for work tracking (overhead > value)
  ✅ Rishi has SSH access to rishi-4/5/6 (Saikat sign-off satisfied)

═════════════════════════════════════════════════════════════
  ⏰ TODAY'S CRITICAL PATH
═════════════════════════════════════════════════════════════
  1. Rishi works through 6-item PRE-LAUNCH CHECKLIST above (~30-45 min)
  2. When all 6 done, Rishi types "build" in coordinator session
  3. Coordinator runs ~45 min warm-up (per 05-GETTING-STARTED-TOMORROW.md)
  4. Rishi launches Sessions 1, 2, 5 in tmux panes (per I17)
