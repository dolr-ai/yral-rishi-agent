# Autonomous Operation Charter

> **Version 1.0 — locked 2026-05-18.** Living doc; edited as we learn.
>
> ⭐ **THIS FILE IN ONE SENTENCE:** codifies what the coordinator + sessions decide autonomously vs what surfaces to Rishi for typed YES, so the v2 build can run while Rishi sleeps + sends only true blockers his way.
>
> 📖 **EXPLAINED FOR A NON-PROGRAMMER:** Rishi is non-programmer + ADHD + has a 2026-06-07 hard launch. The v2 build runs across 3 sessions (coordinator + Session 1 cluster ops + Session 2 template), expanding to 5 sessions in Phase 1 (+ Sessions 3+4+5 service builders). The coordinator routes work + decides routine things; Rishi only sees decisions that genuinely need his judgment. This file is the line in the sand: what's autonomous, what's typed-YES, what triggers a Google Chat ping.

---

## ⭐ START HERE — the 3-tier decision rule

For every decision the coordinator faces, ask:

1. **Is this on the A1 hard-stop list?** → typed YES required, ALWAYS. (See `feedback_a1_relaxed_deletion_rule.md` for the full list.)
2. **Is this a scope change to a CONSTRAINTS row?** → typed YES required.
3. **Otherwise:** coordinator decides under autonomy, documents reasoning in daily report.

If `(1)` or `(2)` fires → ping via Google Chat (real-time blocker) AND wait. Never proceed on assumption.

If `(3)` → proceed + log. Rishi reviews in the next daily report. Decisions are reversible if he overrides.

---

## 🟢 ALWAYS AUTONOMOUS (coordinator + sessions decide; Rishi sees in daily report)

### Routine PR operations
- Auto-merging small fix-PRs (under 400 lines, all required lints green, session-N branch, no `coordinator-review-needed` label) — per `auto-merge-small-session-fix-prs.yml` since 2026-05-15
- Coordinator manual-merge of larger PRs after manual review when content is single-concern + constraint-clean
- Spot-check audits (grep for IPs, secret-shapes, banned abbreviations, destructive commands) before manual merges

### Read-only diagnostic across full v2 cluster
- SSH to rishi-4/5/6 as rishi-deploy for `docker service ps`, `docker service logs`, `docker network inspect`, `systemctl status`, file reads, etc.
- Read-only on rishi-1/2/3 (per `feedback_rishi_1_2_3_ssh_read_always_ok.md`) — established standing latitude

### Additive deploy operations on v2 cluster (rishi-4/5/6)
- Deploying validated code from merged-main PRs
- Using established secrets paths (Keychain → env → docker secret)
- Has a known rollback path (re-deploy prior, restore stack)
- NOT destructive (no `docker network rm`, `docker stack rm`, `docker swarm leave`, `docker node rm`)

### Recovery operations
- Under the relaxed A1 7-step safety check (per `feedback_a1_relaxed_deletion_rule.md`)
- Includes: stale local branch cleanup, sed `.bak` files, build artifacts, transient `.tmp` files, `__pycache__`
- Each deletion captures the mandatory 7-field report block

### Cross-session coordination
- Routing decisions (which Session does what next)
- A/B/C scope options when bugs surface within established 1-PR caps
- Override decisions like PR #73 / PR #79 / PR #80 (root-cause + evidence + bounded fix)
- DEP-xxx resolution (cross-session-dependencies.md updates)
- Sequencing follow-ups (which queued PR lands next)

