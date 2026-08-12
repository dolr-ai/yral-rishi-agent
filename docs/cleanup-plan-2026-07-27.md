# Cleanup plan — live-production edition

**Created:** 2026-07-27
**Service:** `yral-rishi-agent` @ agent.rishi.yral.com (LIVE, serving mobile)
**Author:** Session 6 (backend + orchestration)
**Status:** awaiting Rishi approval

---

## Part 0 — How we work

Read this part even if you skip the rest. It's the process, and the process is
what keeps the service up.

### 0.1 The one fact that shapes everything

**On this repo, merge == deploy.**

`deploy.yml` fires automatically after CI succeeds on `main`. There is no
staging environment. There is no "merged but not shipped" state. The moment a
PR merges, a rolling restart begins on rishi-4 and rishi-5.

> Note: `DEPLOY.md`'s FAQ says auto-deploy is off and that docs-only PRs skip
> the deploy. Both statements are stale — `ci.yml` has no path filter, so every
> merge builds and every build deploys. Fixing that doc is PR 1 of this plan.

Everything below follows from this. We cannot "test in staging." Our substitutes
are: **small PRs, dark deploys, flag flips, and fast rollback.**

### 0.2 The six rules

**Rule 1 — One concern per PR, and name the blast radius.**
Every PR description opens with: *what breaks if this is wrong, and who notices.*
If the answer is "nothing at runtime," say so and prove it. If the answer is
"every chat message," the PR gets a flag (Rule 3).

**Rule 2 — Never change structure and behavior in the same PR.**
A refactor PR must be a provable no-op. A behavior PR must not move code around.
When both are needed: refactor first (no-op), then change behavior (tiny diff).
This is the single highest-value habit in the whole plan. When something breaks
after a mixed PR, you cannot tell which half did it.

**Rule 3 — Risky changes ship dark, then flip.**
Add the new code path behind an env flag that defaults to *current behavior*.
Deploy it. Nothing changes. Watch it. Then flip the flag.

The flag flip is a `docker service update --env-add` — **not a code deploy.**
That matters enormously:
- it takes ~30 seconds instead of a full CI+build+roll cycle
- it is reversed by one command, with no git history involved
- it does not interact with the migration runner or the image at all

Deploys are the risky operation. Flag flips are cheap. Push risk out of deploys
and into flips wherever possible.

**Rule 4 — Baseline before, verify after.**
Before each risky PR, capture the numbers (Part 3 has the exact commands). After
the deploy, capture them again. "It looks fine" is not a verification. A p95 that
went from 2.8s to 4.1s is not fine, and you will not see it by eye.

**Rule 5 — Reversibility ordering.**
We do the perfectly-reversible work first — not because it's most important, but
because it builds the muscle and proves the pipeline while the stakes are low.
Severity and order are different questions. The auth fix is the most *severe*
finding and comes near the *end*, because by then we'll have the tooling to
detect it going wrong.

**Rule 6 — One PR in flight at a time.**
The deploy workflow has a concurrency lock, so two merges queue rather than
collide. But *we* should not have two unverified changes in production at once —
if something breaks, we need to know which one did it. Merge, verify, then open
the next.

### 0.3 The single best safety property of this plan

**Zero schema migrations.**

I checked every item: archive deletion, worker split, test infrastructure,
wire-format consolidation, advisory locks, auth verification, repository
hygiene. None of them need a migration.

That removes the one class of change that `docker service update --rollback`
cannot undo. Rolling back an image is 30 seconds. Rolling back a schema is a
pg_dump restore and an outage. For this entire cleanup, rollback is always cheap.

If any PR in this plan starts to need a migration, that's a signal we've drifted
out of scope. Stop and re-scope.

### 0.4 How you'll learn this

Each PR in Part 3 has a **"Concept"** block. Here's the loop per PR:

1. **I explain the concept first** — before any code. The idea, why it matters
   here, and what the alternative approaches were. Ask questions at this stage;
   it's free.
2. **I write the diff** and walk you through it line by line, in plain English.
3. **You review.** Push back. If you can't explain what a line does, that's a
   defect in my explanation, not in your understanding — make me redo it.
4. **We ship** and watch the verification together.
5. **I write down what we learned** in DAILY-LOG.md, including anything that
   surprised us.

The concepts, in the order they appear:

| PR | Concept you'll learn |
|---|---|
| 1–3 | Build context vs. image contents; why deleting 89K lines can be provably zero-risk |
| 4 | What a smoke test actually protects you from, and what it doesn't |
| 5–6 | Test doubles vs. integration tests; why 1,364 mock tests missed a real bug |
| 7 | Process model: what `--workers 4` really does to your program |
| 8–11 | Dark deploys, flag flips, leader election, advisory locks |
| 12–13 | Wire contracts; why three copies of one function is a latent outage |
| 14–15 | JWT signature verification, JWKS, key rotation |
| 16–18 | Layering rules and how to make CI enforce them so they don't rot |

---

## Part 1 — Ground truth (verified, not assumed)

Everything below I confirmed by reading the code and the workflows, not by
inference. Where PROGRESS.md independently confirms a finding, I've noted it.

### 1.1 Runtime topology

