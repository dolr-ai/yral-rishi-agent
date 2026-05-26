# Multi-Session Parallel Build Coordination — Master Plan

> **Status:** DRAFT, locked 2026-04-27 by Rishi for review.
> **Lifts when:** Rishi types "build" and starts session 1.
> **Coordinator session:** the Claude Code session that owns this folder + CONSTRAINTS.md + README.md (currently this one).

## What this is

A plan for running v2 development across **5 parallel Claude Code sessions in Auto-mode**, with **Codex as the second-opinion reviewer on every PR**, and **Rishi as the final approver on merges to main**.

Goal: build v2 fast, in parallel, without sessions stepping on each other or violating the 70+ constraints in `CONSTRAINTS.md`.

## Why this approach

1. **Parallelism = speed.** v2 has ~13 services + template + cluster + ETL + tests + docs. Sequential build = 6+ months at 2-3 hours/day. Parallel = 6-8 weeks.
2. **Codex as independent reviewer = quality.** Codex isn't in the same chat history as Claude; it reads the diff fresh. Catches things Claude missed.
3. **Auto-mode = throughput.** Claude doesn't pause for permission on every command. Risk is contained because each session has a strict scope file + no rights outside it.
4. **Coordinator session = sanity.** One session (you + me, here) holds the cross-cutting context. Sessions 1-5 don't fight over CONSTRAINTS, README, plan docs.

## Sequencing decision (locked 2026-04-27 — refined from 5)

- **Day 1 launch: 3 sessions (1, 2, 5).** These are the ones that don't depend on cluster + template being live.
- **Day 9 launch: Sessions 3, 4** join — once cluster is up and template has spawned hello-world successfully.
- Reason: Sessions 3+4 build on Session 1's cluster + Session 2's template. Launching them earlier means idle context burn.
- Coordinator's earlier pushback ("start with 3, scale to 5") accepted by Rishi 2026-04-27.

## The 5 sessions

| # | Name | Owns | First-week deliverables |
|---|------|------|------------------------|
| **1** | Infra & Cluster | `bootstrap-scripts-for-the-v2-docker-swarm-cluster/` | Sentry baseline cron (Day 0.5); rishi-4/5/6 bootstrap (Days 4-7); rishi-1/2 Caddy snippet (Day 7); chaos tests (Day 8) |
| **2** | Template & Hello-World | `yral-rishi-agent-new-service-template/` | Template scaffolding (Days 1-3); hello-world spawn + verify (Day 3); Tier 0 browser debug page (Day 4) |
| **3** | Public-API & Auth | `yral-rishi-agent-public-api/` | Skeleton + JWT validation + JWKS cache + billing pre-check (Days 9-13); WebSocket inbox + SSE skeleton (Days 14-18) |
| **4** | Orchestrator + Soul File + Influencer | `yral-rishi-agent-conversation-turn-orchestrator/`, `yral-rishi-agent-soul-file-library/`, `yral-rishi-agent-influencer-and-profile-directory/` | Turn lifecycle (Days 9-12); 4-layer Soul File composer (Days 13-15); influencer CRUD (Days 9-15 in parallel) |
| **5** | ETL + Memory + Tests | `etl-scripts/`, `yral-rishi-agent-user-memory-service/`, `tests/` | Day 9 ETL plan + run with Rishi YES; contract tests vs chat-ai (Days 10-15); memory service stub (Phase 2) |

The **coordinator** (this session) does NOT own services — it owns plans, constraints, integration, conflict resolution, Codex-review reading, and Rishi-merge-approval routing.

## How each session is launched

### Session start ritual (mandatory for each of 1-5)