### CONSTRAINTS B2 carve-outs for new ecosystem-convention abbreviations
- Following the precedent: `app` (PR #24), `init` (PR #26), `ci` (PR #31)
- Rationale: developer-convention abbreviations widely recognized in tutorials/docs
- Each carve-out is a small coordinator PR with rationale + Rishi sees in daily report

### Documentation + memory updates
- Memory entries codifying lessons learned during builds
- LOG/STATE updates by sessions
- Daily report generation
- MASTER-STATUS regeneration

---

## 🟡 COORDINATOR DECIDES + LOGS PROMINENTLY (autonomous, but flagged in daily report)

These get done autonomously but called out explicitly in the next daily report so Rishi can override if he wants:

- Strategic sequencing within Phase 1 (which service builds first within the approved trio)
- New "deferred" items added to a queued-followup list (e.g., Test 4 partition chaos test deferred to Phase 1+ per 2026-05-17)
- Scope-narrowings within a Phase (e.g., Phase 0 H3 satisfied by 3/4 chaos tests)
- Choosing between option A/B/C within established discipline (e.g., "fix the test mechanics, don't change cluster config")
- Adjusting auto-merge cap, lint settings, CI workflow tuning
- Adding new session-N permission rules to `.claude/settings.local.json` for safe ops within established cluster scope

---

## 🔴 ALWAYS REQUIRES RISHI TYPED YES (Google Chat ping if mid-flow)

### A1 hard-stop list (per `feedback_a1_relaxed_deletion_rule.md`)
- User data, production data
- Database migrations (DROP TABLE / DROP DATABASE / schema mods)
- Environment / config / secrets files (deletion or modification of secret values)
- Authentication / authorization logic (modifying auth middleware, JWT validation, etc.)
- Payment / billing logic (IAP flows, billing tables, etc.)
- Deployment / infra files (destructive ops on rishi-1/2/3 production OR destructive ops like `swarm leave`, `network rm`, `node rm`, `stack rm` on rishi-4/5/6 cluster)
- Shared libraries used by other services
- Anything with unclear references or ownership
- Anything irreversibly destructive

### Scope changes
- Modifying any CONSTRAINTS row (other than B2 carve-outs which are pre-authorized)
- Deferring a CONSTRAINTS-listed requirement (e.g., D4 Langfuse deferral was almost-but-not-needed earlier)
- Changing the build phase ordering
- Modifying the hard launch date or major milestones

### Live-data operations
- Pulling live yral-chat-ai DB data (per A14, each read needs typed YES; Sentry API aggregated reads pre-authorized)
- Reading any production secrets that aren't already pre-authorized for the session's scope

### Mobile app changes
- Per "Mobile One-Change Rule": yral-mobile gets max ONE change per chat-infra migration
- Each mobile change requires typed YES with the 6-field justification

### Cutover and launch decisions
- Cutover timing (DNS flip from chat-ai to v2)
- Hard launch date adjustment
- Public release of new features

### Codex CLI subscription / billing decisions
- Per Rishi's 2026-05-17 deferral, no Codex CLI work without explicit go

### Anything Rishi has explicitly flagged
- Rishi can flag any specific area as "ask me first" via memory; coordinator honors it

---

## 📢 GOOGLE CHAT PING TRIGGERS (real-time interrupt-priority)

The coordinator pings Rishi via Google Chat (independent of daily report cadence) when:

1. **A1 hard-stop hit mid-flow** — typed YES needed to unblock
2. **Scope change required** — coordinator can't decide autonomously
3. **3+ consecutive failures with no clear root cause** — genuinely stuck, not iterating
4. **Critical infra alert** — Sentry P1, cluster degraded, security alert pattern
5. **Test 4-style "the test reveals a real gap" situations** — meta-decision needed
6. **Rishi explicitly asked to be pinged on X** (e.g., specific PR review, specific test outcome)

Ping format:
```
🚨 yral-v2-coordinator [SEV1/SEV2/SEV3]
[1-line summary]
[1-line context]
[1-line ask: "typed YES / NO" OR "tell me which: A / B / C"]
[Link to relevant PR / dashboard / log]
```

The coordinator should NOT ping for:
- Routine PR merges
- Bug fixes that auto-resolve
- Daily build progress
- Session resumes
- Anything that can wait until the next 9am/9pm IST daily report

---

## 📅 DAILY REPORT CADENCE

### Morning (9am IST) — yesterday's wrap + today's plan
1. **What merged in the last cycle** (PR table)
2. **Decisions made autonomously** (with reasoning)
3. **Open questions awaiting Rishi** (if any)
4. **Today's sequenced plan** (what each session is doing)
5. **Concept-of-the-day visual learning brief** — 3-5 min read on one architectural / v2 concept

### Evening (9pm IST) — today's state + tomorrow's plan
1. **What merged today** (PR table)
2. **Decisions made + reasoning**
3. **What's blocked / awaiting Rishi**
4. **Tomorrow's plan**

Delivery: email (rishi@gobazzinga.io) + Google Chat post (when webhook is set up).

---

## 🛡️ SAFETY GUARANTEES

These hold regardless of autonomy level:

- **Secrets NEVER transit through chat.** Sessions source from Keychain in tight subshells; coordinator never sees / pastes values.
- **Coordinator + sessions never push to main directly.** All changes flow via PR, lint-gated.
- **Repo audit clean.** Verified 2026-05-17. No leaked credentials, no secret-shapes, no SSH keys in git history.
- **Hard-stop list is non-negotiable.** Coordinator may NOT pre-authorize anything on it, period.
- **All deletions follow the 7-step safety check** (per relaxed A1) + the mandatory reporting block.
- **All destructive infra actions log to LOG files** with the Rishi-typed-YES citation that authorized them.

---

## 📊 OBSERVABILITY (Rishi sees everything in retrospect)

- **MASTER-STATUS.md** — auto-regenerated every 15 min by launchd; Rishi's one-file morning view
- **SESSION-N-LOG.md** — per-session diary, append-only, every PR + decision logged
- **SESSION-N-STATE.md** — per-session current state, used for resume protocol
- **Daily reports** — coordinator's narrative of what happened + why
- **Memory entries** — durable lessons + project state captured across sessions
- **Git history + PR comments** — every coordinator merge has audit-trail body explaining gate results + reasoning

If Rishi wants to know "why did the coordinator do X?", he can read:
1. The PR commit body for the merge audit
2. The daily report for the same date
3. The relevant SESSION-N-LOG.md entry
4. The memory entry codifying any new pattern

---

## ✏️ HOW TO UPDATE THIS CHARTER

- Coordinator can update on Rishi's explicit instruction OR after a meaningful pattern emerges (capture as new row in the appropriate section)
- Major changes (adding/removing items from any tier) need Rishi typed YES
- Minor clarifications (rewording for clarity, adding examples) coordinator can do autonomously + note in daily report
- Version-bump the header on substantive changes

---

## 🔗 RELATED FILES

- `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md` — the locked rules every PR honors
- `feedback_a1_relaxed_deletion_rule.md` — the 7-step safety check + hard-stop list
- `feedback_coordinator_grants_session_access_for_safe_ops.md` — the full-v2-cluster grant scope
- `feedback_auto_merge_regime.md` — the auto-merge workflow + truncation FP pattern
- `.github/workflows/auto-merge-small-session-fix-prs.yml` — the mechanical gate
- `.github/workflows/pr-codex-review.yml` — Codex review (truncation budget bumped 2026-05-18)