```
mobile → Caddy (rishi-1/2) → swarm service `yral-rishi-agent`
                              ├── replica 1 on rishi-4  → uvicorn --workers 4 → 4 processes
                              └── replica 2 on rishi-5  → uvicorn --workers 4 → 4 processes
                                                                    ↓
                                                       Patroni (rishi-4/5/6)
```

**8 independent Python processes serve this service.** Each one runs the full
`lifespan()` in `main.py`, which starts **16 background tasks**.

> Independent confirmation: `PROGRESS.md:358` records "8 subscribers active
> (4 workers × 2 replicas)". This is not a theory.

**16 × 8 = 128 concurrent background loops in production right now.**

### 1.2 The loop inventory — and the trap

This table is the most important thing in this document. Two of the sixteen
tasks are **not** background jobs; they are per-process infrastructure. Moving
them to a worker would silently break WebSocket push and LLM routing.

| # | Task | Kind | Move to worker? |
|---|---|---|---|
| 1 | `websocket_manager.start_redis_subscriber` | **per-process infra** — receives Redis pub/sub and delivers to *this process's* WS connections | ❌ **NEVER** |
| 2 | `llm_routing_pubsub.start_subscriber` | **per-process infra** — invalidates *this process's* in-memory registry cache | ❌ **NEVER** |
| 3 | `_trending_stats_refresher` | job — matview refresh, 15 min | ✅ |
| 4 | `_engagement_loop` | job — proactive + skill check-in + nudge (**calls the LLM**) | ✅ |
| 5 | `_takeover_timeout_sweep` | job — 5s sweep, broadcasts via Redis | ✅ |
| 6 | `consolidation_loop` | job | ✅ |
| 7 | `scoring_loop` | job (**LLM**) | ✅ |
| 8 | `streak_loop` | job — already has `FOR UPDATE SKIP LOCKED` | ✅ |
| 9 | `etl_loop` | job | ✅ |
| 10 | `integrity_loop` | job | ✅ |
| 11 | `digest_loop` | job | ✅ |
| 12 | `video_ideas_loop` | job (**LLM**) | ✅ |
| 13 | `cost_alerts_loop` | job | ✅ |
| 14 | `classification_loop` | job (**LLM**), default OFF | ✅ |
| 15 | `feed_ranker_loop` | job | ✅ |
| 16 | `collage_pregen_loop` | job (**spends money**), default OFF | ✅ |

Why #5 is safe to move even though it pushes to users: `websocket_manager._publish`
writes to the Redis `ws_events` channel, and every API process's subscriber (#1)
delivers to its own connections. A worker can broadcast without holding any
WebSocket. **This only works because #1 stays in the API processes.**

One honest caveat: if Redis is down, `_publish` falls back to local-only
delivery. In a worker with zero WS connections, that fallback drops the event
silently — whereas today it would at least reach users on that process. Redis
Sentinel makes this rare, and the alternative (128 loops) is worse. Noted in the
risk register.

### 1.3 What 128 loops actually costs

- `_trending_stats_refresher`: `REFRESH MATERIALIZED VIEW CONCURRENTLY` fired 8×
  every 15 min. The code comment claims "Postgres row-level locking handles the
  race" — that is **not correct** for a matview refresh. `CONCURRENTLY` takes an
  ExclusiveLock against other refreshes, so the 8 calls *serialize*: eight full
  rebuilds back to back, 96 times a day.
- `_takeover_timeout_sweep`: every 5s × 8 = **1.6 table scans per second**, forever.
- `_engagement_loop`: 8 processes each pull 20 idle conversations and call the
  LLM. The dedup is a code comment ("Postgres row-level locking prevents
  duplicates"), not a lock. **This is the amplifier behind the 2026-05-30 Gemini
  burn.** The kill switch stopped the bleeding; the 8× multiplier is untouched.
- DB connections: 8 × `max_size=10` = up to **80 connections** before watchdog and ETL.

### 1.4 Deploy machinery (good — we build on it)

- Auto-deploy on merge; 3 swarm managers tried in order.
- Migrations applied **before** the image roll, with auto pg_dump → S3 and a WAL
  restore point.
- `--update-order start-first --update-failure-action pause`.
- `/health` polled 2 min → **auto-rollback** on failure.
- Post-deploy smoke on 7 **public** endpoints → auto-rollback on failure.
- `:stable` tag = last image that passed health.

**The gap:** the smoke test covers only unauthenticated endpoints. Chat send —
the actual product — is not covered by any automated post-deploy check. Closing
that is PR 4, and it is the prerequisite for everything risky.

**The other gap:** `deploy.yml` and `rollback.yml` both operate on exactly one
service name. The moment a worker service exists, they must know about it or it
silently pins to a stale image forever. Handled in PR 9.

---

## Part 2 — The waves

18 PRs, seven waves. Ordered by reversibility, not by severity.

| Wave | PRs | Theme | Runtime risk | Est. |
|---|---|---|---|---|
| **0** | 1–3 | Dead weight removal | **none** (proved) | 1 day |
| **1** | 4–6 | Build the safety net | none (CI only) | 3 days |
| **2** | 7–11 | The worker split | **high** — flag-gated | 4 days |
| **3** | 12–13 | Wire contract consolidation | medium | 2 days |
| **4** | 14–15 | Auth hardening | **high** — flag-gated | 3 days |
| **5** | 16–17 | Layering enforcement | low | 2 days |
| **6** | 18 | Docs + memory reconciliation | none | half day |

At 3–4 hrs/day: **~3 weeks.** Waves 0–1 are cheap and unblock everything; if you
only ever do those, the repo is already much healthier.

**Gate between every wave:** 24h soak with no new Sentry issue classes, no p95
regression, no cost anomaly. Waves 2 and 4 get **48h**.

---

## Part 3 — PR-by-PR

Each PR: **Goal / Blast radius / Reversibility / Concept / Verify.**

---

### Wave 0 — Dead weight

#### PR 1 — Fix the stale deploy docs
**Goal.** `DEPLOY.md`'s FAQ contradicts its own opening section (claims manual
deploy only, claims docs-only PRs skip deploy). Correct both.
**Blast radius.** None. Markdown.
**Reversibility.** Trivial.
**Concept.** *Docs that lie are worse than no docs.* You will make a decision
under pressure based on that FAQ one day. We fix it first so the rest of this
plan rests on an accurate description of the pipeline.
**Verify.** Read it back and confirm it matches what we observed in §1.4.