1. **Open new Claude Code session** in `~/Claude Projects/yral-rishi-agent/`
2. **First message Rishi sends**: paste the session-N startup prompt (see `01-SESSION-SHARDING-AND-OWNERSHIP.md` for each session's exact prompt)
3. **Session reads its scope file** (`SESSION-N-SCOPE.md`) and confirms back: "I own these folders, I will not touch others, here's my plan for the first day"
4. **Rishi types `build` in the session** (per A5 — explicit build approval)
5. **Session enters Auto-mode**, starts working
6. **Session checkpoints every ~2 hours** in its own SESSION-N-LOG.md with what it built, what's next
7. **Session opens PR when a unit of work is complete**, NOT after every commit. Granularity: ~1 PR per day per session.

### Auto-mode rules (universal across sessions)

See `02-AUTO-MODE-GUARDRAILS.md` for full list. Key:

- ✅ Auto-allowed: code in own scope, local tests, docker compose, branch pushes
- 🛑 Auto-forbidden: pulling live data, mobile push, rishi-1/2/3 writes, deletions, main merges, CONSTRAINTS edits, cross-scope file edits

## How sessions coordinate without colliding

```
              ┌─────────────────────────────────────┐
              │   1. SCOPE FILES are the contract   │
              │   Each session can ONLY edit files  │
              │   inside its owned subfolder(s).     │
              │                                       │
              │   CI lint enforces this:              │
              │   PR reviewer (Codex) flags any file  │
              │   touched outside the owning scope.   │
              └─────────────────────────────────────┘

              ┌─────────────────────────────────────┐
              │   2. INTERFACE CONTRACTS first       │
              │   Coordinator writes:                 │
              │   • API contract: what public-api    │
              │     exposes to mobile                │
              │   • Internal contract: what          │
              │     orchestrator expects from        │
              │     soul-file-library + memory       │
              │   • DB schema: which tables each     │
              │     service owns                     │
              │                                       │
              │   ALL these in `interface-contracts/`│
              │   BEFORE sessions 3-5 start coding.  │
              └─────────────────────────────────────┘

              ┌─────────────────────────────────────┐
              │   3. SHARED LIBRARY discipline       │
              │   `shared-library-code-used-by-      │
              │   every-v2-service/` is owned by     │
              │   coordinator only.                   │
              │                                       │
              │   If a session needs a shared util,  │
              │   it requests it via PR to           │
              │   coordinator, not by editing the    │
              │   shared lib directly.               │
              └─────────────────────────────────────┘

              ┌─────────────────────────────────────┐
              │   4. DAILY STAND-UP (with Rishi)     │
              │   ~10 minutes morning ritual:        │
              │   • Each session shares its yesterday│
              │     log + today's plan               │
              │   • Coordinator flags conflicts      │
              │   • Rishi makes blocking decisions   │
              └─────────────────────────────────────┘
```

## Codex review process (high level)

```
   Session opens PR → GitHub Actions triggers Codex review
                   → Codex posts inline comments on diff
                   → Coordinator session reads both
                   → Rishi reads coordinator's summary
                   → Rishi types YES on PR comment
                   → GitHub auto-merges
                   → Auto-deploy to staging (Day 8+)
```

Full detail: `03-CODEX-REVIEW-WORKFLOW.md`

## What's stored where

```
yral-rishi-agent/
├── yral-rishi-agent-plan-and-discussions/
│   ├── CONSTRAINTS.md           ← coordinator owns
│   ├── README.md                ← coordinator owns
│   ├── TIMELINE.md              ← coordinator owns
│   └── multi-session-parallel-build-coordination/
│       ├── 00-MASTER-PLAN.md                       ← THIS FILE
│       ├── 01-SESSION-SHARDING-AND-OWNERSHIP.md
│       ├── 02-AUTO-MODE-GUARDRAILS.md
│       ├── 03-CODEX-REVIEW-WORKFLOW.md
│       ├── 04-COORDINATOR-SESSION-PLAYBOOK.md
│       ├── 05-GETTING-STARTED-TOMORROW.md
│       ├── 06-STATE-PERSISTENCE-AND-RESUME.md      ← state strategy
│       ├── MASTER-STATUS.md                        ← Layer 3 (top-of-folder, you read this)
│       ├── decision-log.md                         ← Layer 5 (forever record)
│       ├── cross-session-dependencies.md           ← Layer 2a (kanban)
│       ├── session-state/                          ← Layer 2b
│       │   ├── SESSION-1-STATE.md
│       │   ├── SESSION-2-STATE.md
│       │   ├── SESSION-3-STATE.md
│       │   ├── SESSION-4-STATE.md
│       │   └── SESSION-5-STATE.md
│       ├── session-logs/                           ← Layer 1 (diary)
│       │   ├── SESSION-1-LOG.md
│       │   ├── SESSION-2-LOG.md
│       │   ├── SESSION-3-LOG.md
│       │   ├── SESSION-4-LOG.md
│       │   └── SESSION-5-LOG.md
│       ├── daily-reports/                          ← Layer 4
│       │   └── YYYY-MM-DD.md (per day)
│       ├── templates/
│       │   ├── SESSION-N-STATE-TEMPLATE.md
│       │   ├── SESSION-N-LOG-TEMPLATE.md
│       │   ├── MASTER-STATUS-TEMPLATE.md
│       │   └── DAILY-REPORT-TEMPLATE.md
│       └── interface-contracts/                    ← created at warm-up
│           ├── api-contract.md
│           ├── internal-rpc-contracts.md
│           └── db-schema-ownership.md
│
├── .github/workflows/
│   ├── pr-codex-review.yml         ← triggers Codex on PR
│   ├── lint-naming-and-comments.yml ← B1, B5, B7 enforcement
│   ├── lint-no-hardcoded-ips.yml   ← C6 enforcement
│   ├── lint-scope-violations.yml   ← session can't touch out-of-scope files
│   └── lint-state-hygiene.yml      ← was SESSION-N-LOG updated for this PR?
│
├── .claude/
│   ├── hooks/
│   │   └── post-tool-use.sh        ← auto-appends to log on git commit
│   └── scripts/
│       ├── regenerate-master-status.sh ← runs every 15 min via launchd
│       ├── codex-review.py             ← called by pr-codex-review action
│       └── post-codex-review.py        ← posts comments back to PR
│
└── (service folders, owned per session table above)
```

## Constraints this plan adds (proposed for CONSTRAINTS.md)

- **I8**: Five parallel Claude Code sessions sharded by ownership scope; coordinator session holds cross-cutting context
- **I9**: Auto-mode allows only the operations enumerated in `02-AUTO-MODE-GUARDRAILS.md`
- **I10**: Codex independent review on every PR; CI-triggered via GitHub Actions; coordinator + Rishi approve merges

These are added below in this same plan-doc commit.

## When this plan is invalid

If any of the following happen, STOP and re-plan:

- A session keeps producing PRs that fail Codex review (>3 in a row) — pattern problem, not code problem
- Two sessions end up needing to edit the same file (scope leak)
- Rishi can't keep up with PR-merge approvals (becomes the bottleneck)
- A session goes silent for >24h (Auto-mode hung; investigate)
- Codex review consistently disagrees with Claude on substantive design (conflict resolution needed)

## Next steps

Tomorrow morning, when Rishi is ready:
1. Read `01-SESSION-SHARDING-AND-OWNERSHIP.md` — confirm shard plan
2. Read `02-AUTO-MODE-GUARDRAILS.md` — confirm forbidden list
3. Read `03-CODEX-REVIEW-WORKFLOW.md` — set up the GitHub Action + Codex API key
4. Read `05-GETTING-STARTED-TOMORROW.md` — literal session-launch ritual
5. Type "build" in coordinator session — I add the I8/I9/I10 rows to CONSTRAINTS, write `interface-contracts/`, create the GitHub Actions workflows
6. Open Session 1, paste startup prompt, type "build" there, watch it go
7. Open Sessions 2-5 over the day as Session 1 unblocks them