#### PR 2 — Delete `archive/`
**Goal.** Remove 461 files / 89,275 lines of abandoned microservice skeletons,
including 12 `.bak` files. Untouched since 2026-06-04.
**Blast radius.** **Provably none.** The Dockerfile does:
```dockerfile
COPY --chown=appuser:appuser app/ .
COPY --chown=appuser:appuser infra/ ./infra/
```
`archive/` never enters the image. The application layers will be byte-identical.
**Reversibility.** It's in git history forever; `git revert` restores it. We record
the pre-delete SHA in DAILY-LOG.md.
**Concept — build context vs. image contents.** Docker sends the *whole directory*
to the daemon as build context, then the `COPY` lines choose what lands in the
image. So `archive/` has been slowing every build for two months while
contributing nothing to the running service. This is why "is it in the repo" and
"is it in production" are different questions — and why this delete is safe in a
way that deleting something from `app/` never would be.
**Verify.** Before merge: `docker build` locally both ways, compare the image
digest of the app layer. After merge: deploy goes green; `/health` 200.

#### PR 3 — Root tidy
**Goal.** Move `eval-results-2026-05-29.json` (72 KB) into `docs/`. Add a short
`migrations/README.md` recording that **037, 044, and 049 are intentionally
absent** (or, if they aren't, that's a finding — we investigate before writing).
**Blast radius.** None. `migrations/` is read by the runner, which tracks applied
files in `schema_migrations`; adding a README is inert.
**Reversibility.** Trivial.
**Concept.** *Gaps in a numbered sequence are ambiguous by default.* Six months
from now nobody will remember whether 044 was skipped or lost. One sentence now
saves an hour of archaeology later.
**Verify.** Migration runner no-ops on next deploy (it already tracks state).

---

### Wave 1 — Safety net

> This wave adds **zero** runtime code. It is entirely CI and observability. It is
> also the wave that makes Wave 2 survivable.

#### PR 4 — Authenticated smoke test
**Goal.** Extend `post-deploy-smoke.yml` to exercise the real product path:
create conversation → send message → assert an assistant reply → SSE stream
yields tokens → delete conversation. Against a dedicated test user and a test bot.
**Blast radius.** None to the service; it only *reads* prod after deploy. It does
create and delete one conversation per deploy.
**Reversibility.** Delete the job.
**Concept — what a smoke test is for.** Today `/health` returns 200 if the DB is
reachable. That tells you the process booted. It does not tell you chat works.
The gap between "process is up" and "product works" is where every interesting
outage lives. A smoke test is the automated version of you opening the app after
a deploy — and it's wired to auto-rollback, so it acts even when you're asleep.
**Design note.** The test JWT goes in a GitHub secret against a throwaway
principal. **This must be a real token from the real issuer, not a hand-minted
unsigned one** — otherwise PR 14 (signature verification) breaks the smoke test
at exactly the moment we most need it working.
**Verify.** Deliberately break a route on a branch, confirm the smoke job goes
red and dispatches rollback.

#### PR 5 — `pyproject.toml` + `conftest.py`
**Goal.** Add `[tool.pytest.ini_options] pythonpath = ["app"]` and a root
`conftest.py`. Delete the hand-rolled `sys.path` blocks from **71 of 130** test
files.
**Blast radius.** CI only.
**Reversibility.** Trivial.
**Concept — import roots.** There's no `conftest.py` today, so each test file
patches `sys.path` itself to find `config`, `main`, etc. That's 71 copies of the
same workaround, each able to drift. One config line replaces all of them.
**Verify.** `pytest tests/` passes locally and in CI with identical test counts —
1,364 before, 1,364 after. Any change in count means we broke collection.

#### PR 6 — Real Postgres in CI + first integration tests
**Goal.** Add a `postgres:16` service to the CI test job, apply `migrations/*.sql`
against it, and write ~15 integration tests that use a real connection: chat
send, chat stream, access control (all four `_can_access_conversation` paths),
collage persistence, ETL drain.
**Blast radius.** CI only. Makes CI slower (~2 min) and much more truthful.
**Reversibility.** Revert the workflow change.
**Concept — test doubles vs. integration tests.** You have 1,364 tests and *zero*
touch a database. `test_request_images.py` says so in its own docstring: "a small
SQL-substring stub pool covers the four queries." A stub that matches on SQL
substrings can never catch a type error at the driver layer.

Which is exactly what happened. `chat.py:531` documents the bug:
> *"PR #456's tests passed a real `date` object through the mocked pool and never
> exercised the real asyncpg codec."*

The test asserted the mock behaved like the mock. Production disagreed, and
Sarvesh found it. **Fifteen tests against a real Postgres are worth more than the
1,364 you have** — and they're what let us refactor aggressively in Waves 2–5.
**Verify.** Reintroduce the `collage_date` string bug on a branch; confirm the new
suite catches it.

---

### Wave 2 — The worker split

> The highest-value and highest-risk wave. Five PRs plus two ops steps. Read the
> whole wave before starting any of it.

#### PR 7 — Observability first: `/health/loops`
**Goal.** Each loop registers its name and last-tick timestamp in a module-level
dict; a new `GET /health/loops` (JWT-gated) reports them plus the process PID.
**No behavior change** — pure instrumentation.
**Blast radius.** Low. New endpoint + a dict write per tick.
**Reversibility.** Revert.
**Concept — measure before you change.** Your own rule: *verify the wire before
reading the code.* I've told you 128 loops are running. Before we act on that,
let's see it. After this deploys, hitting `/health/loops` repeatedly will return
different PIDs (Caddy round-robins across 8 processes), each claiming to run all
16 loops. That's the diagnosis, empirically, from outside the box.
**Verify.** `for i in $(seq 20); do curl -s .../health/loops | jq .pid; done | sort -u`
→ expect up to 8 distinct PIDs, each reporting 16 live loops.

#### PR 8 — The `RUN_BACKGROUND_LOOPS` flag (dark)
**Goal.** Gate the 14 movable loops behind `RUN_BACKGROUND_LOOPS`, **defaulting
to `true`**. The two per-process infra tasks (§1.2 rows 1–2) are *not* gated —
they always run.
**Blast radius.** None on deploy: the default preserves today's behavior exactly.
**Reversibility.** It *is* the reversibility mechanism.
**Concept — the dark deploy.** We ship the ability to change behavior without
changing behavior. The code goes to production and does nothing new. It bakes.
Then, separately, we flip a switch. If the flip is wrong we flip it back in 30
seconds — no CI, no build, no image roll. Splitting "deploy new code" from
"change behavior" into two independently reversible events is the core technique
for changing live systems, and it's what makes the rest of this wave safe.
**Verify.** `/health/loops` unchanged post-deploy: 8 PIDs, 16 loops each.

#### PR 9 — Worker entrypoint + deploy machinery
**Goal.** Three things:
1. `app/worker.py` — an entrypoint that runs *only* the 14 movable loops, no
   uvicorn, no HTTP. Plus a liveness file or a tiny health port for the swarm.
2. `deploy.yml` — after updating `yral-rishi-agent`, **also** update
   `yral-rishi-agent-worker` **if the service exists**. Must be a no-op today.
3. `rollback.yml` — same, symmetric.
**Blast radius.** None. `worker.py` is dead code in the image (nothing runs it).
The workflow changes no-op because the service doesn't exist yet.
**Reversibility.** Revert.
**Concept — why the workflow change comes *before* the service exists.** If we
create the worker service first, the very next merge updates only the API and
leaves the worker pinned to an old image — silently, forever, until someone
notices the ETL is running month-old code. Making the deploy workflow
service-aware *first*, in an idempotent way, closes that window before it opens.
Sequencing like this is most of what "careful" means in practice.
**Verify.** Deploy green. Confirm the workflow log prints "worker service not
present, skipping" and the API rolled normally.

#### OPS A — Create the worker service (not a PR)
Run by hand from a swarm manager, at the exact SHA currently serving:
```bash
docker service create \
  --name yral-rishi-agent-worker \
  --replicas 1 \
  --env RUN_BACKGROUND_LOOPS=true \
  --network <same as app> \
  --secret database_url \
  <same env + secrets as the app service> \
  --entrypoint "python -m worker" \
  ghcr.io/dolr-ai/yral-rishi-agent:<current-sha>
```
**During this window loops run 9× instead of 8×.** That is a deliberate, brief
overlap — strictly safer than a gap, where nothing would run. Verify the worker
is healthy and ticking before proceeding.
**Reversibility.** `docker service rm yral-rishi-agent-worker`. Back to 8×.

#### OPS B — Flip the API flag (not a PR)
```bash
docker service update --env-add RUN_BACKGROUND_LOOPS=false yral-rishi-agent
```
**This is the moment 128 loops become 14.**
**Reversibility.** `--env-add RUN_BACKGROUND_LOOPS=true`. ~30 seconds, no deploy.

**Watch for 60 minutes, then 48 hours:**
| Signal | Expected | Abort if |
|---|---|---|
| `/health/loops` on API | 0 movable loops, WS+pubsub still present | any movable loop still ticking |
| `/health/loops` on worker | all 14 ticking | any loop stalled > 2 intervals |
| DB connections | ~80 → ~50 | climbing |
| p50 / p95 chat send | flat or better | p95 worse by >10% |
| Gemini spend/hr (`llm_costs`) | **down ~8×** on background processes | flat (flag didn't take) or up |
| WebSocket delivery | typing indicators + push still work | any drop |
| Proactive messages | still sending, ~1/8 the volume | zero over 2 cycles |
| Sentry | no new issue classes | any new class |

The Gemini-spend row is the proof the whole exercise worked. `llm_costs` already
tracks per-process spend, so you can query it directly.

#### PR 10 — Advisory locks (defense in depth)
**Goal.** Wrap each movable loop's tick in `pg_try_advisory_lock(hashtext('loop:<name>'))`.
If the lock isn't acquired, skip the tick.
**Blast radius.** Low, and it's a *safety* change: with 1 worker it's always
acquired and nothing changes.
**Reversibility.** Revert.
**Concept — belt and braces.** OPS B made loops single-instance *by configuration*.
Configuration drifts: someone scales the worker to 2, or a swarm reschedule
briefly runs two tasks during a node failure. An advisory lock makes correctness
a property of the *code*, not of a deployment setting somebody must remember.
Note this also finally makes true the comment that's been wrong in `main.py` for
months — "Postgres row-level locking prevents duplicates" — by actually taking a lock.
**Verify.** Temporarily scale worker to 2; confirm exactly one instance ticks;
scale back to 1.

#### PR 11 — Right-size the pool and tidy shutdown
**Goal.** Now that the API doesn't run jobs, reduce `max_size` in `database.py`
for the API path. Replace `main.py`'s 80-line copy-pasted shutdown block (15
identical `try/await/except CancelledError`) with a list and a loop.
**Blast radius.** Low. Pool sizing is measurable; shutdown refactor is a no-op.
**Reversibility.** Revert.
**Concept — Rule 2 in action.** These are two changes: one behavioral (pool size),
one structural (shutdown loop). They're together only because both are small and
both are gated by the same precondition. If you'd rather split them, split them —
that instinct is correct and I'll follow it.
**Verify.** Connection count from `pg_stat_activity`; graceful shutdown observed
in logs during the roll.

---

### Wave 3 — Wire contract

#### PR 12 — `app/wire.py` (pure no-op)
**Goal.** One `format_message()` and one `format_conversation()`. Replace the
three divergent `_format_message` copies (`chat.py:154`, `human_chat.py:19`,
`creator_coach.py:25`) and two `_format_dt` copies.
**Blast radius.** **Medium-high — this touches every chat response mobile parses.**
**Reversibility.** Revert. But a wire regression that mobile mis-renders is
user-visible immediately, so this needs the PR 6 integration tests in place.
**Concept — the danger of near-duplicate functions.** Three copies that are 90%
identical is worse than either one copy or three clearly different ones, because
everyone assumes they match. One already has a comment begging future readers to
keep them in sync — that comment is an admission the design is wrong. Consolidate
carefully: diff the three, decide field by field whether a difference is
intentional (H2H really does need different fields) or accidental drift.
**Verify.** Golden-file test: capture the exact JSON from all three endpoints
before the change, assert byte-identical output after. This is the discipline
that makes a "provable no-op" actually provable.

#### PR 13 — Pydantic on the chat routes
**Goal.** Convert `POST /messages` and `POST /messages/stream` from `body: dict`
to typed request models; add `response_model=`. Two routes only — the other 12
`body: dict` handlers follow later, one per PR.
**Blast radius.** Medium. Pydantic validation *rejects* payloads the dict version
silently ignored. A field mobile sends that we don't model → 422.
**Reversibility.** Revert.
**Concept — strict vs. permissive parsing, and why it bites here.** `body.get("x")`
accepts anything. A Pydantic model rejects unknown or mistyped fields by default.
That's what we want long-term — it's how "the mobile contract is sacred" becomes
something CI can check instead of something we hope. But flipping from permissive
to strict against a live client is exactly how you 422 an app in the field. So we
configure the model to **ignore** unknown fields initially, log them for a week,
and only then tighten. Same dark-deploy shape as Wave 2.
**Verify.** Log-and-allow for 7 days; review what unknown fields actually arrive;
then tighten in a follow-up.

---

### Wave 4 — Auth

> Most severe finding. Deliberately late: it needs PR 4's smoke test and PR 6's
> integration tests to be safe to attempt.

#### PR 14 — JWKS verification, dark
**Goal.** Fetch and cache the JWKS from the issuer. Verify RS256. Gate on
`JWT_VERIFY_SIGNATURE`, **default `false`**. When false, verify anyway but only
*log* the outcome — never reject.
**Blast radius.** None while the flag is false. One extra HTTP fetch on cold
cache.
**Reversibility.** Flag.
**Concept — why this is #1 in severity.** `auth.py:24` sets
`verify_signature: False`. The only checks are that `iss` is in a known list and
`sub` is non-empty. Anyone can forge `{"iss":"https://auth.yral.com","sub":"<any
principal>","exp":<future>}` signed with a key they made up, and read, write, or
delete any user's conversations. It also satisfies `_can_access_conversation` as
a bot or parent creator, so creator earnings and takeover are reachable too.

The comment says this matches the production Rust service. I believe that. It's
an argument for fixing both, not for keeping it.

**Concept — why it's #14 in order.** Get this wrong and *every user is logged out
at once*. It is the single most dangerous change in this plan. So we never guess:
we run the verification in shadow mode and read a week of real data. If 0.0% of
real tokens fail, flipping is boring. If 3% fail, we've just learned something
critical that we'd otherwise have learned from Sentry at 2 a.m.
**Verify.** After 7 days: query the log for verify-failure rate by issuer. Target
0.00%.

#### PR 15 — Flip verification on; delete `/debug/whoami`
**Goal.** Default `JWT_VERIFY_SIGNATURE=true`. Remove `main.py:541`
`/api/v1/debug/whoami`, which decodes JWTs without verification and echoes the
full payload — it has said "Remove before cutover" since it was written.
**Blast radius.** **Highest in the plan.** Gated by PR 14's shadow data.
**Reversibility.** `--env-add JWT_VERIFY_SIGNATURE=false`, ~30 seconds.
**Concept — flipping on evidence, not courage.** By this point the decision is
arithmetic: we have a week of real numbers. That's the difference between a
change that's *risky* and one that's merely *important*.
**Verify.** Watch 401 rate for 60 min against the pre-flip baseline. Any rise
above the shadow-mode prediction → flip back immediately, investigate, retry.

---

### Wave 5 — Keep it clean

#### PR 16 — Lint rule: no raw SQL outside `repositories/`
**Goal.** CI check that fails on `pool.fetch|execute|fetchval|fetchrow` outside
`app/repositories/`. Start with the current violations **allowlisted** — 12 route
files and 18 service files — so CI is green on day one.
**Blast radius.** CI only.
**Concept — ratchets.** You don't fix 30 files at once on a live service; you stop
the number from growing and shrink it opportunistically. Every time we touch one
of those files for another reason, we remove its allowlist entry. The rule holds
the line while the cleanup happens gradually. This is also how the layering rule
in CLAUDE.md stops being aspirational — right now it's enforced by memory, and
memory has a 60% compliance rate here.
**Verify.** Add a violation on a branch; confirm CI fails.

#### PR 17 — Rename phase-coded tests
**Goal.** `test_21g_P34_M2c_feed_ranker.py` → `test_feed_ranker.py`, and the rest.
Pure `git mv`.
**Blast radius.** CI only.
**Concept — names are for the next reader.** `21γ.P34.M2c` was meaningful the week
it was written. Today it's a lookup into a 743-line PROGRESS.md. The feature name
is stable; the phase code isn't.
**Verify.** Test count unchanged: 1,364.

---

### Wave 6 — Close the loop

#### PR 18 — Docs, PROGRESS, DAILY-LOG, memory
**Goal.** Per CLAUDE.md: update **both** PROGRESS.md and DAILY-LOG.md. Prune the
rollout-narrative comments from `config.py` and `main.py` (keep every
bug-prevention comment — `_parse_collage_date`, `_fetch_audio_bytes_and_mime`,
`spawn()`). Write the new architecture into CLAUDE.md's reading order. Save
memories for what we learned.
**Blast radius.** None.
**Concept — comments as changelog.** 7.7% of `app/` is comments and 53 files carry
date-stamped narrative. Individually most are good. Collectively, `config.py`
spends ~60 lines of prose on ~15 constants. A comment that stops the next person
reintroducing a bug earns its place forever. A comment explaining why we flipped
a default on 2026-07-15 is history — and history belongs in git and DAILY-LOG.md.

---

## Part 4 — Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Worker split breaks WS push (moved `start_redis_subscriber` by mistake) | low | high | §1.2 table is explicit; PR 7 `/health/loops` shows exactly what runs where |
| R2 | Worker service goes stale — deploy only updates the API | **medium** | high | PR 9 lands the workflow change **before** OPS A creates the service |
| R3 | Rollback rolls the API but not the worker → version skew | medium | medium | PR 9 makes `rollback.yml` symmetric; both services always move together |
| R4 | A loop silently stops after the flip; nobody notices for days | medium | medium | `/health/loops` + a watchdog alert on stale tick timestamps |
| R5 | Redis down + worker broadcasting = silently dropped WS events | low | medium | Accepted (see §1.2). Redis Sentinel covers it. Revisit if it ever bites |
| R6 | Pydantic 422s live mobile clients | medium | high | PR 13 ships ignore-unknown + log for 7 days before tightening |
| R7 | JWKS flip logs out every user | low (after shadow) | **critical** | 7 days of shadow data; 30-second env-flip rollback |
| R8 | Issuer publishes no usable JWKS | low | high | Discovered in PR 14 *shadow mode*, before any flip. If so → escalate; don't flip |
| R9 | `wire.py` changes a field mobile depends on | medium | high | Golden-file byte-comparison tests before/after |
| R10 | Two unverified changes in prod at once, can't attribute a break | medium | high | Rule 6: one PR in flight; wave gates |
| R11 | Plan drifts and someone needs a migration | low | high | Any migration = stop and re-scope (§0.3) |
| R12 | Deleting `archive/` removes something actually imported | very low | high | Verified: Dockerfile only COPYs `app/` + `infra/`. Also grep for imports pre-merge |

---

## Part 5 — What we do NOT touch

Explicitly out of scope. These have been through fire and work:

- **`llm_registry.py`** — the process-routing table, provider capabilities,
  per-provider semaphores, DB overrides, pub/sub invalidation. Best-designed part
  of the codebase.
- **The kill-switch design** — two-tier gates, default-OFF for spend loops. We
  *add* to it (the `RUN_BACKGROUND_LOOPS` flag is a sibling), never subtract.
- **The error-code contract** — `{code, message, retryable}` and the decision not
  to persist fallback text as a real assistant message.
- **The collage pipeline** — hybrid LoRA anchor + downstream model, fallback
  window, blur variants. Recently tuned; leave it.
- **`mobile-docs-archive/`** — exists for a documented reason (archive before
  Sarvesh review).
- **Migrations, ETL correctness, Patroni, WAL-G, chat-ai on rishi-1/2/3.**

---

## Part 6 — Abort criteria

Stop the wave, roll back, and reassess if **any** of these occur:

1. `/health` fails post-deploy → already automatic (auto-rollback fires).
2. p95 on chat send regresses >10% vs. the wave's baseline.
3. Any **new** Sentry issue class appears within 2h of a deploy.
4. LLM spend/hour rises above the pre-wave baseline at any point.
5. Any background loop is stalled >2 of its own intervals.
6. Mobile reports any user-visible regression — Rishi's call overrides all metrics.
7. Two things break in the same day → **stop the whole plan**, spend a session
   understanding why before continuing.

Rollback commands, in escalating order:
```bash
# 1. Behavior flip (30s, no deploy) — Wave 2 and 4 changes
docker service update --env-add RUN_BACKGROUND_LOOPS=true  yral-rishi-agent
docker service update --env-add JWT_VERIFY_SIGNATURE=false yral-rishi-agent

# 2. Image rollback (~2 min) — GitHub Actions → "Rollback production"
#    or, from a manager:
docker service update --rollback yral-rishi-agent

# 3. Pin to last known good
docker service update --image ghcr.io/dolr-ai/yral-rishi-agent:stable yral-rishi-agent

# 4. Remove the worker entirely, restore old topology
docker service rm yral-rishi-agent-worker
docker service update --env-add RUN_BACKGROUND_LOOPS=true yral-rishi-agent
```

---

## Part 7 — Baseline capture (before Wave 2)

Run these and paste the output into DAILY-LOG.md before OPS B. Without a
baseline, "it seems fine" is the best verification we can ever do.

```bash
# Processes and loops (after PR 7)
for i in $(seq 30); do curl -s -H "Authorization: Bearer $TOKEN" \
  https://agent.rishi.yral.com/health/loops | jq -r .pid; done | sort -u | wc -l

# DB connections
psql -c "SELECT count(*), state FROM pg_stat_activity
         WHERE datname='yral_agent_db' GROUP BY state;"

# LLM spend per process, last 24h — the number that proves the split worked
psql -c "SELECT process, count(*), round(sum(cost_usd)::numeric,4)
         FROM llm_costs WHERE created_at > now() - interval '24 hours'
         GROUP BY process ORDER BY 3 DESC;"

# Chat latency p50/p95 — from Sentry, last 24h, txn = POST /api/v1/chat/.../messages
```

---

## Part 8 — Approval

Per CLAUDE.md, nothing merges without explicit approval. What I need from you:

1. **Approve the plan shape** — waves and ordering, especially auth at 14–15
   rather than first.
2. **Confirm the worker split is wanted.** It's the biggest change here. The
   alternative — leave 128 loops and just tune the kill switches — is a legitimate
   choice with less risk and much less upside. Your call.
3. **Confirm I can create a test principal** for PR 4's authenticated smoke test.
4. **Confirm the ops steps** (OPS A / OPS B) are mine to run via SSH, or whether
   you want to run them while I watch.

Then I open PR 1 and we go one at a time.

---

# Appendix A — Baseline audit findings (2026-07-27, 5-way read-only pass)

Independent 5-agent audit (structure/risk, symmetry, de-bloat, tests, docs). It
validates this plan wave-for-wave and supplies the concrete targets below.
Pre-cleanup baseline: `git` HEAD `e0e07d9`. `archive/` is **not** referenced in
the Dockerfile (verified) — deleting it leaves the image byte-identical.

## Sacred surfaces (must stay behavior-identical — do not touch outputs)
- `app/models.py` DTOs — esp. `is_active: str` (not bool), the collage triple
  (`collage_id`/`collage_bot_id`/`collage_date`, emitted only when non-null),
  `AssistantError.code` Literal `BLOCKED_CONTENT|TRANSIENT|NO_PROVIDER`.
- SSE event shapes in `chat.py:912-1185` — event names `token`/`done`/`error` + keys.
- `auth.py` no-signature JWT decode + `EXPECTED_ISSUERS`; 401/410/503 error envelopes.
- `migrations/` (append-only, pg_dump first).

## Wave 0 — dead weight
- Delete `archive/` (not in Dockerfile; 158 `.py` files, untouched since 2026-06-04).
- Move `eval-results-2026-05-29.json` (72 KB, repo root) → `docs/`.
- `migrations/README.md`: record that **037, 044, 049 are absent** (verify intentional).
- `DEPLOY.md` FAQ is stale: it claims manual-deploy-only + docs-only-skip-deploy;
  both wrong (merge == deploy, proven live by #469).

## Wave 1 — safety net (the linchpin; current tests are refactor-hostile)
- No `conftest.py` / no Postgres in CI; ~99/130 test files are source-inspection
  (assert string literals) → false-positive red on any rename, catch no behavior.
- Add `pyproject.toml [tool.pytest.ini_options] pythonpath=["app"]` + root
  `conftest.py`; delete `sys.path` blocks from **71** files (test count must stay 1,364).
- Add `postgres:16` to `ci.yml`; port the working testcontainers template at
  `archive/yral-rishi-agent-*/tests/conftest.py` before deleting archive/ (copy it out).
- Characterization gaps to cover FIRST (highest risk): `chat.send_message_stream`
  (`chat.py:917`), `chat.send_message` (`chat.py:557`), `models.py` DTO JSON snapshots,
  `message_repo`/`conversation_repo` SQL, `llm_registry` routing decisions,
  `moderation`/`content_safety` gating, `amorae_auth.require_amorae_secret`.
- Cleanup-candidate tests (delete/convert): the 44 pure-inspection files — worst:
  `test_phase_25_10_dead_code_removed.py`, `..._latent_gaps.py`, the `test_21ab_*`
  CI-YAML pins, `test_deploy_*`/`test_walg_*`/`test_watchdog_*` workflow pins,
  `test_rate_limiter.py` (source-grep). Triage BEFORE refactoring so renames don't red-storm.

## Wave 2 — worker split
- ~16 background loops spawn in `main.py` lifespan and run in **every** uvicorn
  worker (`--workers 4`). Do the `/health/loops` instrumentation first (PR 7).
- **Do NOT move** `websocket_manager.start_redis_subscriber` and
  `llm_routing_pubsub.start_subscriber` — per-process infra (WS push + routing-cache).

## Wave 3 — wire contract
- `_format_message` duplicated ×3: `chat.py:154`, `human_chat.py:19`, `creator_coach.py:25`
  (they've drifted: token_count handling, lazy-vs-eager `storage` import, collage fields).
- `_format_dt` ×2: `chat_v2.py:50`, `chat_v3.py:16`; ad-hoc datetime `isoformat` in `earnings.py`.
- Gate any consolidation behind golden-JSON snapshots of all three endpoints first.

## Wave 4 — auth (5 implementations → 1 dependency)
- `_require_admin_key` (`admin_classification.py:37`, `admin_discovery.py:41`) — X-Admin-Key→403.
- Inline copies of that check: `influencers.py:409-415`, `:439-445`.
- `_check_auth_flexible` (`admin_dashboard.py:33-58`) — JWT or `?token=` → 401.
- `_check_admin_auth` fake-Request hack (`backup_health_admin.py:48-62`) + a copy in `llm_routing_admin.py`.
- Decide one semantics (shared-key-403 vs JWT-401), then route all through one dependency.
- `auth.py` signature verification is PR 14 (ship dark, flip).

## Wave 5 — layering
- SQL embedded in route handlers (move to repos, per table group): `earnings.py:22,52,94,110`
  (+ **no `creator_earnings` repo exists** — whole table group unlayered), `chat_v3.py:31,64`,
  `creator.py:23,75,127,144,269,430`, `human_chat.py:78,108,138,159,217`,
  `creator_coach.py:222,336,608,666,764`, `creator_takeover.py:157,169`, `admin_discovery.py`,
  `soul_file.py:329`, `llm_routing_admin.py:78,113,279`.
- Finish the half-done `redis_config.get_redis_url()` migration — delete inline
  `os.environ` Redis blocks in `cost_breaker.py:158`, `discovery_feed.py:107`,
  `feed_ranker.py:129`, `spicy_handoff.py:79` (they already import redis_config).
- Standardize router paths on `prefix=` (10 no-prefix files); add a shared 503 wrapper + serializer.

## Wave 6 — docs + memory
- **PROGRESS.md** stale (header "2026-06-13", no July shipments) — refresh through 07-27.
- **DAILY-LOG.md** stale (newest 06-26, out of chronological order) — add July, fix order,
  consider rolling H1 into an archive file.
- `chat.py` — the reading-order finish line — has **no module docstring**; `send_message`
  (`:557`) and `send_message_stream` (`:917`) have none. Add one paragraph each.
- CLAUDE.md reading order → add step 6 (chat_v2/v3, "v1=AI, v2=bot-aware, v3=unified inbox").
- GLOSSARY.md: define `nsfw`/`spicy` (same concept, pick one canonical term), `amorae`,
  `collage`, `LoRA`, `skill`, `wizard`, `nudge`, `proactive`, `coach`.
- Prune/relocate stale one-shot design docs into a `docs/` archive with a README index.
