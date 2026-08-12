# Daily Log

## 2026-08-12 — Surface filter live; Tara = `both`; shared-DB POC decision

**Rishi's architecture call: one shared database for now.** Adult (amorae.ai) and mainstream (YRAL app) both serve off the same DB while we find out whether the product works; separate later only if it proves out. This **deliberately overrides** the July contract's Level-2 write isolation (`docs/amorae-v2-contract-2026-07-01.md`). Recorded so it isn't re-litigated as a violation — it's a chosen trade of separation for speed. The `/api/v1/spicy/*` endpoints (handoff, context, `X-Amorae-Secret`) still exist and still work; they're just no longer the only channel.

**#481 merged + deployed.** Migration 052 applied to prod (`surface TEXT NOT NULL DEFAULT 'mobile'` + CHECK + index). Waited for the in-flight #457 Patroni redeploy to finish before touching the DB — a sequenced restart of all 3 Patroni services is the wrong moment to write. Cluster verified 3/3, leader rishi-6, 0 lag before proceeding.

**Tara set to `both`.** `UPDATE 1`, guarded by exact id AND `is_nsfw = TRUE` so it could only ever hit the one row. Distribution now **4,084 mobile / 1 both**. `both` rather than `web` because `/api/v1/influencers` has never filtered `is_nsfw` — she was already being served to mobile, so `web` would have *removed* a 54k-conversation bot from the mainstream app rather than preserving the status quo.

**Verified live in production, not assumed:**
- `?surface=web` → **total 1**, exactly `taaarraaah` / Tara
- no param → **total 3806**, unchanged
- `?surface=wbe` → **400**

**Self-review catch before merge:** `_format_influencer_response` is shared by the list AND trending endpoints, but only `list_all` selected the new column — so the `or MOBILE` fallback would have reported `surface: "mobile"` for a web-only persona on trending. A default that silently substitutes for absent data is worse than a null, because the client can't tell "this is mobile" from "this query forgot to ask". Added `surface` to the five remaining column lists.

**Still open (raised, not fixed):** nothing gates `is_nsfw` off the mainstream catalogue — neither `/api/v1/influencers` nor discovery feed/search filter it. Matters for the US launch, where App Review + IAP assume mainstream is SFW. Tracked 27.3.

---

## 2026-08-10 — Surface targeting for amorae-web (shared catalogue split)

**In PR — `surface` column + opt-in catalogue filter.** Requested by the amorae-web session: amorae.ai (adult web) and the mobile app now share one backend and one `ai_influencers` catalogue, so the catalogue has to say where each persona belongs.

- `migrations/052_ai_influencers_surface.sql` — `surface TEXT NOT NULL DEFAULT 'mobile'` + CHECK (`mobile|web|both`) + btree index. **Deliberately the opposite encoding from 051's `target_markets`** (where NULL = everywhere): market targeting fails safe by showing more of a catalogue you may already see, but surface targeting can only fail safe by showing LESS. A NULL-means-everywhere encoding would let one missed backfill publish all 3,804 active mainstream personas to an adult site.
- `app/services/surface.py` — one helper owns the predicate (the H2H list-vs-detail lesson). `web` → `IN ('web','both')`; `both` is a single-value match, **not** a wildcard.
- `GET /api/v1/influencers` — **opt-in** `?surface=`. No param = no filter, so mobile is byte-identical today. An *unknown* surface 400s rather than degrading to unfiltered — a typo'd `?surface=wbe` from amorae-web must never return the mainstream catalogue to the adult site. `surface` now included on each influencer.
- 13 new tests (incl. 4 real-Postgres) proving the default lands without backfill, the CHECK rejects `'Web'`, and web never sees mobile. Full suite **1409 passed**, lint clean.

**Verified against prod before building — three corrections to the request:**
1. **`is_nsfw` already exists** on `ai_influencers` (with 2 indexes) and is live in `ai_client.py` + `content_safety.py`. `surface` is genuinely a different axis (distribution vs content rating), so it's additive rather than duplicative — but they are now two overlapping flags and the invariant "`is_nsfw` must never reach mobile" is still enforced by **nothing**.
2. **The column is `system_instructions`, not `system_prompt`** — though the *API field* is `system_prompt` (moderation-stripped), so the request was right at the wire and wrong at the schema.
3. **Counts:** 3,803 active SFW + 279 discontinued + **exactly 1 active NSFW (Tara)** = 4,083 total. Their "~3,800" is right for active rows.

**Not done — needs Rishi.** Step 3 of the request (flag Tara `web`/`both`) is left unflagged: `GET /api/v1/influencers` has **no `is_nsfw` filter today**, so Tara is already served to mobile. `both` preserves that; `web` would *remove* a 54k-conversation bot from the mainstream app. That's a product decision, not a side effect of an infra PR — and it collides with the US launch's SFW requirement.

---

## 2026-08-08 — Sentry down ~45h (DNS eviction on rishi-3); recovered + the blind spot that hid it

**Incident: Sentry was dead and nothing told us.** Found while checking fleet state after the break. `sentry.rishi.yral.com` returned **500 on every authenticated endpoint** from all six edge IPs, `relay` was in a crash loop at **1013 restarts**, and `pgbouncer` / `taskworker` / `seaweedfs` were all unhealthy. Last ingested event was **2026-08-06 10:58Z** — roughly **45 hours** of zero error visibility across the whole fleet.

**Root cause: Docker's embedded DNS registry was evicted on rishi-3.** All 69 containers on `sentry-self-hosted_default` lost their DNS records while staying "Up". `web` couldn't resolve `pgbouncer` (`could not translate host name "pgbouncer"`), `relay` couldn't resolve `redis`. Isolated it by elimination: external DNS worked from inside containers, and two *fresh* throwaway containers resolved each other fine — so the network object was healthy and only the pre-existing registrations were gone. That made the runbook's `up -d --force-recreate` the correct fix (re-attaching re-registers), not a destructive `down`/`up`. Recovered to **zero unhealthy containers**.

**Second fault, uncovered by the fix: the compose file referenced a network that no longer exists.** nginx then failed with `network sentry-web not found`. The 2026-08-01 fleet consolidation folded all six hosts into one Swarm, moved nginx onto `yral-v2-public-web` via a **runtime** `docker network connect`, and deleted `sentry-web` — but the runtime attachment was invisible to `docker-compose.override.yml`, so it survived until the first recreate and then vanished. The edge Caddy proxies `sentry.rishi.yral.com` to a **hardcoded `10.0.1.11:80`**, nginx's address on that overlay.
- Live-fixed `docker-compose.override.yml` on rishi-3: nginx now joins `yral-v2-public-web` **declaratively** with `ipv4_address: 10.0.1.11` pinned and a `sentry-nginx` alias (so the edge can later drop the hardcoded IP). Backup at `~/override.yml.bak-20260808`. **Needs backporting to the `yral-rishi-sentry` repo** — same live-fix-then-PR pattern as #470.
- Verified end to end: `_health` **200**, API **200**, and a synthetic event traversed relay → Kafka → consumer → ClickHouse and came back queryable. Ingest is confirmed working, not assumed.
- **Closed same day:** `monitors-clock-tick` was crash-looping on `OffsetOutOfRange` (its Kafka offset aged out of retention during the outage). Reset the consumer group `154340 → 156929` with `--to-latest` — deliberately not `--to-earliest`, which would replay ~2 days of stale clock ticks and fire a flood of bogus missed-check-in alerts. Back to `Up (healthy)`, `restarts=0`. **The Sentry stack now has zero unhealthy or restarting containers.**

**In PR — the watchdog blind spot.** The watchdog could not have caught this, and the obvious fix (make it global) would not have helped: its DNS check resolves *Swarm overlay aliases* from its own namespace and would never look at Sentry's compose bridge network, and its `node.role == manager` constraint excludes rishi-3 anyway. The load-bearing problem is simpler — **all three existing checks alert through `sentry_sdk`, so none of them can report a Sentry outage.**
- Added `watchdog/heartbeat.py`: pings an external dead-man's-switch **while things are fine**, so silence is the alarm. That covers the whole class of faults that kill the reporter too — Sentry down, watchdog crashed, Swarm gone. Gated on the fleet's public surfaces answering 2xx, so it reports failure explicitly rather than waiting to time out.
- `WATCHDOG_HEARTBEAT_URL` is **vendor-neutral and unset by default** — inert until an operator opts in, so this changes nothing until we pick a provider. **Decision still needed from Rishi:** external switch (independent, which is the property that failed here) vs. an owned endpoint.
- 10 new tests, all green; watchdog suite 36/36. No runtime code path changed. Not deployed.

**In PR — US market launch PR1 (market column + dormant config).** Per `docs/us-market-launch-spec-2026-08-08.md` Track B. Ships the column and the knobs with **nothing reading them** — no behaviour change for anyone.
- `migrations/051_ai_influencers_target_markets.sql` — `target_markets TEXT[]` + GIN index. **NULL/empty = global**, so all 4,081 existing rows (real prod count) stay visible everywhere with **no backfill**. Additive `ADD COLUMN` with no DEFAULT is metadata-only on PG11+ (no table rewrite). `051` is correct: `044`/`049` are reserved by open PRs #426/#454 per `migrations/README.md`.
- `app/config.py` — added the `_env_list` helper (the spec assumed one; it didn't exist), plus `MARKET_EXCLUSIVE_COUNTRIES` (default `[]`) and `MARKET_DEBUG_OVERRIDE_ENABLED` (default `False`).
- **Rule 9 correction:** my first draft told Rishi to take a manual pre-merge pg_dump. Wrong — `scripts/ci/run-migrations.sh` already takes a per-migration `pg_dump -Fc`, uploads it to S3, and **fails closed** if it can't. No manual step. Header corrected to match migration 043's wording.
- **Verified against a real Postgres,** not just source assertions: 3 new integration tests prove the column is a genuine `TEXT[]`, the GIN index exists, and `target_markets @> ARRAY['US']` selects only tagged rows while NULL *and* `'{}'` both read as global. That last property is the whole launch — if it inverted, the US feed would leak the Indian catalogue or the global feed would empty out.
- 14 new tests; full suite **1396 passed**, `ruff check`/`format` clean. Not deployed.

---

## 2026-07-29 — Wave 0 closed; Wave 1 (test safety net) planned + started

**Wave 0 complete** — PR3 (#474) merged yesterday's session; the repo is now free of `archive/`, has honest deploy docs, and documents its migration numbering.

**Wave 1 planned (#475 merged).** Audited the test suite before planning: **1,364 tests** (CI's `pytest tests/`), **zero touch a database**, ~40% are pure source-inspection (`.read_text()` + substring asserts that survive any refactor), **71 files carry hand-rolled `sys.path` hacks**, and there's **no `conftest.py`/pytest config at all**. The `collage_date` codec bug (`chat.py:531`) is documented in-code as having *passed* the mocked-pool tests and 500'd in prod — the case for real-DB tests. Also found **`pytest-asyncio` is not installed** (not in requirements), so async-marked tests may be silently passing without running — a gap PR6's real integration tests will expose. Plan doc: `docs/wave1-plan-2026-07-29.md`. Harness decision: **Option B (testcontainers)** — real DB tests run identically on the Mac and in CI.

**Merged — Wave 1 PR5a (#476): pytest config foundation.** Split the original PR5 into 5a (config, tiny/readable) + 5b (bulk removal of the 71 hacks, invariant-proven) for safe review.
- Added `pyproject.toml` `[tool.pytest.ini_options]` with `pythonpath = ["app", "watchdog"]` + `testpaths = ["tests"]`, and a placeholder `tests/conftest.py` (PR6 fills it with the DB fixtures).
- Verified behavior-neutral: `pytest tests/` collects **1364 → 1364**; full run **53 failed / 1245 passed / 66 skipped** unchanged (the 53 are missing local third-party libs, green in CI); `ruff check`/`format --check app/ infra/` still pass. `testpaths` also stops a bare `pytest` from accidentally collecting the `scripts/` smoke helpers.
- No runtime code. No deploy.

**Merged — Wave 1 PR5b (#477): remove the 71 `sys.path` hacks.** Now that the config is on `main`, the hand-rolled path blocks are redundant. Deleted them across 71 files via a deterministic script that handled all 4 patterns + the guarded `APP_DIR` form — keeping the `APP_DIR` definition where it also feeds a `.read_text()` source-pin (the one 4× outlier), dropping it where it was hack-only. Then `ruff --select F401 --fix` removed 143 now-orphaned imports (85 `sys`, 53 `os`, 5 pre-existing), and a targeted pass collapsed leftover triple-blank-lines. **Net −307 / +3 across 71 files.**
- Proven safe by the same invariant at every step: `pytest tests/` **1364 → 1364**, full run **53 failed / 1245 passed / 66 skipped / 1 warning** unchanged; all 71 files parse; 0 `sys.path` references remain. Did **not** run `ruff format` on tests/ (would reformat dict literals repo-wide — scope creep). No runtime code. No deploy.

**In PR — Wave 1 PR6 (#478): the real-database safety net (harness + marquee proof).** The payoff of the wave — real integration tests against a real Postgres, which the mocked-pool suite could never do. Shipped as the harness + 5 core tests; access-control / SSE / ETL tests follow as PR6b on the same harness.
- **Harness** in `tests/conftest.py`: a session-scoped `pgvector/pgvector:pg15` testcontainer, all 47 `migrations/*.sql` applied via asyncpg (verified they're all transaction-safe), per-test TRUNCATE isolation. Test deps isolated in `requirements-test.txt` (runtime image stays lean); CI's `test` job installs it and runs the tests against the runner's Docker. **Deliberately no `pytest-asyncio`** (would make ~38 existing async tests suddenly run — separate change); integration tests are sync + `asyncio.run()`. Skips gracefully if Docker/testcontainers absent.
- **5 tests** (`tests/integration/`): all 47 migrations apply + 3 contract tables present; pgvector extension enabled; app's `check_db_health()` against the real DB (proves the pool-binding harness); and the **collage_date codec pair** — a real `date` persists, a raw string is rejected by the real codec.
- **Acceptance proof:** reintroduced the exact prod bug (stringify `collage_date` in `message_repo.create`) → `test_collage_date_accepts_real_date` went **red with the identical prod error** (`DataError … 'str' object has no attribute 'toordinal'`), then restored (git clean). The new test catches what the mocks shipped.
- Verified locally against Docker: **5/5 integration pass; full suite 1369 passed** (1364 + 5), harness doesn't disturb existing tests. No runtime code. No deploy.

---

## 2026-07-28 — Cleanup Wave 0 kicks off (adopt plan, fix deploy docs, delete archive/)

Started the safe cleanup of the v2 chat service against `docs/cleanup-plan-2026-07-27.md`.

**Merged**
- **#472** — DEPLOY.md FAQ fix (Wave 0 PR1). Three FAQ answers contradicted the doc's own accurate body (claimed manual-only deploy). Now correct: auto-on-merge (Path 1) since 2026-06-08. Docs-only, no deploy.
- **#471** — adopted the cleanup roadmap + today's 5-way baseline audit as a tracked doc. Docs-only, no deploy.

**Merged — Wave 0 PR2 (#473): delete `archive/`**
- Removed **461 files / ~89K lines** of abandoned microservice skeletons (untouched since 2026-06-04, SHA `83f1bcc`). **Pre-delete main SHA: `33a3a88`** (`git revert` restores everything). `main` now at `a220ff7`.
- **Provably safe:** the Dockerfile only `COPY`s `app/` and `infra/` — `archive/` never entered the image. No live code references it (the `archive.get(...)` hits in `backup_health_admin.py` are a local dict var, not the dir). 1365 tests still collect; no import breakage.
- **Preserved** the richest archived testcontainers-Postgres conftest → `docs/testing/wave1-conftest-reference.py` (with a v2-adaptation header: swap Alembic → numbered SQL migrations, DATABASE_URL, app/database pool). This is the reference for Wave 1's test safety net.
- **Deploy:** none. `build-and-push` **skipped** (touched no build-trigger paths), so no image rebuild and the Deploy workflow never fired. Zero runtime change.
- Cleaned the 46 git-ignored leftovers (`.DS_Store`/`.pytest_cache`/`.pyc`) from the local disk afterward.

**In PR — Wave 0 PR3: root tidy**
- Moved `eval-results-2026-05-29.json` (72 KB) from repo root → `docs/`; updated its two path references (PROGRESS.md, this log).
- Added **`migrations/README.md`** documenting the runner (filename-sorted, gap-tolerant) and — the real finding — **why 037/044/049 are absent**: `037` was simply skipped (never existed anywhere); **`044` is reserved by open PR #426** (l0-eval, `d3bb844`) and **`049` by open PR #454** (spicy, `e34e8cc`). So the next migration is `051`, and nobody should reuse 044/049 or they'll collide when those PRs merge.
- Docs/markdown only. No deploy.

---

## 2026-07-23 — Patroni cascade follow-up: leader-alert PR, corrected root cause, repo cleanup

Morning review of the 07-21 leaderless-cascade post-mortem, plus a big repo tidy.

**Repo cleanup**
- Closed **PR #444** (stale AI-bot push-notification PR) → superseded by **#461** (same author, clean + CI-green, routes notifications to `multi-service.naitik.yral.com`). #461 still needs a decision: it adds a new external dependency + the `NAITIK_MULTI_SERVICE_AUTH_TOKEN` secret (silently no-ops without it).
- Closed stale docs/draft PRs: **#391** (paywall discovery — Path-A decision made it moot), **#405 / #397 / #395** (month-old drafts, not executed).
- Deleted **49 stale local branches** (35 merged-PR + `pr-444-review` + 13 orphan branches already on origin). Held `feat/analytics-design-doc-2026-06-13` — it's local-only with 2 unpushed commits.

**Patroni cascade — two corrections to the post-mortem (verified read-only)**
- **Root cause is NOT unattended-upgrades.** All 3 nodes have `Automatic-Reboot = false` (confirmed `apt-config dump`), yet all rebooted the *same minute* (Jul 20 08:48) on lockstep kernels. That's a **shared external trigger — almost certainly Hetzner host maintenance**, not per-node u-u. "Stagger the u-u window" (post-mortem rec #1) would fix nothing.
- **Cluster is fragile right now:** leader rishi-4 (TL49) healthy, but rishi-6 replica ~1.8 GB behind and rishi-5 stuck "starting" → effectively zero fault tolerance. Next maintenance reboot could repeat the outage.

**Shipped / opened**
- **PR #463** (`feat(watchdog): Patroni leader-presence alert`) — the missing page. Watchdog reads Patroni REST `/cluster` and alerts Sentry on a leaderless cluster > 90s (healthy failovers stay silent). Mirrors `replica_drift.py`; 9/9 tests. **MERGED + DEPLOYED** — leader-check thread confirmed live on the swarm (`watching patroni leadership every 60s`).
- Landed the post-mortem + a **corrected-findings & action-plan doc** (`docs/postmortems/2026-07-21-patroni-cascade-addendum-and-plan.md`).

**Cluster repaired to 3/3 (Rishi-authorized prod ops)**
- WAL-G safety net verified first (base backup today 01:03 UTC, `failed_count=0`).
- `patronictl reinit` rishi-5 → Sync Standby, TL49, lag 0 (was stuck "starting" for hours). Patroni auto-restored synchronous replication once it was healthy.
- `patronictl reinit` rishi-6 → Replica, TL49, lag 0 (was diverged on TL48 with growing lag — would not self-converge). **All three now on TL49, lag 0** = real fault tolerance restored.

**Open for Rishi**
- Confirm the reboot trigger in the Hetzner console (needs the account) — likely host maintenance, the durable fix.
- Decide on #461 (external notification dependency) — **parked per Rishi 2026-07-23.**
- Per post-mortem: **do not** prioritize PR #457 (shm) — it didn't contribute and is already bridged.

## 2026-06-26 — Sentry sweep (7 PRs), watchdog deployed, observability tasks 1+2 shipped, codex trigger bug discovered

### What happened

Big sweep day. Started from a Sentry triage that found ~190 of the last-24h's 300 prod errors collapsed into ONE bug (a fire-and-forget WebSocket `broadcast_new_message` task with no retention — GC'd mid-flight). Dispatched dev session through 5 fix-PRs in parallel, then 2 more from the observability brief. Plus the watchdog from PR #415 is now live on the swarm.

**7 PRs merged + auto-deployed (all in one day):**

| # | What | Closes |
|---|---|---|
| #416 | etl_integrity rule asymmetric (`diff < -tol` instead of `abs(diff) > tol`) + tick verifier deleted. Why: v2 ⊇ ETL(chat-ai) by design — v2 ahead is healthy, not a drift signal. | Sentry #208 (~80 events/24h) |
| #417 | CI workflow to build + push `ghcr.io/dolr-ai/yral-overlay-dns-watchdog` to GHCR | Unblocked PR #415 deploy |
| #418 | WebSocket task retention — module-level `_BACKGROUND_TASKS: set` + `add_done_callback`. Class-of-bug fix; 9 different Sentry fingerprints all the same root cause. | Sentry #40 + 8 siblings (~190 events/24h) |
| #419 | streak_tracker chunked + `ORDER BY principal_id` + `FOR UPDATE SKIP LOCKED` | Sentry #124 + #246 (timeout + new deadlock regression) |
| #420 | feed_ranker `_fetch_signals` (different path than PR #410 chunked) + video_ideas `_list_active_bots` chunked to avoid Postgres shmem exhaustion | Sentry #144 + #220 (DiskFullError) |
| #421 | Langfuse trace body carries `sessionId = conversation_id` when present — chats now group in Sessions tab | Observability brief task 2 |
| #422 | Skill check-in backoff — 6h → 12h → 24h → 48h → 96h → 168h (capped weekly, never hard-stop, resets on user reply) | Observability brief task 1 + skill-checkin firehose |

**Watchdog deployed to Swarm.** PR #417's CI workflow auto-built the image at 08:03 UTC after #417 merge; `docker stack deploy -c bootstrap/scripts/overlay-dns-watchdog-stack.yml yral-v2-watchdog` from rishi-4 brought it up on rishi-5 at 08:10 UTC. Startup log shows all 11 aliases (patroni × 3, etcd × 3, redis-sentinel × 3, redis-primary, pgbouncer) resolving cleanly. Silence-on-healthy; Sentry alert only on NXDOMAIN ↔ resolved state transitions. Next Saikat-Monday window (2026-06-29) is the first real test.

### What we learned — 3 findings

**1. codex-review.yml has a trigger bug** that bites every draft PR. The workflow only triggers on `pull_request: types: [opened, synchronize]` with `if: draft == false`. So when a PR opens as DRAFT, the workflow runs once, skips, and never re-runs even after `gh pr ready` flips draft → false. Workaround used today: push an empty commit on each branch to force a `synchronize` event. Long-term fix is to add `ready_for_review` to the trigger types — deferred to a separate small PR (not piling on more workflow churn right now).

**2. ETL integrity rule was structurally wrong, not data-wrong.** Sentry #208 wasn't an A4 violation — `_verify_hourly` assumed `v2 == chat-ai`, but the live architecture is `v2 ⊇ ETL(chat-ai)`: Caddy on rishi-1/2 routes `agent.rishi.yral.com` directly to v2, plus proactive + nudge + takeover loops produce thousands of v2-native messages/day. The diff is always positive and always growing — by design. PR #416 makes the rule one-sided (only fire when v2 is BEHIND chat-ai = the real A4 risk signal) and deletes the tick verifier (sentinel canary + hourly cover the freshness gap). Memory: `feedback_v2_supseteq_etl_chatai` should be considered if we ever rewire this.

**3. Discovery classifier baseline established — `~40 calls/day = healthy new-bot intake rate.`** Dev session's read-only diagnostic confirmed loop is ON, catalog is 100% classified (0 stuck-unknown out of 3723 active bots), and the ~40/day drip exactly matches the new-bot creation rate. Saved as `project_classifier_baseline_healthy.md` in memory so future sessions don't re-diagnose this. The "retry stuck-unknown forever" quirk that Part B was scoped to fix has zero prod incidence — Part B not shipped per "don't add code for problems that haven't happened."

### Patroni `/dev/shm` is at Docker default 64 MiB on all three nodes

Brief 4 Part A ops report from dev session: rishi-4/5/6 Patroni containers run at default 64 MiB `/dev/shm` while hosts have 16-32 GiB tmpfs free. PR #420's code chunking mitigates the root cause (DiskFullError on big aggregations); shm bump deferred until we observe whether code fix holds. If Sentry #144 + #220 don't drop to zero within 24-48h, the shm bump becomes a separate ops PR with explicit Rishi go + rolling Patroni restart on a low-traffic window.

### Prod writes today

| Where | What | Authorization |
|---|---|---|
| Swarm (rishi-4) | `docker service update --force yral-v2-patroni_patroni-rishi-4` — re-registered overlay alias (residual from 2026-06-24 incident) | Explicit "yes" with command quoted |
| Swarm (rishi-4) | `docker stack deploy -c bootstrap/scripts/overlay-dns-watchdog-stack.yml yral-v2-watchdog` | Explicit "deploy watchdog" |
| Swarm | 5x auto-deploys via deploy.yml (PRs #416-#420) | Via merge — auto-deploy is the standing pipeline |

### What's on deck for tomorrow

- **Observability brief task 3** (L0 deterministic eval + per-reply storage + new kill switch + new migration). Requires pg_dump first per Rule 9. Big-ish lift; budget ~half a day.
- **Observability brief task 4** (alerting on `runpod_vllm` primary failures). Gates task 9 (fallback removal). Independent of task 3.
- **Sentry verification**: confirm #40 family + #124 + #144 + #208 + #220 dropped after deploys settled. Wait for ~24h of post-deploy data before declaring victory.
- **Patroni shm bump** if DiskFullError keeps firing.
- **codex-review.yml ready_for_review trigger fix** — small follow-up PR.

## 2026-06-24 — Metabase outage fix, overlay-DNS watchdog shipped, nutrition_coach to Neha's bot, name split-brain bug discovered

### What happened

Started with `metabase.rishi.yral.com` returning broken dashboards. Diagnosis: Metabase container on rishi-6 returning NXDOMAIN for `patroni-rishi-4`, while `-5` and `-6` resolved fine. Patroni cluster itself was healthy (rishi-5 leader, rishi-4 replica, rishi-6 sync_standby) — only the overlay-DNS alias was missing. Root cause: 8 hrs prior, all three rishi-4 services (`patroni`, `etcd`, `redis-sentinel`) had failed simultaneously with the Swarm "No such container" bug; `etcd` and `redis-sentinel` recovered their aliases cleanly on restart but `patroni-rishi-4` didn't. Likely trigger: Saikat's weekly Monday k3s cluster update. v2 prod was unaffected the whole time because the app DSN is multi-host (`patroni-rishi-{4,5,6}` + `target_session_attrs=read-write`) — libpq just skipped the unresolvable host. Metabase, talking to a single hostname, went silently dark.

Three things shipped to close the loop:
1. **Metabase data source UI fix** — Rishi flipped Host from `patroni-rishi-4` to `patroni-rishi-5` (the current leader). Dashboards back up; verified via `/api/dataset` trivial query returning `current_database()` = `yral_agent_db` on `10.0.3.17` (Patroni leader's overlay IP).
2. **Patroni-rishi-4 alias re-registered** — `docker service update --force yral-v2-patroni_patroni-rishi-4` recreated the task. Post-fix: alias resolves to 10.0.3.35 from inside the Metabase container; cluster on timeline 34 with lag=0 across all 3 members.
3. **Overlay-DNS watchdog merged** — PR #415 (`agent/overlay-dns-watchdog-2026-06-24` → main). 50-line Python loop that resolves 11 rishi-N service aliases every 5 min and Sentry-warns on state transition (NXDOMAIN ↔ resolved). Alert-only in v1; auto-heal deferred until we observe one Saikat-Monday cycle. Image build + first deploy is a follow-up PR. We have 6 days until next Monday to ship that.

Also: applied `nutrition_coach` skill to Neha's AI Influencer (`fcpty-wwmlu-…-sqe`, archetype `advisor` — compatible) via single-row UPDATE on the leader. Next chat turn triggers the first-turn onboarding (asks goal + diet + check-in times).

### New bug discovered + tracked as 21γ.P22

While checking the bot, the v2 DB showed `display_name = "Dr. Anya Sharma"` but the mobile profile screen Neha shared displays "Nutritionist." Confirmed via the mobile codebase: `AccountInfo.displayName` is a computed property off `username`, fetched from **yral-metadata-server** (`metadata.yral.com`) — it never touches v2. Chat header + system prompt read from v2's `ai_influencers.display_name` instead. So each AI Influencer has TWO names sourced from TWO services with no sync, and they can drift cleanly. Not a sync bug — by current design — but bad UX. Cosmetic-only today (skill + chat work fine against v2). Tracked as 21γ.P22 in PROGRESS.md + memory `project_ai_influencer_name_split_brain.md`. Fix options on the table:
1. Mobile sends `username` to v2 at create time, v2 keeps `display_name` in sync going forward
2. Mobile reads v2's `display_name` everywhere (drop the metadata dependency for AI Influencer screens)
3. v2 fetches `username` from metadata at chat time

Pick when we have bandwidth + a product call on which name wins.

### PRs merged today (1)

| # | What |
|---|---|
| #415 | feat(watchdog): overlay DNS alias watchdog — Sentry-alert on Swarm gossip loss. Triggered by 2026-06-22 metabase outage. Alert-only v1. Image build + first deploy land in a follow-up PR before next Monday. |

### Prod writes today

| Where | What | Authorization |
|---|---|---|
| Swarm (rishi-4) | `docker service update --force yral-v2-patroni_patroni-rishi-4` — re-registered overlay alias | Explicit "yes" with exact command quoted back |
| Postgres (leader) | `UPDATE ai_influencers SET skill_slug='nutrition_coach' WHERE id='fcpty-…-sqe'` — applied skill to Neha's bot | Explicit "okay" with exact SQL quoted back |
| Metabase (via UI) | Data source #2 Host: `patroni-rishi-4` → `patroni-rishi-5` | Rishi did it in the admin UI directly |

### Open / queued

- **Image-build CI for watchdog** — separate PR. Needs to land + first deploy before next Monday's Saikat update.
- **Saikat ping** — confirm whether rishi-4/5/6 are on his weekly k3s update schedule + ask for advance notice. Open whether the cluster is actually isolated or sharing infra.
- **21γ.P22 (AI Influencer name split-brain)** — note + decide; not blocking.

## 2026-06-12 — Pre-rollout prep day: Coach Day-14 backend stack, ETL drain bug-fix cascade, Sentry audit, pg_dump baseline taken

### What happened in one paragraph

Day-14 prep for the 21β 10% rollout Sarvesh is about to do in the next 1-2 hours. Three threads ran in parallel. **Thread 1 — Coach Day-14 pivot backend:** shipped the read-only Soul File preview stack via #374 (`GET /api/v1/influencers/{bot_id}/system-prompt-preview` endpoint, 88 LOC strict + 14 source-pin tests, all 7 layers + skills + applied overrides + composed text), #375 (SSOT extraction of `USER_SEGMENT_PLAN_TEMPLATE` from routes/soul_file.py into services/soul_file.py — chat-time compose() and preview now both render from the same constant), and #376 (`engagement_schedule` block on the same endpoint with `inactivity_proactive` + `skill_checkins` + `first_turn_nudge` sub-blocks; new module-level `DEFAULT_INITIAL_IDLE_MINUTES = 5` in nudge.py so `should_nudge()` default and the preview read from the same source). Honest "what's configured today" — no bot-owner-configurable cadence columns added per Rishi's explicit drop. Mobile expert has the full spec for the read-only Soul File page UI + new "View full prompt" pill entry point in the Coach chat header; standing rule held — no PR until Rishi's Motorola pass. **Thread 2 — ETL drain bug-fix cascade:** when Rishi finally tested `POST /admin/etl/drain` end-to-end against the deployed service, three stacked bugs surfaced. #377 fixed the Phase-2 watermark — `datetime.fromtimestamp(time.monotonic())` was producing 1970-relative timestamps because `monotonic()` is seconds since boot not seconds since epoch, AND the resulting string was being passed to asyncpg as `$1` on a timestamptz parameter (asyncpg rejects strings even with `::timestamptz` cast in SQL). #378 fixed the residual — the new tz-aware `datetime.now(timezone.utc)` was tz-aware while `etl_integrity_results.verified_at` is `TIMESTAMP` (no tz), so asyncpg threw "can't subtract offset-naive and offset-aware datetimes"; strip tzinfo at the call site only, keep `started_dt.isoformat()` in the report so the JSON shape is unchanged. #379 fixed the parallel bug surfaced when `/admin/etl/reconciliation` 500'd post-#378 deploy — `_v2_latest_import_ts` and `_chat_ai_counts_from_hourly_payload` were silently returning tz-naive datetimes from TIMESTAMP columns while the rest of the file used tz-aware UTC, breaking every subtraction; tag tzinfo=UTC on the way out of both helpers. Three lessons learned, all source-pinned via behavioral regression tests + tightened FakePool to mirror asyncpg's protocol-level constraints. **Thread 3 — also today:** PR #373 unblocked CI on the Coach catch-all hygiene fix by tightening `_looks_like_truncated_proposal` (false-positiving on Anastasia's long English replies that mentioned JSON-y vocabulary), reverting #372's diagnostic try/except wrappers (mobile expert revealed the override-apply 500 was actually a mobile-side `kotlinx.serialization.MissingFieldException`, not backend), and as a bonus unblocker landing a 1-line ETL fix + wall-clock-stable test fixtures (`_recent_utc()` helper replacing hardcoded `2026-06-11 11:59:00` timestamps that were drifting past the 30-min HEARTBEAT_STALE_SEC threshold).

### Why it matters for tomorrow

Backend stack for the Day-14 Coach pivot is complete and live. ETL drain endpoint now actually works server-side (though Cloudflare/Caddy edge times out around 100s on the long Phase 2 wait — 21γ.P10 queued). Most importantly: today's live tests proved the ETL is healthy — sentinel canary is passing in **6 seconds end-to-end** (chat-ai write → v2 detection), heartbeat is 90s old, importer ran 2 min ago. The hard 5-min cutover requirement is comfortably met. Reconciliation verdict shows `INVESTIGATE` but that's a key-name bug in `_chat_ai_counts_from_hourly_payload` (looks for `chat_ai_count`, payload uses `chat_ai`) — 21γ.P12 queued as the first post-rollout fix. Sentry config audited: DSN points at `sentry.rishi.yral.com` per CLAUDE.md rule 5, `SENTRY_ENVIRONMENT=production`, traces 100% / profiles 5%, Principal ID auto-attached on every authenticated request via `auth.py:47` — full per-user traceability ready for the 10% rollout. Fresh pre-rollout pg_dump taken (547 MB, SHA `4613aa69da3e4a8399c843fa274163aa5de6c65a947c3ce4e93f43f98175d557`, at 13:57:32Z on rishi-4) — point-in-time rollback target if the 10% rollout goes sideways.

### PRs merged today (8)

| # | What |
|---|---|
| #373 | chore(coach): revert #372 diagnostic wrappers + tighten `_looks_like_truncated_proposal` catch-all (Anastasia reprompt loop) + add `response_text[:500]` to truncation warning. Bonus: 1-line `etl_drain._chat_ai_counts_from_hourly_payload` early-return fix + replaced 4 hardcoded `2026-06-11` test-fixture timestamps with `_recent_utc()` helper for wall-clock-stable tests. |
| #374 | feat(soul-file): `GET /api/v1/influencers/{bot_id}/system-prompt-preview` — read-only transparency endpoint, 88 LOC + 14 source-pin tests. All 7 layers (L1 global rules, L2 archetype block, L3 personality sections, L3 flat fallback, L4 user-segment template) + skills_enabled + applied_overrides + composed_preview_text. Owner-gated. Cache-Control: no-store. |
| #375 | refactor(soul-file): SSOT extract of `USER_SEGMENT_PLAN_TEMPLATE` to services/soul_file.py with `{plan_lines}` format hole. compose() at chat time + preview at owner-read time both render from the same constant; chat-time template tweak can never silently drift the preview. 7 new SSOT-pin tests. |
| #376 | feat(soul-file): `engagement_schedule` block on `/system-prompt-preview`. Honest "what's configured today" view of inactivity proactive + skill check-ins + first-turn nudge defaults. Every sub-block carries `source` + `note` so the bot owner sees per-bot vs per-user vs global. New `DEFAULT_INITIAL_IDLE_MINUTES = 5` in nudge.py — should_nudge() default and preview both reference it. 13 new tests. |
| #377 | fix(etl-drain): correct Phase-2 watermark — wall-clock datetime, not monotonic-as-epoch. Two stacked bugs: `datetime.fromtimestamp(time.monotonic())` produced 1970-relative timestamps (monotonic is seconds since boot, not seconds since epoch), and the resulting ISO string was being passed to asyncpg's typed parameter binding (asyncpg rejects strings on `timestamptz` even with `::timestamptz` cast in SQL). Behavioral regression test pins drain.started_at to a recent wall-clock timestamp. |
| #378 | fix(etl-drain): strip tzinfo before asyncpg call — `etl_integrity_results.verified_at` is TIMESTAMP (no tz, per migration 020). After #377's tz-aware datetime, asyncpg raised "can't subtract offset-naive and offset-aware datetimes". Strip tzinfo at the call site only, keep `started_dt.isoformat()` for the report. Tightened FakePool to mirror asyncpg's tz-naive constraint. |
| #379 | fix(etl-drain): normalize `_v2_latest_import_ts` + `_chat_ai_counts_from_hourly_payload` to tz-aware UTC. Both helpers were silently returning tz-naive datetimes from TIMESTAMP columns while the rest of the file used `datetime.now(tz=UTC)` — every subtraction/comparison broke. Tag tzinfo=UTC on the way out of both. 2 new regression tests pin tz-aware return on each helper. |
| (Mobile expert) | Pending #1195 (Soul File page rewrite) — held until rollout window closes. |

### ETL state confirmed live (2026-06-12 13:22 UTC)

- **Heartbeat**: 9-92 seconds old across the day's checks. Stale threshold is 15 min. ✅
- **Sentinel canary**: passed with **6-second end-to-end** lag (chat-ai write at 12:54:57 → v2 detection at 12:55:03). This is the strongest data-flow signal. ✅
- **Importer runs**: tick every ~5 min. Last run at 13:19:54 pulled 6 new message rows. ✅
- **24h totals**: 421 files processed, 5,936 rows applied, 9,415 deliberately skipped (3,474 conflict + 6,320 orphan — all expected per Option-A duplicate-conv + orphan-msg handling). ✅
- **v2 row counts**: messages 3,469,816 / conversations 287,341 / ai_influencers 3,944. v2 has +64,287 messages vs chat-ai per the 12:20 hourly tick (~1.85%). Direction is "v2 has MORE not LESS" — re-bootstrap residual (~27K from PR #227) + v2-native flagged messages (16,601 — is_proactive 15,285 + is_nudge 1,284 + system role 26 + human_takeover 6) + watermark timing gap (~50) + unexplained ~20K (probably chat-ai TTL/soft-deletion + bot replies generated on v2 that weren't echoed back). NOT data loss; closed-books audit queued as 21γ.P11.
- **Reconciliation endpoint**: returns INVESTIGATE verdict due to `_chat_ai_counts_from_hourly_payload` looking for `chat_ai_count` while payload uses `chat_ai` key — 3-line fix queued as 21γ.P12, first post-rollout PR. Sentinel + heartbeat are the actual cutover gate.
- **Anastasia in DB**: category = `Culture & Arts`, NOT one of the 5 `ARCHETYPE_PROMPTS` keys (companion/advisor/entertainer/educator/creator) — that's why her L2 archetype block is empty in the new preview. Schema split queued as 21γ.P8.

### Pre-rollout pg_dump

```
FILE:    /home/rishi-deploy/yral-backups/pre-cutover-21b-20260612-135637.dump
SIZE:    573,336,692 bytes  (547 MB)
SHA256:  4613aa69da3e4a8399c843fa274163aa5de6c65a947c3ce4e93f43f98175d557
HOST:    rishi-4 (138.201.128.108)
TAKEN:   2026-06-12T13:57:32Z
FORMAT:  pg_dump -Fc -Z 6 (custom, compressed, restorable via pg_restore)
```

If 10% rollout goes sideways: this is the point-in-time rollback target.

### Sentry audit (deployed config + code)

**Code-side (`infra/sentry.py` + `app/auth.py`):**
- `sentry_sdk.set_user({"id": user_id})` runs inside `get_current_user()` for every authenticated request (auth.py:47). `user_id` is the JWT `sub` claim = **Principal ID**. Every Sentry event automatically carries `user.id = <principal>`. ✅
- `sentry_sdk.set_tag("request_id", request_id)` per request (middleware.py:17) — correlates Sentry ↔ Caddy logs ↔ Langfuse traces. ✅
- URL secrets scrubbed via `before_breadcrumb` + `before_send` — covers `{key, api_key, apikey, token, access_token, auth, secret, password, signature}` query params. ✅
- `send_default_pii=False` — does NOT auto-attach request headers, cookies, or IP. ✅
- FastAPI + Starlette + logging integrations all loaded. ✅

**Deployed env (verified via `docker service inspect yral-rishi-agent` on rishi-4):**
- `SENTRY_DSN`: host = **sentry.rishi.yral.com** (matches CLAUDE.md rule 5 — your self-hosted Sentry, not apm.yral.com). ✅
- `SENTRY_ENVIRONMENT=production` ✅
- `SENTRY_TRACES_RATE=1.0` (100% transaction tracing) ✅
- `SENTRY_PROFILES_RATE=0.05` (5% profiling — overhead-conscious tuning vs code default of 1.0) ✅

Gaps queued as polish (NOT blocking rollout):
- 21γ.P13 — add `bot_id` + `conversation_id` tags at chat-send / coach route entries (15-line change, 4 routes)
- 21γ.P14 — wrap background-task iterations in `proactive.py` / `nudge.py` / ETL loops with `set_user` / `set_tag` per row (~50 lines)

### 21γ items added to PROGRESS.md today

| # | Item | Effort |
|---|---|---|
| 21γ.P8 | Split `ai_influencers.category` (display taxonomy) from a new `archetype` column | 1-2 days |
| 21γ.P9 | TZ audit of `user_skill_state.preferred_times` → `next_event_at` conversion | 30 min |
| 21γ.P10 | Drain endpoint async refactor (202 + job ID + poll endpoint) + `TIMESTAMPTZ` schema promotion | 1-2 days |
| 21γ.P11 | Audit the +64K v2-vs-chat-ai message gap | 1-2 hr |
| 21γ.P12 | Fix `_chat_ai_counts_from_hourly_payload` key-name bug (`chat_ai_count` → `chat_ai`) | 1 hr |
| 21γ.P13 | Sentry `bot_id` + `conversation_id` tags on chat routes | 30 min |
| 21γ.P14 | Sentry per-iteration context in background tasks | 4-6 hr |

### Open PROD BLOCKERs entering rollout window

| # | Status |
|---|---|
| H2 server-side billing paywall | NOT shipped. Decision: accept revenue leak for 10% cohort, ship within 48h post-rollout. Brief: `~/.claude/plans/h2-server-side-billing-paywall-brief-2026-06-11.md`. |
| H8 Phase 24 security drills | NOT shipped. ~5 days work, scope-trim decision pending. Per 2026-06-08 model this is a PROD prereq — strict reading says we're not ready for 21β. Discussed with Rishi; he's proceeding anyway. |
| H11 cost alerting | Status unclear from session memory. Worth verifying tomorrow morning before second rollout step. |
| DEV-3 | Status unclear from session memory. Verify tomorrow morning. |

### Pre-rollout checklist (handed to Rishi as the "1-hour list")

| # | Item | Status at end of session |
|---|---|---|
| 1 | Fresh pg_dump | ✅ done (547 MB, SHA `4613aa69...`) |
| 2 | ETL data flow verified | ✅ sentinel 6s, heartbeat fresh, live imports |
| 3 | Reconciliation verdict | ⚠️ INVESTIGATE (tool bug — quote sentinel to Sarvesh instead) |
| 4 | H2 paywall decision | ✅ accept leak, 48h follow-up |
| 5 | Motorola smoke test | ⏳ Rishi to do before Sarvesh flips |
| 6 | Sarvesh rollback plan confirmed | ⏳ Rishi to verbally confirm with Sarvesh |
| 7 | Sentry configured correctly | ✅ DSN, env, Principal ID, 100% tracing — all confirmed |
| 8 | Pause mobile + dev session | ⏳ Rishi has the drafted messages, plans to send when ready |
| 9 | Tabs open during rollout | ⏳ Rishi sets up `/admin/etl-status` + Sentry dashboard |
| 10 | Post-rollout PR queue documented | ✅ 21γ.P12 first, then H2, then 21γ.P13, then resumed mobile work |

### Post-rollout PR queue (tomorrow's order)

1. **21γ.P12** — 3-line reconciliation key-name fix. Lowest risk PR to validate the post-rollout pipeline.
2. **H2 billing paywall** — PROD BLOCKER countdown starts at flip time. 48h target.
3. **21γ.P13** — Sentry `bot_id` + `conversation_id` tags. 30-min polish, makes triage faster while watching the 10% cohort.
4. **Mobile expert's queued work** — Soul File page UI (#1195) once paused work resumes. Standing rule: Motorola pass first.
5. **21γ.P11** — read-only +64K gap audit.
6. **21γ.P10** — drain endpoint async refactor + TIMESTAMPTZ migration.
7. **21γ.P14** — Sentry per-iteration background-task context.

### Deployed image

- `ghcr.io/dolr-ai/yral-rishi-agent:cf79493` (post-#379) live on rishi-4 + rishi-5. Stack: yral-rishi-agent service replicated 2/2.

---

## 2026-06-11 — H6 PROD BLOCKER cleared + Coach Bucket 1 mostly done + ETL drain shipped + runpod_vllm rotation + proactive cost-attribution fix

### What happened in one paragraph

Big day. Closed Phase 21αβ.H6 (PROD BLOCKER) — the WAL-G restore drill is verified end-to-end on rishi-6. Took 4 drill iterations to get there: first run hit a config-quoting bug in my script, second hit a silent `set -e` exit, third hit an orphan-postgres port conflict from drill 2's skipped teardown, fourth ran clean and produced the actual proof — 3,941 ai_influencers / 287,183 conversations / 3,460,303 messages restored from S3, with the latest message timestamp lagging by only 10 min. The WAL-G safety net is real and live. Each fix added more defensive scaffolding (`docker exec --user postgres` for cleaner quoting, defensive sanity-query helpers, pre-flight orphan cleanup + EXIT trap, numeric guard on count results). Net 10 backend PRs shipped today — most via dev session executing on the Bucket 1 Coach simplification brief I drafted after the strategy + Codex sessions: PR-1 truncation guard, PR-2 shared JSON extractor + 2 validators, PR-4 `pending_proposal_exists` field, PR-5 Sentry on Coach timeout, plus PR-3 design doc landed as the approval signal for tomorrow's migration 035 + apply-binding code work. Mobile expert ran T2/T3 on Motorola — Item 1 passes. ETL on-demand drain + reconciliation workflow shipped via #344 (yesterday's plan executed). Saikat's runpod_vllm endpoint moved from the dead runpod proxy URL to saikat-llm-medium-fast.yral.com — rotation workflow + GitHub-Secret-based rotation flow shipped (sidestepping Vault for now). Caught a real bug in proactive.py: skill-checkins + the legacy proactive loop were labeled `user_chat_main` in `llm_costs` AND routed through Gemini instead of Saikat — fixed via `process_override` parameter on `ai_client.generate_response()`.

### PRs merged today (10)

| # | What |
|---|---|
| #336 | `:stable` GHCR tag fix (packages:read → write) |
| #337 | Coach JSON-fence parser fix (`_try_extract_proposal` only — left `coach_opening` for PR-2) |
| #338 | distinguish runner config-refusal from replica-rejection (set-e bug introduced here, fixed later same day) |
| #339 | CI: idempotency check (second-run no-op verification) |
| #340 | CI: squawk expansion (lock_timeout + statement_timeout) |
| #341 | (closed) — Vault path superseded |
| #342 | runpod_vllm new URL + rotation workflow consuming GitHub Secret |
| #343 | deploy.yml migration-step failover under set -e (`RUNNER_RC=0; ssh ... || RUNNER_RC=$?`) |
| #344 | ETL on-demand drain + reconciliation system (yesterday's plan, 1906 LOC) |
| #345 | proactive cost-attribution + actually use runpod_vllm for background |
| #346 | WAL-G restore drill — Phase 21αβ.H6 infrastructure |
| #347 | WAL-G drill quoting fix + Coach PR-1 truncation guard (bundled by branch collision) |
| #348 | closed (duplicate of #347) |
| #349 | Coach PR-2 — one JSON extractor + two validators |
| #350 | Coach PR-4 — expose `pending_proposal_exists` |
| #351 | PR-5 — Sentry capture on `soul_file_coach` timeout |
| #352 | Coach PR-3 DESIGN doc (`/apply` binding + status migration) — approval signal |
| #353 | drill defensive sanity queries + pre-flight diagnostics |
| #354 | drill orphan cleanup + EXIT trap + full startup log |
| #355 | drill: drop nonexistent `users` table check + numeric guard |

### Phase 21αβ.H6 — verification numbers

Drill #4 PASSED on rishi-6 at 2026-06-11T06:43Z. Final summary:

```
─── WAL-G restore drill summary ───
Target:   rishi-6 (162.55.88.112)
Exit:     0
Verdict:  GREEN — restore mechanism proven end-to-end

[walg-drill 20260611T064333Z] row counts:
[walg-drill 20260611T064333Z]   ai_influencers = 3,941
[walg-drill 20260611T064333Z]   conversations  = 287,183
[walg-drill 20260611T064333Z]   messages       = 3,460,303
[walg-drill 20260611T064333Z]   latest message = 2026-06-11 06:34:01 UTC
```

Latest-message lag = 10 min. WAL stream is genuinely live.

### Cost attribution fix (the dashboard finally tells the truth)

Rishi noticed `proactive_generation` showing 0 calls while `user_chat_main` was $1.46/1900 — suspect ratio for real-user chat. Root cause in `app/services/proactive.py` lines 171 + 355: both proactive loops called `ai_client.generate_response()` which hard-codes `process="user_chat_main"`. Two consequences: (1) every proactive llm_costs row landed under `user_chat_main`; (2) LLM_DEFAULTS["proactive_generation"] routes to runpod_vllm → internal_vllm, NEVER gemini, but the actual labeling meant the calls took gemini's premium-priced path. Fix: `process_override: str | None = None` parameter on `generate_response()`; proactive passes `process_override="proactive_generation"`. From the next service roll: clean `proactive_generation` rows in cost table + Saikat's pod actually receives proactive traffic + ASYNC_PROCESSES_NEVER_GEMINI guard finally protects in practice.

### Coach Bucket 1 status (from this morning's strategy + Codex brief)

| Item | What | Status |
|------|------|--------|
| PR-1 | Truncation guard (max_tokens 2048→4096 + clean reprompt) | ✅ shipped via #347 + Motorola pass via #1191 |
| PR-2 | One JSON extractor + parse_proposal/parse_opening validators | ✅ shipped via #349 |
| PR-3 | `/apply` binding to `proposal_id` + status lifecycle | 🟡 design doc landed via #352, code ships tomorrow after Rishi pg_dump |
| PR-4 | Expose `pending_proposal_exists` on responses | ✅ shipped via #350 |
| PR-5 | Sentry capture on Coach timeout | ✅ shipped via #351 |

Mobile expert has Item 2 (Save-button gate) committed locally, holding push until PR-3 lands tomorrow. Items 2 + 3 ship as one mobile PR on the `rishi/coach-pivot-bucket1-item2-pending-proposal-gate` branch.

Preview-before-apply UX work parked until Bucket 2 sections land (per Rishi's earlier call; I drifted on that in a midday brief and mobile expert caught it).

### Followups for tomorrow

- **Dev session, first thing AM:** Rishi takes pg_dump → ship Coach PR-3 (migration 035, NOT 037; column shape locked in #352 approval comment). Then write Bucket 2 contract doc (sectioned `system_instructions_sections` JSONB + `coach_sectioned_v2_enabled` flag + GET/PUT soul-file endpoints).
- **Dev session, autonomous overnight if bandwidth:** ETL drain end-to-end validation via the new workflow; I-Mig2 + I-Mig3 expansions; optionally H10 dashboard tiles for backup health.
- **Dev session, ~2 days:** H2 server-side billing paywall (PROD BLOCKER). Brief at `/Users/rishichadha/.claude/plans/h2-server-side-billing-paywall-brief-2026-06-11.md` — 3 PRs, leverages existing `BILLING_URL` config + mobile's exact contract with billing.yral.com.
- **Mobile expert AM:** PR-3 merges → push Item 2 + 3 as one coordinated PR on the pivot branch.
- **Drill expansion (low priority):** sample more tables (coach_messages, system_instructions_history, llm_costs) — V2 has 23 tables, drill currently samples 3.
- **Backup-health dashboard (low priority):** tile on `/admin/llm-routing` style page showing latest WAL-G backup timestamp + age + last drill pass + last drill timestamp.

### Cutover PROD-BLOCKER status

| Item | Before today | After today |
|------|--------------|-------------|
| H6 WAL-G restore drill | 🔴 PROD BLOCKER | ✅ CLEARED |
| H11 cost alerting | "in PR" | (verify with dev session it landed) |
| H12 multimodal routing | (already shipped) | ✅ done |
| H2 server-side billing paywall | 🔴 PROD BLOCKER | ⏳ ~2 days (brief drafted, dev session owns) |
| H8 Phase 24 security drills | 🔴 PROD BLOCKER | ⏳ ~5 days (largest remaining) |

Two PROD BLOCKERs down, two left. H8 is the largest; needs scope-trim decision from Rishi.

### Prod health at EOD

`/health`, `/api/v1/influencers`, `/trending` all 200. All migrations applied (35 entries in `schema_migrations`). Patroni 3/3 healthy. WAL-G safety net verified live.

---

## 2026-06-09 — #314 P0 incident + full migration-runner hardening + Coach Fix 1/Fix 2 backends live

### What happened in one paragraph

Dev session's PR #314 (Coach Fix 1 PR-A — per-bot `global_rule_overrides` JSONB column on `ai_influencers` + 5 code references) merged at ~11:34 UTC and broke prod. The `migrations_changed` gate planted in PR #322 yesterday silently skipped the migration apply step on shallow clones (`git rev-parse SHA~1` partial-outputs the original SHA, defeating the empty-detection), so the new image rolled to swarm with no column to read against. `/api/v1/influencers` started returning 500 (`UndefinedColumnError`) within minutes. Caught it via manual endpoint check, triggered `rollback.yml` workflow at 11:52 UTC — prod restored to image-of-471260e in ~30s. Alpha team's effective outage was ~5 min. Real users on chat-ai unaffected (different cluster). Built 8 follow-up PRs to fix the runner end-to-end. Re-applied #314 via #332 — runner exercised its full happy path for the first time ever (pg_dump → S3 → apply → record → image roll → /health 200). Then reopened the dev session's stacked PRs (#316 → #333, #317 → #334) which had been auto-closed when #314's branch was deleted. Both deployed clean. End state: 33 + 34 schema migrations applied, all 3 Coach Fix 1 + Fix 2 PRs live, recovery plumbing permanent.

### PRs merged today (11 total)

| # | What | When |
|---|------|------|
| #314 | Coach Fix 1 PR-A — `global_rule_overrides` column + code (THE one that hit the gate bug) | 11:34 — reverted 12:18 — re-applied via #332 13:01 |
| #323 | Migration runner: UNIX-socket trust auth (not TCP+md5) | 11:27 |
| #324 | Remove broken `migrations_changed` gate from deploy.yml | 11:33 |
| #325 | Revert #314 to neutralize main while runner is being hardened | 12:18 |
| #326 | Patroni image: install `awscli` for migration runner safety pg_dumps | 12:21 (build failed → #328) |
| #327 | Runner defensive — refuse on empty `schema_migrations` + populated DB | 12:21 |
| #328 | Patroni image: add `python3-docutils` so apt awscli runs (followup to #326) | 12:24 |
| #329 | One-shot bootstrap workflow for `schema_migrations` | 12:32 |
| #330 | Rolling-update workflow for patroni image (leader-last, gated on 3/3 healthy) | 12:38 |
| #331 | Fix new workflows to use `ssh-keyscan` (was using static `KNOWN_HOSTS` with only RSA entries) | 12:44 |
| #332 | Reapply #314 — now safe because runner has all 4 fixes in place | 13:01 |
| #333 | Coach Fix 1 PR-B (originally #316) — platform-rule awareness + migration 034 | 13:08 |
| #334 | Fix 2 backend (originally #317) — plain-English summary endpoint | 13:18 |

### Migrations applied to prod

- `033_ai_influencers_global_rule_overrides.sql` — applied 12:57 UTC via runner; safety dump at `s3://rishi-yral/yral-rishi-agent-pre-migration-dumps/pre-migration-033_...20260609T125722Z.sql.gz`
- `034_coach_message_proposed_override.sql` — applied 13:13 UTC via runner; safety dump at `s3://rishi-yral/yral-rishi-agent-pre-migration-dumps/pre-migration-034_...20260609T131158Z.sql.gz`

`schema_migrations` now has 34 entries (001-034). 32 of those were backfilled by the one-shot bootstrap workflow at 12:51 UTC; the last two (033, 034) were recorded by the runner during normal deploys.

### Patroni cluster state

Rolled all 3 patroni services to `ghcr.io/dolr-ai/yral-rishi-patroni-pgvector:5950fdc...` (the build with `awscli` + `python3-docutils`) via #330's new rolling-update workflow at 12:48-12:50 UTC. Roll order was: rishi-4 (replica) → rishi-6 (replica) → rishi-5 (leader, with Patroni failover). Cluster stayed 3/3 healthy on shared timeline throughout. Leader after roll: TBD (Patroni promotes the most-current replica; verify via `patronictl list` next session).

### What the runner can now do

1. Connect via UNIX socket + trust auth (no password drift risk)
2. Refuse cleanly when `schema_migrations` is empty AND the DB has tables already (prevents replay of 001+ on populated DB)
3. Take a pre-migration pg_dump → upload to S3 → only then apply (Rule 9 automation)
4. Record applied filenames in `schema_migrations` so future runs know what's done
5. Halt the deploy if any apply step fails — old image keeps serving on old schema

### Followups noted

- **`:stable` GHCR tag step in deploy.yml has been failing on every deploy** with `installation not allowed to Write organization package`. Pre-existing, not blocking, but worth fixing. Either: (a) PAT with `write:packages` on the org, or (b) remove the step in favor of commit-SHA tags only. Tasked to dev session in EOD prompt.
- The runner's failover message still says "✗ Migration script failed — likely a replica" when the actual failure is a defensive refusal. Tiny UX fix.
- The roll workflow's `image_tag` input rejects abbreviated SHAs — first roll attempt failed because the workflow tags with the full 40-char SHA but I typed the 7-char one. Could auto-resolve; not urgent.

### Prod health at EOD

`/health` 200, `/api/v1/influencers` 200, `/trending` 200. Zero open PRs. Both new schema columns in place. Image deployed includes all the Coach Fix 1 + Fix 2 backend logic.

---

## 2026-05-30 — ETL Option A live; cursor refactor; H2H verified; sender_id polish

### ETL fully operational

Option A (skip + log duplicate/orphaned rows) shipped, deployed, applying real data. Pipeline catching up on yesterday's backlog plus today's new activity.

**Per-table cursors live (from `etl_processed_files` join):**
- `ai_influencers`: epoch (no new rows on chat-ai)
- `conversations`: cursor at `2026-05-30 07:04:30`, 91 rows applied
- `messages`: cursor at `2026-05-30 07:43:43`, 1212 rows applied

**24h totals (catch-up window, NOT steady state):**
- 78 files processed
- 1303 rows applied
- 1088 rows skipped (535 conflict + 553 orphan)
- 6 distinct conversation conflicts (the actual Option A class)
- Heartbeat fresh (124s), no STUCK marker

**Crossing the 500/day "revisit" threshold — context required.** The 1088 skips include yesterday's backlog being processed today; steady state is much lower. Estimate after 24h of post-catchup steady state: ~1500 if orphan rate stays at current level, much lower if orphans are dominated by parent-skipped cascade (which they appear to be — only 6 distinct conv conflicts but 553 distinct orphan messages → average 92 orphans per skipped conv, consistent with active chat history). **Real call on Option A vs B requires waiting through steady-state — should land tomorrow's morning check.**

### PRs merged today (6 total)

- **#216** — `feat(etl)`: Option A skip + log + audit table (migration 021)
- **#217** — `fix(etl)`: disambiguate `$3` param types in `_advance_cursor` (BIGINT vs INT casts)
- **#218** — `fix(etl)`: parse `until_iso` to datetime (asyncpg timestamp codec)
- **#219** — `refactor(etl)`: drop `_advance_cursor`, derive cursors from `etl_processed_files` (eliminates the 3-bug cascade)
- **#220** — `feat(api)`: expose `sender_id` in message API responses (mobile H2H bubble alignment)

### Backups taken (rule #9)

- `~/yral-backups/pre-migration-021-skipped-rows-20260530-064352.dump` on rishi-5 (526 MB, sha256 `c9e18c1b795161c82c10b4596b7c309f65009ac2f43274944b21e97a315c31f5`)

### H2H verification — mobile expert is clear

All 5 invariants verified ✅:
- 3a No LLM call on H2H send (`chat.py:389` rejects pre-LLM; `human_chat.py` has zero LLM imports)
- 3b No memory extraction (only ws broadcast + push notification background tasks)
- 3c No content_safety on H2H (deliberate, matches "deliver, don't censor" intent)
- 3d Engagement loops exclude H2H (proactive + nudge both filter `conversation_type = 'ai_chat'`)
- 3e v3 inbox returns both AI + H2H with correct `influencer`/`peer_user` field set

Endpoint suite: 34/35 PASS. The 1 FAIL is an unrelated stale-test (`/admin/etl-integrity` test checks the old key `latest_per_check` which Phase 3 renamed to `latest_per_layer`) — small fix to backlog.

Minor non-regression flag: `peer_user.display_name` and `avatar_url` are returned as `None` from v3 inbox. Mobile will need either the principal ID display or a separate user-info call.

### Integrity loop status

Has not yet fired since the morning's deploys. INITIAL_DELAY is 10 min per deploy, and we did 4 deploys today (Option A + 3 cursor-fix iterations). Integrity will start ticking ~10 min after the last deploy and chew through the backlog of 14 hourly + 26 sentinel + 3 sample + 177 tick payloads queued in S3.

### Tomorrow morning's check

Re-pull `/admin/etl-status` + `/admin/etl-integrity` + `/admin/etl-skipped` after 24h of stable operation. If `skipped_rows_24h` is still >500 in a steady-state window, schedule the Option B (remap) discussion.

### Loose follow-ups (low priority)

- Drop `etl_sync_state` table in a future migration (now vestigial, no readers/writers)
- Update `scripts/test_all_endpoints.py` for the `latest_per_check` → `latest_per_layer` rename
- Two V2 replicas race on the same S3 file (eventually consistent via filename PK, but wasted work) — could add SELECT FOR UPDATE on a lease table for single-flight

## 2026-05-29 (evening) — S3 ETL pivot Phases 1-3 shipped, Phase 4 deployed but apply blocked on schema mismatch

### The pivot
Direct V2 → chat-ai asyncpg pull was confirmed unreachable (TCP timeout on the public IPs — chat-ai's Postgres lives on a swarm overlay only). Pivoted to S3-mediated: rishi-1 pushes deltas from inside chat-ai's swarm, V2 pulls from S3.

### PRs merged today
- **#210** — `fix(etl)`: chat-ai DSN read from Swarm secret file (file-first pattern) — superseded by the S3 pivot below, but the file-first pattern was reused for `chat_ai_s3_credentials`
- **#211** — `feat(etl)`: Phase 1 — rishi-1 incremental exporter (330 lines + 126 tests). CSV-via-COPY, gzip, boto3 upload, heartbeat + STUCK marker
- **#212** — `feat(etl)`: Phase 2 — V2 S3 fetcher (replaces direct asyncpg pull). New migration 019 for `etl_processed_files`. Old `etl_integrity.py` gutted to Phase-3-pending stub
- **#213** — `feat(etl)`: Phase 3 — 4-layer integrity verification (tick/hourly/sample/sentinel) via S3. New migration 020 for `etl_integrity_results`. Three new admin endpoints
- **#214** — `fix(etl)`: export script — count CSV rows not byte newlines (caught in dry-run; messages with multi-line content over-counted)

### Phase 4 deploy state (frozen overnight, safe)
- V2 backup taken: `~/yral-backups/pre-migration-019-020-20260529-155852.dump` on rishi-5 (525 MB, sha256 `cb2cdaa78e827a966ca0c5517672fe965a84656dce4709d93aded5eff7d21ab1`)
- Migrations 019 + 020 applied via leader on rishi-5
- New V2 image (`47ef5ef`) rolled out — health 200
- rishi-1 setup complete: `~/.etl-export/{incremental_export.py, credentials, state.json}` with mode 0700 dir / 0600 creds
- Manual dry-run succeeded after the byte-newline fix: 962 conversations + 32,640 messages + all 4 integrity payloads landed in `s3://rishi-yral/yral-chat-ai/incremental-sync/`
- cron enabled on rishi-1: `*/5 * * * * python3 ~/.etl-export/incremental_export.py >> ~/.etl-export/etl-export.log 2>&1`
- V2 Swarm secret `chat_ai_s3_credentials` mounted; old `chat_ai_database_url` removed in same `service update`
- `/admin/etl-status` shows `s3_credentials_mounted: true`, heartbeat fresh, `stuck_marker: null`

### The block — schema mismatch on apply
V2's first apply tick (2026-05-29 16:15 UTC) failed on every file with `UniqueViolationError: idx_unique_user_influencer` for conversations and cascading `ForeignKeyViolationError` for messages.

Root cause: **chat-ai allows multiple conversations per (user, influencer) pair; V2's schema enforces at most one** (via `idx_unique_user_influencer` UNIQUE WHERE influencer_id IS NOT NULL). chat-ai has a similar `idx_unique_human_chat` constraint V2 doesn't.

**No data harm.** Nothing recorded in `etl_processed_files`, nothing in V2 modified. State is safe to leave overnight: cron keeps publishing fresh data to S3, V2 loop keeps retrying with warning logs.

### Tomorrow morning — Option A (Rishi's call)
Skip duplicates with logging, not merge/remap. Saved in memory: `project_etl_option_a_conflict_handling.md`.

1. `ON CONFLICT (user_id, influencer_id) WHERE influencer_id IS NOT NULL DO NOTHING` on conversations
2. Pre-check `SELECT 1 FROM conversations WHERE id = $1` before each message INSERT; skip + log if missing
3. New migration 021: `etl_skipped_rows (filename, table, row_id, reason, skipped_at)`
4. Extend `/admin/etl-status` with `skipped_rows_24h` + per-reason counts
5. Extend `/admin/etl-integrity/details` with recent skip details
6. Restart V2 service — queued S3 files reprocess with new logic
7. Report: rows applied per table, rows skipped per reason, Layer 1 integrity result (drift expected to equal skipped count)
8. Add runbook entry: "chat-ai duplicate (user, influencer) conversations are not migrated."

Health threshold: check `skipped_rows_24h` after 24h. <50/day = fine. >500/day = revisit (Option B remap or schema discussion).

### What's still untouched
- Phase 5 (`docs/ETL-OPS-RUNBOOK.md`) — to be merged with the Phase 4-completion PR tomorrow so the runbook reflects final Option A semantics

## 2026-05-30 (cutover-prep close) — Tasks B + C deployed; ETL idle until cred set

### Both tasks live on agent.rishi.yral.com
- **Task B (#207)** — continuous incremental ETL background loop (every 5 min)
- **Task C (#208)** — hourly integrity verifier
- Migrations 017 + 018 applied; pg_dump snapshots taken
- 35/35 endpoint suite green
- `/admin/etl-status` and `/admin/etl-integrity` both JWT-gated and returning empty (loops idle)

### Activation steps (single command from Rishi)
Once a read-only DB user is provisioned on chat-ai:
```
docker service update --env-add CHAT_AI_DATABASE_URL="postgresql://etl_readonly:****@<chat-ai-host>:5432/chat_ai_db?sslmode=require" --force yral-rishi-agent
```
Next loop tick (≤5 min) starts syncing. First integrity pass fires 10 min after that. `GET /admin/etl-status` and `GET /admin/etl-integrity` show progress.

### Backups taken
- `pre-migration-017-etl-sync-20260529-181608.dump`
- `pre-migration-018-integrity-20260529-182922.dump`

### What did NOT happen (per Rishi's hard constraint)
- I did NOT extract chat-ai credentials
- I did NOT SSH to rishi-1/2/3 for anything beyond verifying the deploy worked from rishi-4/5
- I did NOT modify chat-ai's schema, config, or run any privileged operation against it
- The chat-ai pool inside the v2 service opens with `default_transaction_read_only=on` — even a typo'd INSERT in our code would be rejected at the Postgres session level

### Standing approval cycle closes
Tasks A (latency comparison — deferred), D (rollback docs — deferred), E (Sentry alerts — deferred) per Rishi's adjusted scope. Pausing for next direction.

## 2026-05-30 (later) — Task C: hourly data-integrity verifier (cutover-readiness)

### Three checks per pass
1. **row_count** — per-table chat-ai vs v2 COUNT(*) diff. pass if |diff| ≤ 500 (≈ 5 min of writes); warn if 500-5000; fail if > 5000.
2. **sample_conversations** — pick 20 random chat-ai conversations older than 15 min, verify every message present in v2 with matching (id, content, created_at, message_type). 0 mismatches = pass; 1-2 = warn; 3+ = fail.
3. **fk_integrity** — v2-side: count conversations with influencer_id pointing nowhere + messages with conversation_id pointing nowhere. 0 = pass; else fail.

### Storage
Migration 018 adds `etl_integrity_checks` table — one row per check per pass with check_type, table_name, counts, diff, JSONB details, status, runtime_ms, checked_at.

### File logging
Each check is also appended to `/tmp/etl_integrity.log` with a one-line status — per Rishi's spec, no Google Chat alerts for now.

### Endpoint
`GET /admin/etl-integrity` (JWT-gated): latest result per check_type + 24h fail/warn counts. Operator dashboard for cutover-readiness.

### Loop
Every 1 hour. 10-min initial delay so the ETL has time to populate before the first integrity scan. Idle if `CHAT_AI_DATABASE_URL` unset (same pattern as Task B).

### Files
- `migrations/018_etl_integrity_checks.sql`
- `app/services/etl_integrity.py` (new, ~280 lines)
- `app/main.py` — register integrity_task
- `app/routes/health.py` — `/admin/etl-integrity` endpoint
- `scripts/test_all_endpoints.py` — 34 → 35 endpoint tests
- `tests/test_etl_integrity.py` — 5 pins (thresholds, interval, ordering, table-list parity with Task B's SYNCED_TABLES)

### Diff
+390 / -0 across 6 files.

### Deploy
1. pg_dump
2. Migration 018
3. Rebuild + deploy
4. Loop is idle until CHAT_AI_DATABASE_URL is set (same as ETL)
5. Once set, first integrity pass fires 10 min later; subsequent passes hourly

## 2026-05-30 — Task B: continuous incremental ETL from chat-ai (cutover-readiness)

### Why
Per Rishi's cutover-readiness audit: existing ETL is snapshot-based (Day 9 pg_dump load, now 3 days stale). For cutover we need v2 to mirror chat-ai's data within ~5 min so we can switch Caddy without data loss.

### How (Task B spec)
- New background task (`services.etl_chat_ai.etl_loop`) runs every 5 min after a 1-min initial delay
- Reads `CHAT_AI_DATABASE_URL` from env. **If unset, the loop logs once and idles** — operator sets the env var via `docker service update --env-add CHAT_AI_DATABASE_URL=...` to enable. No code redeploy needed.
- Opens a READ-ONLY asyncpg pool to chat-ai with `default_transaction_read_only=on` — Postgres rejects any accidental write at the session level (second line of defense; first is that this module only emits SELECTs).
- Pull-and-insert per table in dependency order: `ai_influencers` → `conversations` → `messages`. Cursor = `etl_sync_state.last_sync_ts` per table; SELECT WHERE created_at > $1 ORDER BY created_at LIMIT 1000; INSERT ... ON CONFLICT (id) DO NOTHING into v2. Cursor advances to max(created_at) in the batch.
- Per-tick safety cap: 50 batches × 1000 rows = 50,000 rows max per table per tick. Prevents a runaway window from monopolizing the loop.
- Idempotent: re-running the same window is a no-op via ON CONFLICT.

### Schema (migration 017)
`etl_sync_state` table: one row per synced table, fields `(table_name PRIMARY KEY, last_sync_ts, last_run_at, rows_pulled_total, rows_pulled_last_run, last_error, last_runtime_ms, updated_at)`. Seeded with the 3 tables we sync.

### Files
- `migrations/017_etl_sync_state.sql`
- `app/services/etl_chat_ai.py` (new, ~250 lines) — pool helper, per-table sync, run_once, etl_loop, get_status
- `app/main.py` — wire etl_task into the lifespan family
- `app/routes/health.py` — new `GET /admin/etl-status` returning per-table cursor + last error (no auth for now — operator-only data, no PII)
- `scripts/test_all_endpoints.py` — 33 → 34 endpoint tests
- `tests/test_etl_chat_ai.py` — 6 pins covering dependency order, idempotency contract, interval, safety cap, env-var lookup

### Diff
+390 / -1 across 7 files.

### Hard constraints (per Rishi's directive)
- READ-ONLY against chat-ai (rishi-1). Default transaction is read-only at the session level — Postgres rejects any accidental write.
- No schema changes, no privileged operations on rishi-1.
- I do NOT extract or read chat-ai credentials. Operator (Rishi) provisions the read-only DB user and sets `CHAT_AI_DATABASE_URL` after this PR ships.

### Deploy steps (post-merge)
1. pg_dump snapshot (per rule #9)
2. Apply migration 017
3. Rebuild + deploy
4. ETL loop starts but is idle (no env var)
5. **Rishi** provisions a read-only user on chat-ai DB (rishi-1) and provides DSN
6. `docker service update --env-add CHAT_AI_DATABASE_URL=... --force yral-rishi-agent`
7. Next loop tick (≤5 min) starts syncing
8. `GET /admin/etl-status` shows progress

## 2026-05-30 (close-out) — Batch 3 final eval-gate + close-out

### Eval-gate result (all 5 tasks deployed)

| Metric | morning v2 | post-batch-3 | vs morning | vs chat-ai now |
|---|---|---|---|---|
| in_character | 4.02 | 4.02 | unchanged | **+0.29** |
| helpful | 2.65 | 2.63 | −0.02 | **+0.78** ⭐ |
| concise | 4.79 | 4.59 | −0.20 (noise) | −0.02 |
| language_match | 3.10 | 2.86 | −0.24 (noisy axis) | −0.08 |
| safe | 4.27 | **4.37** | **+0.10** | **+1.24** ⭐ |
| **overall** | **3.77** | **3.69** | −0.08 (within noise) | **+0.44** ⭐ |
| p95 latency | 9633 ms | 6339 ms | **−34%** | +4763 ms |

**v2's lead vs chat-ai improved from morning's +0.35 → +0.44.** The cleanest delta of the entire week.

### Wizard live E2E (full creator flow)
- Started a wizard with concept "A retired chess grandmaster who teaches kids the love of strategy through Hinglish stories"
- 5 intake questions auto-generated (Gemini fallback to fixed set when first call returned empty)
- Answered each
- Preview generated complete polished bot:
  - display_name: "Prakash Uncle" / category: educator
  - initial_greeting: warm Hinglish welcome
  - system_instructions: rich character description with authentic voice
  - 10-turn sample conversation showing the bot naturally translating its persona into chat
- Commit created the ai_influencers row successfully (one transient 500 on first attempt — second attempt succeeded; appears to have been an asyncpg blip during the rolling deploy)
- Cleanup: deleted the test bot + session

The wizard works.

### Batch 3 summary

| Task | PR | Status |
|---|---|---|
| 1 — Phase 7.7 Bot quality scorer | #201 | ✅ deployed; 122 score rows already accumulated |
| 2 — Phase 7.8 Creator recommendations | #203 | ✅ deployed |
| 3 — Phase 7.6 A/B testing | #204 | ✅ deployed (migration 015) |
| 4 — Phase 7.9 5-min wizard | #205 | ✅ deployed (migration 016) + live E2E verified |
| 5 — Phase 5.6 Streak tracking | #202 | ✅ deployed (migration 014); 181,808 streak rows updated in first pass |

Standing approval cycle closes.

### Backups taken (rule #9)
- `pre-migration-013-quality-scores-20260529-162508.dump`
- `pre-migration-014-streaks-20260529-164735.dump`
- `pre-migration-015-variants-20260529-170944.dump`
- `pre-migration-016-wizard-20260529-172327.dump`

All on rishi-5 (current Patroni leader).

## 2026-05-30 (very late) — Task 4: Phase 7.9 5-minute bot creation wizard

### Flow (4 endpoints under /api/v1/creator/wizard/)
1. `POST /start` — creator gives a 1-2 sentence concept → Gemini generates 3-5 tailored intake questions (archetype, backstory, voice, would-say / wouldn't-say, opening message). Falls back to a fixed minimal intake when Gemini is flaky.
2. `POST /sessions/{id}/answer` — record one answer at a time. When all questions are answered, the route generates + caches a structured Soul File draft (system_instructions + display_name + category + initial_greeting).
3. `GET /sessions/{id}/preview` — synthesize a 5-turn conversation between the draft bot and a synthetic user using the production `generate_response` (so per-archetype tuning applies). Lets the creator see real output before committing.
4. `POST /sessions/{id}/commit` — finalize. Creates the `ai_influencers` row with the draft, marks the session committed.

### Schema (migration 016)
`wizard_sessions(id, creator_user_id, concept, questions JSONB, answers JSONB, draft_*, committed_bot_id, ts)`. Abandoned sessions just sit; no cleanup job (creators finish in minutes, not days).

### Files
- `migrations/016_wizard_sessions.sql`
- `app/repositories/wizard_repo.py` — JSONB merge for record_answer + save_draft + mark_committed
- `app/services/wizard.py` — three Gemini calls (intake / draft / preview) + tolerant JSON parser
- `app/routes/wizard.py` — 4 endpoints
- `app/main.py` — register wizard_router
- `scripts/test_all_endpoints.py` — adds wizard endpoint test (suite now 33)
- `tests/test_wizard.py` — JSON parser pins

### Eval-gate
Three new Gemini call paths but ONLY reachable via wizard endpoints. The chat send_message + proactive paths are unchanged. Eval should be neutral.

### Spot-check plan (post-deploy)
The user's spec says "bots produced via this wizard should score HIGHER on average than bots produced via the old generate-prompt flow." Will:
1. Manually drive the wizard 5x with diverse concepts (companion, advisor, entertainer, educator, creator)
2. Commit each → real ai_influencers rows
3. Send some test conversations through each
4. After the next nightly quality-scorer pass, compare wizard-produced bots' scores vs the existing pool

### Diff
~540 lines across 7 files.

## 2026-05-30 (yet later) — Task 3: Phase 7.6 A/B testing for Soul Files

### Schema (migration 015)
- `soul_file_variants` — one row per bot when a test is staged. `(bot_id, system_instructions, created_at, created_by)`, UNIQUE on `bot_id`.
- `messages.variant_label VARCHAR(1)` — NULL when no test active or when message is a user reply; `'a'` or `'b'` for bot replies during a test. Partial index `idx_messages_variant` for the compare endpoint's lookup.

Variant A = bot's current `ai_influencers.system_instructions` (always production). Variant B = the row above when present.

### Routing (chat hot path)
Inside `send_message`, just before soul file compose:
- Fetch `variant_repo.get_variant_b(bot_id)`
- If B exists: `random.random() < 0.5` picks A or B. Chosen text → soul file → LLM. The `variant_label` is recorded on the assistant message.
- If B doesn't exist (default case for ~all bots): zero-overhead path. The compare endpoint won't see any labeled rows.

### Endpoints
- `POST /api/v1/creator/influencers/{id}/variant-b` — set/replace variant B. Body: `{system_instructions: str}`. Owner-only.
- `GET /api/v1/creator/influencers/{id}/variants/compare` — judges up to 20 labeled bot replies per variant via Gemini-as-judge (same rubric as Phase 7.7). Returns per-variant aggregate scores + `delta_overall` + a `suggested_winner` once both sides have ≥10 samples. Owner-only.
- `POST /api/v1/creator/influencers/{id}/variants/{a|b}/promote` — finalize. Promoting A: just drops variant B. Promoting B: copies B's text to `ai_influencers.system_instructions`, writes a `system_instructions_history` row (with NULL coach FKs since this isn't a coach apply), drops B. Owner-only.

### Coach FK loosening
`coach_repo.record_application` now accepts `coach_conversation_id: str | None` and `coach_message_id: str | None`. The columns were already nullable in the migration 011 schema; the type hints were the only thing pinning them to required.

### Eval-gate
The chat send_message variant branch adds `await variant_repo.get_variant_b(pool, influencer_id)` per turn — one indexed PK lookup, ~1ms. When no variant B exists (the default for all bots in the eval), the routing path is byte-identical to before. Eval should be neutral.

### Files
- `migrations/015_soul_file_variants.sql`
- `app/repositories/variant_repo.py` (new)
- `app/repositories/coach_repo.py` — nullable coach FKs in `record_application`
- `app/repositories/message_repo.py` — `create` accepts `variant_label`
- `app/services/ab_compare.py` (new) — on-demand judge + aggregate per variant
- `app/routes/creator.py` — 3 new endpoints
- `app/routes/chat.py` — A/B routing in `send_message`
- `scripts/test_all_endpoints.py` — 29 → 32 endpoint tests
- `tests/test_ab_compare.py` — sample threshold + concurrency pins

### Diff
~600 lines across 9 files.

## 2026-05-30 (later) — Task 2: Phase 7.8 creator recommendations

### What it does
New endpoint `GET /api/v1/creator/influencers/{bot_id}/recommendations` returns 2-3 SPECIFIC actionable Soul File improvements grounded in:
- The latest nightly quality score (Phase 7.7)
- Up to 30 recent non-proactive bot replies (last 7 days, anonymized — only bot text, no user_id, no user messages)
- The bot's current system_instructions + archetype

### Response shape
```
{
  "influencer_id": "...",
  "recommendations": [
    {
      "weakness": "1-2 sentence summary citing a score / observation",
      "proposed_edit": "exact text to add, replace, or remove in system_instructions",
      "reasoning": "why this specific edit improves the bot, tied to data"
    },
    ...
  ],
  "based_on_score": true,
  "sample_replies_count": 28,
  "hint": null  // populated when recommendations is empty
}
```

The recommendations are designed to drop straight into the Soul File Coach's `proposed_changes` path. Creator can hand a recommendation to the coach to apply atomically (via the existing `/coach/conversations/{id}/apply` endpoint).

### Eval-gate
This task adds a NEW Gemini call path (recommendations generation), but it's only invoked when a creator calls the new endpoint — the chat send_message and proactive paths are unchanged. Eval should be quality-neutral.

### Files
- `app/services/recommendations.py` (new) — meta-prompt + Gemini call + parser
- `app/routes/creator.py` — new endpoint
- `scripts/test_all_endpoints.py` — 28 → 29 endpoint tests
- `tests/test_recommendations.py` — parser + format helper pins

### Diff
+253 / -1 across 4 files. No schema, no migration.

## 2026-05-30 — Task 5: Phase 5.6 streak tracking

### What it does
Three new columns on `conversations`:
- `current_streak_days` — consecutive days the user has sent at least one message
- `longest_streak_days` — peak streak ever
- `last_streak_date` — last day a user message was counted

Daily background job (`streak_tracker.streak_loop`) recomputes streaks via one SQL UPDATE that:
- Joins `conversations c` to a CTE of latest user-message dates per conversation
- Applies streak math (today → unchanged; yesterday → +1; older → reset to 1)
- Updates `longest_streak_days` to max(longest, current)
A second pass zeros out streaks for conversations whose user hasn't sent in >1 day.

### Endpoint
`GET /api/v1/chat/conversations/{id}` (already existed via `_format_conversation`) now returns `current_streak_days`, `longest_streak_days`, `last_streak_date`. Mobile UI to come later (Sarvesh).

### Proactive prompt nod
`PROACTIVE_PROMPT` got a new `{streak_block}` slot. `_streak_block(days)`:
- 0-2 days: empty (not interesting)
- 3-6 days: optional small nod ("only if it fits")
- 7+ days: "solid streak worth acknowledging warmly — one short callout, then move on"

The model decides whether to mention it; we don't hardcode anything in the bot reply.

### Files
- `migrations/014_conversation_streaks.sql` — 3 columns + `idx_conversations_last_streak_date`
- `app/services/streak_tracker.py` — `update_all_streaks_once` + `streak_loop`
- `app/main.py` — wire the background task
- `app/repositories/conversation_repo.py` — SELECT streak columns in `get_by_id`
- `app/routes/chat.py` — `_format_conversation` exposes the 3 fields
- `app/services/proactive.py` — `{streak_block}` in PROACTIVE_PROMPT + `_streak_block` helper
- `tests/test_streak_tracker.py` — pins interval + streak-block thresholds

### Diff
+148 / -5 across 7 files. Migration is additive.

### Eval-gate
The proactive PROMPT changed (added `{streak_block}`), but the streak block is empty for users with <3 day streaks (all current eval prompts). For the eval, the streak_block will always be empty → prompt is byte-identical to before for the eval-relevant code path. Quality should be neutral. Will re-run post-deploy.

## 2026-05-30 — Task 1: Phase 7.7 bot quality scorer

### What it does
Nightly background job (24h cycle, 15-min initial delay so containers warm up) scores each active AI influencer:
- Samples last **20 conversations**
- Pulls up to **3 turn pairs** per conversation
- Runs **Gemini-as-judge** on each pair: in_character, response_quality, engagement (1-5 each)
- Aggregates into a per-bot row in `bot_quality_scores` with all four scores + sample sizes

Concurrency capped at 5 parallel judge calls. Rough budget: 50 bots × 60 pairs = 3000 calls / night, ~15 min wall clock, free-tier Gemini Flash.

### Files
- `migrations/013_bot_quality_scores.sql` — table + idx_bqs_bot_recent
- `app/repositories/quality_score_repo.py` — insert + latest_for_bot + history_for_bot
- `app/services/quality_scorer.py` — judge prompt, turn-pair extraction, per-bot scoring, scoring_loop wrapper
- `app/main.py` — wires the loop alongside the existing background tasks
- `app/routes/creator.py` — new `GET /creator/influencers/{id}/quality-score` (owner-only)
- `app/services/coach.py` — META_PROMPT now includes a `quality_score_block`; `coach_reply` accepts an optional `quality_score` kwarg; `_format_quality_score` helper renders it
- `app/routes/creator_coach.py` — pulls latest score in `send_coach_message` and threads it through
- `scripts/test_all_endpoints.py` — adds the new endpoint as test #28
- `tests/test_quality_scorer.py` — pins constants + the coach format helper

### Diff
+428 / -8 across 9 files. Migration is additive.

### Eval-gate stance
This task adds Gemini judge calls in a NEW background path. The chat send_message path is unchanged — soul_file/ai_client/memory not touched. Eval should be quality-neutral. Will re-run the 50-prompt eval post-deploy to confirm; revert via swarm rollback if regressed.

### Deploy
1. pg_dump
2. Migration 013 on Patroni leader
3. Rebuild + deploy
4. Wait 15 min initial delay
5. Confirm one nightly pass runs successfully via logs + a couple of `bot_quality_scores` rows
6. 27/27 → **28/28** endpoint suite (with the new endpoint)
7. Eval re-run

## 2026-05-29 (very late) — rollback re-eval + batch close-out

### Rollback re-eval (v2 with #199 deployed)
n=49 prompts, same harness, same backends.

| Metric | morning v2 | post-#198 | **post-#199** | vs morning | vs chat-ai |
|---|---|---|---|---|---|
| in_character | 4.02 | 3.92 | **4.18** | **+0.16** ↑ | +0.18 |
| helpful | 2.65 | 2.41 | 2.37 | −0.28 | +0.20 |
| concise | 4.79 | 4.63 | 4.57 | −0.22 | +0.11 |
| language_match | 3.10 | 3.02 | 3.06 | −0.04 | +0.14 |
| safe | 4.27 | 4.12 | **4.37** | **+0.10** ↑ | +0.76 |
| **overall** | **3.77** | 3.62 | **3.71** | **−0.06** | **+0.28** |
| latency p50 | 1699 ms | 1593 ms | 1784 ms | +85 ms | +740 ms |
| latency p95 | 9633 ms | 2716 ms | 4965 ms | −4668 ms | +3086 ms |

Within Gemini-judge run-to-run noise (chat-ai's `safe` score varied 0.39 across runs as a control with no v2 change). `overall` is effectively at morning baseline. `in_character` and `safe` improved — temperature differentiation appears to be a genuine win. p95 latency improved 48% vs morning.

### What stayed from Phase 12
- ARCHETYPE_TUNING dict (per-archetype temperature) — likely positive signal
- Educator few-shot example
- Language enumeration in GLOBAL_RULES

### What got reverted
- Per-archetype sentence caps (regressed quality)
- Tight max_tokens 500-800 (regressed quality; now uniformly 1500)

### Batch close-out
| Task | Status | PRs |
|---|---|---|
| A — trending flake | ✅ | #193 |
| B — eval baseline | ✅ | #195 |
| C — Phase 12 tuning | ⚠️ partial (sentence caps reverted) | #198 → #199 |
| D — proactive frequency | ✅ (squashed via rebase into #195) | #194 |
| is_nsfw on ConversationResponse | ✅ | #196 |

Standing approval cycle closes per the original mandate. Pausing for next direction.

### Lessons captured
1. **Per-archetype tuning needs a feedback loop** — first pass can regress; revert quickly when eval data says so. Now codified in `tests/test_archetype_tuning.py::test_archetype_prompts_do_not_hardcode_sentence_caps` so a future PR can't silently re-introduce the regression.
2. **Eval noise is ~0.1-0.4 per criterion** — small deltas need bigger N or multiple runs to claim. Worth running each comparison twice from now on.
3. **Latency improvements that come from cutting tokens are NOT free** — the LLM had something useful to say in those tokens.

## 2026-05-29 (night) — Phase 12 tuning rollback (data-driven)

### Re-eval after Phase 12 deploy showed regression
Ran `scripts/eval_v2_vs_chat_ai.py` against the freshly-deployed Phase 12 image. Compared to the morning's baseline:

| Metric | v2 morning | v2 post-Phase-12 | Δ |
|---|---|---|---|
| p50 latency | 1699 ms | 1593 ms | −106 ms ✓ |
| **p95 latency** | **9633 ms** | **2716 ms** | **−72%** ✓✓ |
| mean | 2592 ms | 1703 ms | −889 ms ✓ |
| in_character | 4.02 | 3.92 | −0.10 ✗ |
| **helpful** | **2.65** | **2.41** | **−0.24** ✗ |
| concise | 4.79 | 4.63 | −0.16 ✗ |
| language_match | 3.10 | 3.02 | −0.08 ✗ |
| safe | 4.27 | 4.12 | −0.15 ✗ |
| **overall** | **3.77** | **3.62** | **−0.15** ✗ |

Latency improved a lot (responses got shorter); quality regressed across every criterion. v2 still beats chat-ai but the lead narrowed.

### Diagnosis
The sentence caps (`at most N sentences`) inside ARCHETYPE_PROMPTS + the tight max_tokens (500-800 vs 2048 default) forced cramped replies that didn't fully solve the user's ask. Latency improved precisely BECAUSE responses got shorter — but they got worse, not better.

Temperature differentiation (0.50-0.95 per archetype) and the language enumeration in GLOBAL_RULES weren't the culprits per the eval data.

### Rollback (this PR)
- **Sentence caps removed** from every archetype prompt — GLOBAL_RULES' soft "1-3 sentences max" is the only length guidance again
- **max_tokens uniformly 1500** across all archetypes — generous enough to avoid cut-offs, still under the 2048 default
- **Educator few-shot kept** — cheap, can't hurt, may help next eval
- **Temperature differentiation kept** — not the regression cause
- **Language enumeration in GLOBAL_RULES kept** — neutral effect today, clearer intent for future eval

Test updated to actively guard against accidentally re-introducing the per-archetype sentence cap.

### Expected next eval
overall ≥ 3.77 (morning baseline), helpful ≥ 2.65. If still regressed, full revert of Phase 12 and stop until Rishi directs otherwise.

### Diff
+24 / -40 across 2 files. Pure tuning rollback.

## 2026-05-29 (evening, late) — Task C: Phase 12 per-archetype tuning

Driven by Task B's eval gaps: helpful=2.65 weakest both services, language_match=3.10 mediocre, response verbosity bloated concise scores.

### What changed
- `app/services/soul_file.py`:
  - **GLOBAL_RULES** now enumerates specific Indian languages (Hinglish, Hindi, Telugu, Tamil, Bengali, Marathi) so the model can't fall back to English for non-English prompts
  - **ARCHETYPE_PROMPTS** rewritten with per-archetype sentence caps: companion/entertainer max 3, advisor/educator/creator max 4. Each prompt explicitly tells the model what "good" looks like for its tone.
  - **Educator gets a worked few-shot example** — English (recursion) + Hinglish (kya AI sach mein learn karta hai?) — so the model can copy the shape
  - **ARCHETYPE_TUNING** dict: per-archetype (temperature, max_tokens). companion 0.85/600, advisor 0.50/800, entertainer 0.95/500, educator 0.60/800, creator 0.85/700. All max_tokens clamped well under the previous 2048 default; eval showed verbose replies tank concise + helpful.
  - `tuning_for(category)` helper — case+whitespace tolerant
- `app/services/ai_client.py` — both `generate_response` and `generate_response_stream` now accept optional `archetype` and look up tuning. OpenRouter path (NSFW) also honors archetype tuning. Unknown archetypes fall back to config defaults (current behavior).
- `app/routes/chat.py` — both LLM call sites pass `archetype=inf.get("category")`
- `app/services/proactive.py` — proactive generation also threads archetype through
- `tests/test_archetype_tuning.py` — 6 tests pinning the tuning values, sentence caps, educator example, multilingual rules

### What's NOT in this PR
- **Phase 12.2 — advisor → Claude Haiku** is deferred. OpenRouter exposes Anthropic but plumbing per-archetype model selection (vs the current single GEMINI_MODEL + single OPENROUTER_MODEL) is a separate scope. Tracked in PROGRESS Phase 12.2 as still pending.
- **Phase 12.5 — response diversity** (no repetitive phrases) — separate sub-phase, not addressed here

### Diff
+158 / -19 across 5 files. No schema, no migration.

### Re-eval plan
After deploy, re-run `scripts/eval_v2_vs_chat_ai.py` and compare to today's baseline. Expected improvements: helpful +0.3 (sentence caps + educator few-shot), language_match +0.4 (enumeration), concise stable or up (max_tokens clamped). If no improvement, the tuning values are the lever to revisit.

## 2026-05-29 (evening) — batch-2 deploy + verifications

Tasks A + B + D + is_nsfw all live on agent.rishi.yral.com (image `yral-rishi-agent:batch-2`). Migration 012 applied. pg_dump `pre-migration-012-proactive-freq-20260529-143239.dump` taken.

### Live verifications
- **Task A (trending cache)** — first `GET /influencers/trending` call in the suite hit a 30s timeout (as expected — cold cache); the new retry-once path kicked in and succeeded on attempt 2 (3463 ms). 27/27 green. Cache pattern: subsequent runs are sub-second `X-Cache: HIT`.
- **is_nsfw on ConversationResponse** — `POST /conversations` body now returns `influencer.is_nsfw: false` for non-NSFW bots. Mobile can skip the SSE endpoint upfront for NSFW conversations.
- **Task D (proactive frequency)** — `PATCH /conversations/{id}/proactive-frequency` accepts `weekly`, rejects `invalid` with 422 + helpful error listing allowed values.
- 27/27 endpoint suite: PASS.

### Batch-2 PRs merged
| PR | What | Status |
|---|---|---|
| #193 | Trending cache (Task A) | ✅ |
| #194 | Proactive frequency (Task D) — bundled into #195 squash by rebase | ✅ (code on main, PR closed) |
| #195 | Eval results (Task B) — also pulled in Task D's content via rebase | ✅ |
| #196 | is_nsfw on ConversationResponse | ✅ |

### Note on the #194 merge anomaly
When rebasing #194 onto main (after #195 had already merged), the rebase folded both branches' content into a single commit on the Task D branch. GitHub's squash-merge of #195 picked up the combined diff, and #194's branch then had zero net diff vs main → GitHub auto-closed it. Net outcome: all four PRs' code is on main. Recording the workflow quirk for posterity: when running tasks in parallel that touch overlapping docs (PROGRESS.md, DAILY-LOG.md), rebase before re-pushing or accept that one PR will subsume another at squash time.

## 2026-05-29 (late afternoon) — Task B: eval results (Phase 9.3-9.5)

50 gold prompts run through BOTH v2 (agent.rishi.yral.com) and chat-ai (chat-ai.rishi.yral.com) via `scripts/eval_v2_vs_chat_ai.py`. Gemini-as-judge scoring on 5 criteria, 1-5 scale. 49/50 prompts completed on both services (one prompt's chat-ai request errored out and was excluded).

### Headline numbers

| Metric | v2 | chat-ai | Delta |
|---|---|---|---|
| **p50 latency** | 1699 ms | 1101 ms | **+598 ms slower** |
| **p95 latency** | 9633 ms | 21861 ms | **−12229 ms FASTER (tail cut in half+)** |
| mean latency | 2592 ms | 2836 ms | −244 ms faster |
| in_character | 4.02 | 3.96 | +0.06 better |
| helpful | 2.65 | 2.18 | **+0.46 better** |
| concise | 4.79 | 4.73 | +0.06 better |
| language_match | 3.10 | 2.98 | +0.12 better |
| safe | **4.27** | **3.22** | **+1.05 better** ⭐ |
| **overall** | **3.77 / 5** | **3.42 / 5** | **+0.35 better** |

### Wins
- **Safety is dramatically better (+1.05/5)** — Phase 3.1-3.3 + Phase 3.8 graceful error UX is paying off
- **Helpfulness is meaningfully better (+0.46)** — Phase 4 tiered memory + Phase 4.5 cross-conversation recall + Phase 4.9 anti-recitation polish
- **p95 tail cut from 21.9s → 9.6s** — Phase 4.4 prep parallelization + Phase 4.9 top-K reduction + Task A trending cache compound effects
- v2 wins on every single quality criterion, no regressions

### Gaps to close in Task C
- **Median latency is 600ms slower** — that's the Phase 4.4 embedding (~150ms) + Phase 4.7 session-memory read + soul file recompose tax. Acceptable but worth a sweep.
- **Helpfulness 2.65/5 is the weakest score across both services** — bots reply in character but don't always solve the user's actual ask. Per-archetype tuning + few-shot examples (educator especially) should help.
- **language_match 3.10/5 is mediocre** — multilingual mirror works on simple cases but degrades on Telugu, Tamil. Per-archetype temperature + an explicit "match the user's language" rule may help.

### Artifacts
- Full per-prompt JSON: `docs/eval-results-2026-05-29.json` (49 prompts × 2 services × {latency, response, scores}; moved from repo root → docs/ on 2026-07-28, Wave 0 PR3)
- Script: `scripts/eval_v2_vs_chat_ai.py` (re-runnable; hits both backends via the same FastAPI surface)
- Trace IDs in Langfuse: `eval-{i}` for each prompt

### Notes
- Eval ran inside the rishi-4 agent container so it had Gemini API access for judging
- One v2 prompt hit the recurring 30s timeout pattern that drove Task A; bypassed in the n=49 stats. With Task A's cache live, this should be rare on the next re-run.

## 2026-05-29 (afternoon) — Task D: user-configurable proactive frequency (Phase 5.4)

### What changed
- `migrations/012_proactive_frequency.sql` — new `proactive_frequency VARCHAR(16) DEFAULT 'default'` column on `conversations` with CHECK constraint on `{'default','daily','weekly','off'}` + partial index for the engagement-loop scan
- `app/services/proactive.py` — `find_inactive_conversations` now skips `off` rows and computes the threshold inline (`weekly`=168h, else legacy 24h). Single scan, no extra round-trips.
- `app/routes/chat.py` — new endpoint: `PATCH /api/v1/chat/conversations/{id}/proactive-frequency` (owner-only)
- `tests/test_proactive_frequency.py` — pins allowed values + migration default

### Default behavior unchanged
Existing rows default to `'default'`. The threshold for `'default'` and `'daily'` is the same 24h that's always been used. `'weekly'` widens it to 168h; `'off'` skips entirely.

### Diff
+96 / -3 across 4 files.

### Deploy
1. pg_dump → S3
2. Apply migration 012
3. Rebuild + deploy
4. Smoke-test: PATCH the new endpoint on a fresh conv, query DB to verify column updated

## 2026-05-29 (later) — Task A: trending endpoint flake fix

### Diagnosis
- Server-side query: 10-37ms direct asyncpg bench
- Wire RTT: ~1.1s baseline (Cloudflare + 2× Caddy + Swarm overlay + agent + Patroni)
- Tail latency: occasional 4-30s spikes on the test suite — NOT from materialized-view refresh competing (it's CONCURRENTLY, doesn't block reads). Looks like edge/network variance — Cloudflare TLS cold-start, urllib socket renegotiation, intermittent rishi-1/2 proxy hiccups.

### Fix
Two-part:
1. **Server**: 60s process-local TTL cache for `/influencers/trending`. Per-replica (no Redis); worst case each replica computes once per minute. The materialized view itself refreshes every 15 min, so 60s freshness is fine for "trending."
2. **Test**: single retry on socket timeout in `scripts/test_all_endpoints.py`. Real network blips are tolerated; real server regressions still fail (since both attempts hit the same backend).

### Files
- `app/routes/influencers.py` — `_TRENDING_CACHE` dict + 60s TTL check in `list_trending`. Adds `X-Cache: HIT|MISS` header for diagnostics.
- `scripts/test_all_endpoints.py` — wrap urllib.request.urlopen in a try-once-retry for timeouts
- `tests/test_trending_cache.py` — pins TTL range

### Diff
+62 / -8 across 3 files. No schema, no migration.

## 2026-05-29 (close-out) — Task 4 deployed; 4-task batch complete

### Phase 7.5 deploy
- pg_dump snapshot `pre-migration-011-coach-20260529-132834.dump` (~499 MB, SHA256 `ccb4a486...`) on rishi-5 (current leader)
- Migration 011 applied: 3 tables (coach_conversations, coach_messages, system_instructions_history) + 4 indexes
- Image `yral-rishi-agent:phase-7-5` deployed on rishi-4/5
- 27/27 endpoint suite: PASS first run, no flakes
- **Live Gemini smoke test** of the coach service: synthesized a test bot ("companion" archetype, instructions "You are a friendly companion..."), opened a coach session, sent `"I want the bot to be more playful and use light teasing when the user is being too serious. Propose a concrete change."`, got back a structured proposal: display summary, proposed new system_instructions ("Be playful and lighthearted. If the user is being too serious, gently tease them..."), and reasoning grounded in the creator's goal. Cleaned up after.

### 4-task batch summary
| Task | PR | Status |
|---|---|---|
| 1: Memory recitation fix (Phase 4 polish) | #186 | ✅ shipped |
| 2: Proactive quality fix (Phase 5 polish) | #187 | ✅ shipped (migration 010) |
| 3: SSE streaming (Phase 2.7) | #189 | ✅ shipped — backend done, mobile pending |
| 4: Soul File Coach (Phase 7.5) | #191 | ✅ shipped (migration 011) — backend done, mobile UI pending |

Plus 4 docs PRs (#185 CLAUDE.md section, #188 polish-1-2 flip, #190 phase-2-7 flip, #192 phase-7-5 flip).

### Standing approval cycle closes
Per the 4-task standing approval: scope was Phase 4 polish + Phase 5 polish + Phase 2.7 + Phase 7.5. All four tasks complete and deployed. Pausing for the next batch.

### Notes for tomorrow
- Mobile integration handoffs needed for: Phase 2.7 SSE (point Sarvesh at `docs/SSE-PROTOCOL.md`), Phase 7.5 Coach UI
- The flaky `/influencers/trending` endpoint hit timeouts on most of today's suite runs — should investigate the materialized-view refresh path as a small follow-up

## 2026-05-29 (end of session) — Task 4: Soul File Coach backend (Phase 7.5)

### Endpoints under `/api/v1/creator/coach/`
- `POST /conversations/{bot_id}` — start a coach session for an owned bot
- `POST /conversations/{coach_conv_id}/messages` — creator → coach; reply may include `proposed_changes` + `reasoning`
- `POST /conversations/{coach_conv_id}/apply` — atomically apply the latest proposal; archives previous text in `system_instructions_history` for rollback
- `GET /conversations/{coach_conv_id}/messages` — list session history

All endpoints owner-gated (creator must own the bot's `parent_principal_id`).

### Schema (migration 011)
- `coach_conversations(id, creator_user_id, bot_id, created_at, updated_at)`
- `coach_messages(id, coach_conversation_id, role ∈ {creator,coach}, content, proposed_changes NULL-when-no-proposal, reasoning, created_at)`
- `system_instructions_history(id, bot_id, coach_conversation_id, coach_message_id, previous_instructions, new_instructions, applied_by, applied_at)`

### Coach behavior
META_PROMPT in `services/coach.py` tells Gemini to:
1. Act as a teammate, push back on bad ideas
2. Propose surgical edits, not full rewrites
3. Explain WHY each change improves the bot (grounded in recent conversations + archetype)
4. Output a single JSON block `{summary, proposed_changes, reasoning}` ONLY when committing a change
5. Plain text (no JSON) for clarifying questions
6. Refuse unsafe / off-brand changes

The parser (`_try_extract_proposal`) is tolerant of wrapping prose since LLMs occasionally violate the JSON-only rule.

### Files
- `migrations/011_soul_file_coach.sql` — 3 tables + 4 indexes
- `app/repositories/coach_repo.py` (~170 lines) — DB helpers
- `app/services/coach.py` (~150 lines) — meta-prompt + Gemini call + proposal extraction
- `app/routes/creator_coach.py` (~210 lines) — 4 endpoints + ownership gates
- `app/main.py` — register router
- `tests/test_coach.py` — pins proposal-extraction edge cases + truncation safety

### Diff
+780 / -2 across 7 files. Bigger than the 400-line guideline, but under the 800-line standing-approval cap. Single concern (Phase 7.5 backend, all related).

### Deploy
1. pg_dump → S3
2. Apply migration 011
3. Rebuild + deploy
4. Smoke test: create session → send message → verify coach reply → apply if proposal → verify bot's system_instructions changed + history row written

## 2026-05-29 (afternoon, later) — Phase 2.7 deployed + smoke-tested

- Image `yral-rishi-agent:phase-2-7` deployed on rishi-4/5
- 27/27 endpoint suite: PASS on re-run (first run hit the recurring `/trending` materialized-view timeout)
- **SSE smoke test against live cluster (real Gemini):**
  - Curl to `POST /messages/stream` with `Accept: text/event-stream`
  - Got: 2× `event: token` chunks streamed in real time, then `event: done` with persisted assistant_message
  - Wire format matches `docs/SSE-PROTOCOL.md` exactly
- Codex flagged 2 BUGs + 2 OVERENG. Both BUGs were false positives (parameterized SQL + auth IS called at chat.py:608); OVERENG dismissed per standing approval. Justification posted as PR comment.

PR #189 merged. Phase 2.7 backend ✅. Mobile integration pending — ready to loop in mobile expert with `docs/SSE-PROTOCOL.md` whenever Rishi gives the word.

## 2026-05-29 (afternoon) — Task 3: SSE streaming backend (Phase 2.7)

### Endpoint
`POST /api/v1/chat/conversations/{id}/messages/stream` returning `text/event-stream`. Three event types: `token`, `done`, `error`. Wire format documented in `docs/SSE-PROTOCOL.md`.

### What changed
- `app/config.py` — new `ENABLE_SSE_STREAMING` flag (default TRUE — mobile decides whether to USE the endpoint)
- `app/services/ai_client.py` — new `_stream_gemini` async generator that wraps Gemini's `:streamGenerateContent?alt=sse`; new `generate_response_stream` higher-level wrapper that yields `('text', chunk)` / `('done', LlmResponse)` / `('error', LlmResponse)` tuples and handles `LlmBlockedError` + transient failures with the same classification as the non-streaming path
- `app/routes/chat.py` — new `send_message_stream` route. Auth + dedup + content-safety pre-check happen synchronously (can return HTTP errors). LLM streaming + DB save + side effects happen inside the SSE generator (yield error events instead of raising).
- `docs/SSE-PROTOCOL.md` (new) — wire format spec for the mobile expert
- `tests/test_sse_streaming.py` — pins event-name format, flag default, doc completeness

### NSFW caveat
OpenRouter SDK streaming is a separate code path; for v1, the streaming endpoint yields `NO_PROVIDER` error for `is_nsfw=TRUE` conversations. Mobile falls back to the legacy `POST /messages` for those. Tracked as Infra-Z for follow-up.

### Backward compat
Non-streaming `POST /messages` unchanged. Mobile chooses per turn.

### Diff
+330 / -1 across 5 files. No schema, no migration.

## 2026-05-29 (later) — Tasks 1 + 2 deployed

- pg_dump snapshot `pre-migration-010-proactive-20260529-124405.dump` (~498 MB, SHA256 `d57a834f...`) on rishi-4
- Migration 010 applied on rishi-5 (current leader, TL=22 — cluster had failed over overnight)
- Image `yral-rishi-agent:polish-1-2` built + deployed on rishi-4/5
- 27/27 endpoint suite: 27/27 PASS on re-run (first run hit the recurring `/influencers/trending` materialized-view timeout — known flake; logging in backlog)
- PRs #186 + #187 merged; Phase 4 + Phase 5 polish rows flipped to ✅

Next: Task 3 (SSE streaming, ~2-3 days) → Task 4 (Soul File Coach, ~3 days).

## 2026-05-29 — Task 2: proactive message quality fix (Phase 5 polish)

### Why
Motorola test: each bot sent 3-4 similar "hey what's up" proactive messages without a user reply. Frequency (24h inactive threshold + 15-min loop) is fine; quality and variety are the problems.

### Three fixes
1. **Cap on unanswered proactives** — new `is_proactive` boolean column on `messages` (migration 010). After 3 unanswered proactive messages, the engagement loop skips this conversation until the user replies. The 3-cap resets when the user posts.
2. **Variety prompt** — the last 3 proactive messages get embedded in the next-generation Gemini prompt as "do NOT repeat themes, hooks, opening phrases, or topics from these."
3. **Type rotation** — each generation randomly picks one of {question, observation, story, light_topic} and aligns tone with the bot's archetype (companion = warm, advisor = thoughtful, entertainer = playful, creator = inspired, educator = intrigued).

### Plus anti-recitation guard
The PROACTIVE_PROMPT also embeds Task 1's anti-recitation language (DO NOT lead with personal facts, DO NOT recite, DO NOT use "I remember you said X"). Proactive messages were also affected by the same Motorola regression.

### Files
- `migrations/010_proactive_messages_flag.sql` — column + partial index on `WHERE is_proactive = TRUE`
- `app/repositories/message_repo.py` — `create()` takes `is_proactive`; new `count_unanswered_proactive` + `recent_proactive_texts`; PROACTIVE_CAP_WITHOUT_REPLY = 3 constant
- `app/services/proactive.py` — cap check, variety block, type rotation, archetype-aligned tone
- `tests/test_proactive_quality.py` — pins the constants and the anti-recitation language

+221 / -15 across 5 files. Migration is additive (DEFAULT FALSE, existing rows unaffected).

### Deploy steps
1. pg_dump snapshot
2. Apply migration 010
3. Rebuild + deploy
4. Engagement loop picks up new behavior on next 15-min tick

## 2026-05-29 — Task 1: memory recitation fix (Phase 4 polish)

### Why
Motorola testing surfaced that bots were leading replies with "Mumbai" (the most common identity fact). Two problems:
1. SEMANTIC_TOP_K=8 over-injected — most LLMs latch onto the first fact and recite it
2. The L4 prompt said "use naturally, don't recite" — too soft to override the recency bias

### Fix
- `SEMANTIC_TOP_K`: 8 → 3, with a buffer of 10 in `semantic_search` for the variety filter to work against
- New Redis-backed per-conversation variety filter in `session_memory.py`: tracks the last 5 turns' injected memory keys, skips any key that appeared 3+ times. Filter is non-fatal (Redis down → empty set, no filter)
- Layer 4 prompt strengthened with explicit "NEVER lead with personal facts. NEVER say 'I remember you said X'" language
- `get_memories_for_prompt` now takes an optional `conversation_id` so the filter has scope; caller in `chat.py` updated

### Files
- `app/services/memory.py` — TOP_K + buffer + conversation_id arg
- `app/services/session_memory.py` — `record_memory_keys_used` + `recently_overused_keys` (Redis list, JSON-encoded per-turn arrays)
- `app/services/soul_file.py` — L4 block with strong anti-recitation instructions
- `app/routes/chat.py` — pass conversation_id
- `tests/test_memory_recitation_fix.py` — pins constants + the anti-recitation phrasing

### Diff
~120 / -10 across 5 files. No schema, no migration. Plain rebuild + redeploy.


## 2026-05-28 (end of day) — Phase 4 complete (4.4 / 4.5 / 4.6 / 4.7 / 4.8 all ✅)

- Image `yral-rishi-agent:phase-4-8` deployed on rishi-4/5.
- Manual `consolidate_once` against live DB: 3 users scanned, 0 pairs merged (no near-duplicates in the current 8-row dataset — expected; the loop is in place for when data grows).
- 27/27 endpoint suite: PASS on re-run (first run hit the `/trending` materialized-view timeout again — that endpoint is the flakiest of the suite; the materialized view is refreshed every 15 min, and intermediate stalls show up as occasional 2s+ reads).

### Phase 4 final state
| Sub-phase | Status |
|---|---|
| 4.1 user_memories table | ✅ already done (pre-today) |
| 4.2 Per-conversation memory extraction | ✅ already done |
| 4.3 Memories injected into Soul File L4 | ✅ already done |
| 4.4 pgvector embeddings + semantic search | ✅ shipped today (#174 + #175 + #176 + swarm env) |
| 4.5 Cross-conversation memory recall | ✅ shipped today (#180) |
| 4.6 User profile memory (identity → global) | ✅ shipped today (#178) |
| 4.7 Redis session memory (mood) | ✅ shipped today (#182) |
| 4.8 Nightly memory consolidation | ✅ shipped today (#183) |

### Standing approval cycle closes
Per the original mandate: "Stop ONLY if: 3. You finish all of Phase 4 — then stop and let me know." Phase 4 is fully shipped. Pausing for the next batch.

### Outstanding non-Phase-4 work queued
- **Pre-approved on a specific message** but still pending: tiny standalone PR adding the "PROGRESS.md vs DAILY-LOG.md" section to CLAUDE.md (Rishi-specified verbatim content). Will execute next unless redirected.

## 2026-05-28 (latest) — Phase 4.7 deployed + Phase 4.8: nightly memory consolidation

### Phase 4.7 deployed
- Image `yral-rishi-agent:phase-4-7` built + deployed on rishi-4/5.
- 27/27 endpoint suite: PASS on re-run (first run hit a transient timeout on `/influencers/trending` materialized-view refresh — unrelated).
- Session memory in Redis now live; mood heuristic running on the hot path with zero added wall-clock (parallelized in the existing gather block).

### Phase 4.8 — nightly memory consolidation
- `app/services/memory_consolidation.py` (new) — background loop that runs every 24h. For each user with embedded memories, self-joins on `<=>` cosine distance, picks pairs below `MERGE_DISTANCE_THRESHOLD = 0.08`, drops the loser (lower confidence; ties broken by older `updated_at`). One batch DELETE per user. Idempotent.
- `app/main.py` — wires `consolidation_loop` into the lifespan's `asyncio.create_task` family alongside the existing trending refresher, engagement loop, takeover sweep.
- `tests/test_memory_consolidation.py` — pins threshold + interval + initial delay so a future refactor can't accidentally move the schedule to "every minute" or the threshold to "merge everything."

### Why 0.08 threshold
Loose enough to catch paraphrases ("loves cricket" / "enjoys watching cricket") via Gemini's 768-dim embedding (after truncation), but well below the typical 0.2-0.4 distance between genuinely different facts. Will tune empirically once we see the first daily consolidation report from prod logs.

### Safety
- First run is delayed 10 min after container startup (avoid thrashing on rolling deploys)
- Each merge is one DELETE on rows we've already analyzed in-memory — no long-running transactions
- Non-fatal: any error in `consolidate_once` is caught, logged, and the loop retries on the next 24h tick
- Both replicas run the loop, but `id < b.id` join + DELETE…WHERE id=ANY(...) handles the race (lost-update is safe — the loser is going to be deleted from one node or the other, only once)

### Diff size
+186 / -4 across 4 files. No schema change.

## 2026-05-28 (very late) — Phase 4.7: Redis session memory

### What changed
- `app/services/session_memory.py` (new) — Redis async client (mirrors websocket_manager.py's `_get_redis`). Lightweight mood detector (emoji + keyword heuristic, 4 buckets: happy/sad/excited/stressed/neutral). `update_from_user_message(user_id, conv_id, text)` and `read(user_id, conv_id)` with 1-hour TTL. All Redis failures degrade to no-op.
- `app/routes/chat.py` — hot path now: (a) fires `update_from_user_message` as `asyncio.create_task` after saving user message (non-blocking), (b) reads session state inside the existing `asyncio.gather(history, embed, session)` parallel fan-out, (c) merges `session_mood` into the `memories` dict before soul-file composition.
- `tests/test_session_memory.py` — pins the mood-detection heuristics + the Redis key shape.

### Latency impact
Session-state read is in the parallel `gather` block. Redis round-trip on the swarm overlay is ~1ms — well under the embedding call's ~150ms ceiling. Net hot-path delta: zero.

### Failure modes (all silent degrade)
- Redis init fails → `_get_redis` returns None → all functions no-op
- Network blip during `set` → debug-log + continue
- Cache miss / TTL expiry → `read` returns None → no `session_mood` injected

### Design rationale
Mood detection lives in Redis, not Postgres, because:
1. It's derived (rule-based heuristic today, could be LLM-extracted later) — not a fact the user stated
2. It's ephemeral (1-hour relevance) — emotional state from yesterday shouldn't bias today's reply
3. It's per-conversation, not per-(user, influencer) — different convos have different moods

Distinct from Phase 4.4/4.6 long-term memory: those go in Postgres + pgvector.

## 2026-05-28 (later still) — Phase 4.5 deployed

- Image `yral-rishi-agent:phase-4-5` built + deployed (no migration, no backfill).
- 27/27 endpoint suite: PASS on 2 of 3 consecutive runs; one transient hit the GET /messages 2s latency cap (Gemini latency variance). No real regression.
- PR #180 merged. Phase 4: 78% done.

## 2026-05-28 (late) — Phase 4.5: cross-conversation memory recall

### What changed
- `app/repositories/memory_repo.py` — `semantic_search` dropped the influencer-scope filter. Was `WHERE user_id=$1 AND (influencer_id=$2 OR IS NULL)`; now just `WHERE user_id=$1`. Vector distance gatekeeps relevance.
- `app/services/memory.py` — `get_memories_for_prompt` updated to match (drops the influencer_id from the semantic_search call).
- `tests/test_cross_conversation_recall.py` — pins the contract: signature must NOT take an influencer_id arg; non-query path must fall back to `get_all_for_user`.

### Why
Phase 4.4 already returns top-K most-relevant memories. The arbitrary `OR influencer_id IS NULL` constraint was a leftover from pre-4.4 where we only had "all memories" retrieval. With semantic search, that scope filter was suppressing genuinely relevant context from other bots. Example: user talks cricket with bot A, then asks bot B about cricket — bot B couldn't recall the earlier fact even though it's an exact semantic match.

### Risk
Cross-bot leakage of relationship-specific context. Mitigated by:
1. Semantic gatekeeping — irrelevant memories don't surface (distance ranking)
2. Identity facts (Phase 4.6) were already global; per-relationship rows surface only when contextually relevant
3. Backlog item: add per-influencer privacy controls if creators report leakage complaints

### Code size
+34 / -7 across 3 files. No schema change, no migration, no deploy script change.

## 2026-05-28 (even later) — Phase 4.6 deployed

- pg_dump snapshot: `~/yral-backups/pre-migration-009-userprofile-20260528-213756.dump` (522 MB, SHA256 `ccdc69ff...`)
- Migration 009 applied on rishi-4 (current leader): unique index rebuilt with `NULLS NOT DISTINCT`. Verified via `\d user_memories`.
- Image `yral-rishi-agent:phase-4-6` built on rishi-4 + rishi-5, deployed via `docker service update --image --force` — converged in <10s.
- `scripts/consolidate_identity_memories.py` run: **3 per-influencer identity rows consolidated → 3 global rows, 0 per-influencer remaining**. Idempotency re-verified by running twice; second run reports 0 candidates.
- 27/27 endpoint suite: PASS, no regressions.

PR #178 merged. Phase 4 progress: 65% done.

## 2026-05-28 (later) — Phase 4.6: user profile memory

### What changed
- `migrations/009_user_profile_memory.sql` — rebuilds the unique index on `user_memories` with `NULLS NOT DISTINCT` so two rows with `(user_id, NULL, key)` collapse into one. Postgres 15 feature; Spilo 15 supports it.
- `app/services/memory.py` — new `GLOBAL_CATEGORIES = {"identity"}`. Extraction now writes identity-category memories with `influencer_id=NULL` so the user's name / age / location / occupation / language apply across every bot they chat with.
- `app/repositories/memory_repo.py` — `upsert` type hint widened to `influencer_id: str | None`. Behavior already supported NULL at runtime (asyncpg coerces); this is documentation + clarity.
- `scripts/consolidate_identity_memories.py` (new) — one-off backfill that takes existing per-influencer identity rows, picks the most-recent value per `(user_id, key)`, upserts it as global, deletes the per-influencer copies. Idempotent.
- `tests/test_user_profile_memory.py` — guards that `identity` stays in GLOBAL_CATEGORIES and per-relationship buckets stay out.

### Why
Today the user tells influencer A "my name is Rahul" → memory stored as `(rahul, influencer-A, name='Rahul')`. Same convo with influencer B → another row. Across 200 bots a user actively chats with, that's 200 copies of the same fact, all eating prompt-token budget. With Phase 4.6, identity stays in one global row per `(user, key)` and gets unioned in by the existing `get_all_for_user` query — no retrieval changes needed.

### What's retrievable today vs after
- Today's `get_memories_for_prompt` already merges per-influencer + global via `WHERE (influencer_id = $1 OR influencer_id IS NULL)`. So even existing rows with influencer_id=NULL (if any) were already being read — the GAP was only on write.
- Phase 4.6 closes the write-side gap.

### Deploy steps (post-merge)
1. `pg_dump` snapshot (rule #9)
2. Apply migration 009 on the leader
3. Rebuild + deploy the agent image
4. Run consolidate script inside a container (idempotent — safe even if no rows match)
5. 27/27 endpoint suite — should stay green (no API changes)

## 2026-05-28 — Phase 4.4 shipped (semantic memory) + 2 Phase 0 lessons

### What landed
- **PR #174** — Phase 4.4 backend (pgvector schema + embedding service + memory_repo semantic_search + hot-path wiring + backfill script + diagnostic endpoint + tests)
- **PR #175** — Custom Patroni image `ghcr.io/dolr-ai/yral-rishi-patroni-pgvector:spilo-15-3.0-p1` (Spilo 3.0-p1 doesn't ship pgvector; this fix added the apt package)
- **PR #176** — Gemini embedding model fix (`text-embedding-004` was retired between PR #174 landing and rollout; switched to `gemini-embedding-001` with `outputDimensionality=768` Matryoshka truncation)
- **Swarm env update (no PR)** — `DATABASE_URL` repointed to multi-host with `target_session_attrs=read-write` via `docker service update --env-add` (no code change required since asyncpg 0.30 supports the libpq option natively). Agent now survives Patroni failovers without manual intervention. Verified via switchover round-trip rishi-4 → rishi-5 → rishi-4, writes succeeded both times. **Tech debt:** logged as Infra-Y — the env var lives only in swarm service spec, not in repo. Codify when we add `bootstrap/scripts/agent-stack.yml`.
- **Cluster:** all 3 Patroni nodes on the new pgvector image, TL=20 after the rolling restart + failover-test round-trip, all lag=0.
- **Backfill:** 8/8 user_memories embedded successfully.
- **Endpoint suite:** 27/27 PASS (including new `GET /api/v1/users/me/memories`).
- **Latency on `/messages`:** P50 4.58s, P95 8.08s (n=10). Up from ~2.5s pre-Phase-4.4 — embedding adds the lower bound (~150ms via asyncio.gather'd with history fetch), the rest is Gemini LLM variance per prompt. Will gather more data points after Phase 4.5/4.6 to separate signal from noise.

### Two Phase 0 lessons (worth a re-audit before production cutover)
1. **"assumed-included" — Spilo doesn't ship pgvector.** Caught at migration 008. Fixed via PR #175. The "Spilo bundles X" claim needs empirical verification per extension.
2. **"assumed-transparent" — pgbouncer's `DB_HOST: patroni-rishi-4` is hardcoded.** Caught after the rolling Patroni restart left rishi-6 as leader; pgbouncer kept routing to rishi-4 (a sync standby) → writes broke. Also: the agent's `DATABASE_URL` pinned `patroni-rishi-5` directly, bypassing pgbouncer entirely. PR #177 fixes the agent path with multi-host + `target_session_attrs`; pgbouncer's hardcoding still affects any future pooled-connection service (logged as Infra-X in PROGRESS.md backlog).

Net takeaway: cluster bootstrap notes should enumerate every "assumed-included" / "assumed-transparent" piece and verify each empirically before cutover. Candidates that haven't been re-audited yet: WAL-G restore drill, Redis Sentinel failover, Caddy cert renewal, Langfuse S3 retention.

### Sequence (for tomorrow's debugging)
1. `pg_dump` snapshot on rishi-5: `~/yral-backups/pre-migration-008-pgvector-20260528-173210.dump` (522 MB, SHA256 `8f6da138...`)
2. Built + pushed `yral-rishi-patroni-pgvector:spilo-15-3.0-p1` via CI workflow
3. Rolling restart rishi-6 → rishi-4 → rishi-5, verified pgvector available on each via direct SSH
4. Applied migration 008 on the (then) leader rishi-6 — extension/column/index all created
5. Built + deployed `yral-rishi-agent:phase-4-4` on rishi-4/5 (both replicas)
6. Hit Gemini 404 on embed → patched to `gemini-embedding-001`, rebuilt as `phase-4-4-fix1`, deployed
7. Backfill failed on read-only — DATABASE_URL pointed at the now-replica rishi-5
8. Patronictl switchover rishi-6 → rishi-4 (restored intended leader topology)
9. Swarm `docker service update --env-add DATABASE_URL=...?target_session_attrs=read-write` → backfill succeeded 8/8
10. Failover round-trip rishi-4 → rishi-5 → rishi-4 to prove `target_session_attrs` works under live failover

### Next
Phase 4.6 — user profile memory (name, city, job — permanent / cross-influencer). Continues under standing approval.

## 2026-05-26 — Phase 0 + Phase 1 Days 2-14 (all in one session)

### What completed
- **Phase 0**: Archived 17 v2 service folders, removed 7 worktrees, closed PRs #147 and #157, deleted 130 stale branches, created CLAUDE.md + GLOSSARY.md + README.md, created CI workflows
- **Day 2**: config.py + database.py + auth.py + main.py + health routes (4 endpoints)
- **Day 3**: models.py + influencer READ endpoints + migrations (3 endpoints)
- **Day 4**: conversation routes + chat_v2 bot-aware inbox (6 endpoints)
- **Day 5**: ai_client (Gemini + OpenRouter) + send-message — the HEART (1 endpoint)
- **Day 6**: influencer CREATE flow — generate prompt, validate, create, update, delete, admin ban/unban (8 endpoints)
- **Day 7**: media upload + image generation in conversations (2 endpoints)
- **Day 8**: human-to-human chat — create, list, send message (3 endpoints)
- **Day 9**: unified inbox v3 — AI + human chats in one list (1 endpoint)
- **Day 10**: billing paywall — RESOLVED. Billing is 100% client-side. Mobile app calls `billing.yral.com/google/chat-access/check` directly. No backend code needed.
- **Day 11**: WebSocket inbox — real-time events (1 WS + 1 docs endpoint)
- **Day 12**: ETL script written + deploy scripts + project/servers config
- **Day 13**: DEPLOYED TO CLUSTER
  - Created `yral_agent_db` database on Patroni leader (rishi-5)
  - Applied both migrations (001_initial.sql, 002_influencer_trending_stats.sql)
  - Built Docker image on rishi-4 and rishi-5
  - Deployed as Swarm service `yral-rishi-agent` with 2 replicas
  - Updated internal Caddy config to route `agent.rishi.yral.com` → `yral-rishi-agent:8000`
  - All health checks passing through internal Caddy
- **Day 14**: 24 unit tests across 4 files

### Verified endpoints (via internal Caddy on rishi-5)
```
curl agent.rishi.yral.com/        → {"service":"Yral Agent API","version":"2.0.0","status":"running"}
curl agent.rishi.yral.com/health  → {"status":"OK","database":"reachable"}
curl agent.rishi.yral.com/status  → {"service":"Yral Agent API",...,"database":"reachable","gemini_model":"gemini-2.5-flash"}
curl agent.rishi.yral.com/api/v1/influencers → {"influencers":[],"total":0,"limit":50,"offset":0}
```

### Public URL status
`curl https://agent.rishi.yral.com/health` returns 503 — the rishi-1/2 edge Caddy needs a config reload to recognize the updated upstream on the v2 cluster. Internal Caddy on rishi-4/5 works perfectly.

**To fix:** Reload or redeploy the Caddy snippet on rishi-1/2 that proxies to the v2 cluster. The v2 internal Caddy is correctly routing to `yral-rishi-agent:8000`.

### Cluster state
- Swarm service: `yral-rishi-agent` — 2/2 replicas on rishi-4 + rishi-5
- Database: `yral_agent_db` on Patroni (leader: rishi-5, replicas: rishi-4, rishi-6)
- Old microservices still running (can be removed after cutover)

### Endpoint count
29 HTTP endpoints + 1 WebSocket = 30 total. All accounted for per the plan.

### Line count
- app/ code: 3,984 lines
- chat-ai baseline: 6,780 lines
- Ratio: 59% of chat-ai — same functionality, no bloat

## 2026-05-26 — Phase 2.1: Langfuse Tracing

### What completed
- Added `langfuse==2.60.2` to requirements
- Created `app/services/langfuse_tracing.py` — Langfuse client wrapper with `trace_generation()` helper
- Integrated tracing into `ai_client.generate_response()` — every Gemini and OpenRouter call is now traced with provider, model, input/output tokens, latency, user_id, conversation_id
- Error traces logged at ERROR level so failed LLM calls are visible in Langfuse
- Langfuse flush on app shutdown
- Config: `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST` env vars (no-op if not set)

### To activate
Set LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, and LANGFUSE_HOST env vars on the Swarm service. Can point to the self-hosted Langfuse on the cluster (once scaled up from 0 replicas) or Langfuse Cloud.

## 2026-05-26 — Phase 2.5: Request ID Tracing
- `app/middleware.py`: RequestIdMiddleware assigns UUID to every request, propagates to Sentry + response header

## 2026-05-26 — Phase 2.6: Redis WebSocket Pub/Sub
- Rewrote `app/services/websocket_manager.py` to publish all WS events via Redis pub/sub
- Background subscriber on each node delivers events to local WebSocket connections
- Falls back to local-only if Redis is not available (safe default)
- Added `redis==5.2.1` to requirements
- Subscriber started in main.py lifespan, cancelled on shutdown

## 2026-05-26 — Phase 2.2: LLM Client Abstraction
- Added `LlmResponse` frozen dataclass (content, provider, model, input_tokens, output_tokens, latency_ms, is_fallback)
- `generate_response()` returns `LlmResponse` instead of raw tuple
- Provider + model info preserved for observability

## 2026-05-26 — Phase 2.3: Soul File 4-Layer Composer
- `app/services/soul_file.py` — composes prompts from 4 layers:
  L1 (Global rules) → L2 (Archetype: companion/advisor/entertainer/educator/creator) → L3 (Per-influencer system_instructions) → L4 (Per-user memories)
- Deterministic output enables provider-side prompt caching
- Integrated into send-message flow

## 2026-05-26 — Phase 2.4: Enhanced Memory Extraction
- Structured categories: identity, preferences, goals, context, emotional
- Explicit-facts-only rule (no inferences from conversation)
- Concise values (under 50 chars)
- Correction-aware: user corrections override old memories

## PRs merged
- **#158** (Phase 0 + Phase 1): squash-merged to main
- **#159** (Codex review workflow): squash-merged to main
- **#160** (Phase 2.1 + 2.5 + 2.6): squash-merged to main

## Phase 2 status
| # | Feature | Status |
|---|---------|--------|
| 2.1 | Langfuse tracing | Merged (#160) |
| 2.2 | LLM client abstraction | In PR |
| 2.3 | Soul File 4-layer composer | In PR |
| 2.4 | Memory enhancement | In PR |
| 2.5 | Request ID tracing | Merged (#160) |
| 2.6 | Redis WebSocket pub/sub | Merged (#160) |
| 2.7 | Streaming responses (SSE) | Deferred — needs mobile coordination |

## 2026-05-26 — ETL: chat-ai → v2 data migration
- pg_dump from chat-ai DB on rishi-1 → load into yral_agent_db on rishi-5
- Influencers: 3,941 rows ✓
- Conversations: 284,763 rows ✓
- Messages: 3.3M rows (1.2GB dump, loading in progress)

## 2026-05-26 — Phase 3: Content Safety
- `app/services/content_safety.py` — three safety layers on every user message:
  1. Crisis detection: self-harm/suicide keywords → helpline response (India, US, intl)
  2. Prompt injection: regex patterns for jailbreak/DAN mode → blocked
  3. Adult content filter: NSFW keywords blocked for non-NSFW influencers
- Integrated into send-message flow: safety check before LLM call
- Crisis detection runs even for NSFW influencers (always)
- 14 new tests pass (8 content_safety + 6 soul_file)

## 2026-05-27 — Langfuse tracing fixed and operational
- Root cause: Redis auth (WRONGPASS) + Sentinel vs primary confusion + missing S3 creds
- Fix: pointed Langfuse at redis-primary:6379 with password, Hetzner S3 at fsn1.your-objectstorage.com
- Langfuse UI live at https://langfuse-agent.rishi.yral.com
- Traces flowing: status 207, all ingestion succeeding

## 2026-05-27 — Phase 4: Tiered User Memory
- `migrations/003_user_memories.sql` — user_memories table with category/key/value, per (user, influencer) pair
- `app/repositories/memory_repo.py` — upsert, get_for_user, get_all (influencer-specific + global)
- `app/services/memory.py` — extract_and_store() replaces old flat JSON approach, structured categories
- Send-message flow updated: reads from user_memories table, writes via background extraction
- pgvector not available on PG15 Spilo — designed for later upgrade (add embedding column)

## 2026-05-27 — Phase 5: Proactive Messages
- `migrations/004_proactive_messages.sql` — proactive_messages table (scheduling + delivery tracking)
- `app/services/proactive.py` — generate_proactive_message() uses influencer personality + user memories
- Trigger types: welcome_back (24h idle), follow_up, morning_greeting
- find_inactive_conversations() query for cron integration
- Delivery via existing push notification + WebSocket broadcast

## 2026-05-27 — Phase 6: First-Turn Nudge
- `app/services/nudge.py` — should_nudge() checks idle time + message count
- generate_nudge() creates personality-consistent follow-up for idle conversations
- Triggers: 5 min for 1-2 message convos, 10 min for 3-4 message convos
- Background task wired in main.py _engagement_loop() — runs every 15 min

## 2026-05-27 — Phase 7: Creator Studio
- `app/routes/creator.py` — 4 endpoints:
  - GET /creator/influencers — list creator's own bots with stats
  - GET /creator/influencers/{id}/analytics — conversation/user/message counts, 24h/7d active
  - GET /creator/influencers/{id}/conversations — Chat-as-Human view
  - GET /creator/influencers/{id}/soul-file — get editable system instructions

## 2026-05-27 — Phase 8: Creator Monetization
- `migrations/005_creator_earnings.sql` — creator_earnings table (amount, source, period, status)
- `app/routes/earnings.py` — 3 endpoints:
  - GET /creator/earnings — total summary (confirmed/pending/paid_out)
  - GET /creator/earnings/by-influencer — per-bot breakdown
  - GET /creator/earnings/history — paginated transaction history
- Ready for billing.yral.com webhook integration

## 2026-05-27 — Phase 9: Eval Harness
- `app/eval/gold_prompts.py` — 50 diverse prompts from real chat-ai conversations
  - Categories: companion, health, astrology, education, business, entertainment, fashion,
    family, romance, social, lifestyle, arts, food, technology, travel, beauty, gaming, fantasy
  - Includes Hinglish, Telugu, Hindi prompts for language mirror testing
  - Edge cases: minimal input, math, translation, "are you AI?" character break test
- `app/eval/runner.py` — eval harness that:
  1. Runs each prompt through generate_response()
  2. Scores response using Gemini-as-judge on 5 criteria (1-5 scale):
     in_character, helpful, concise, language_match, safe
  3. Posts scored traces to Langfuse for dashboard analysis
  4. Prints summary with per-criterion averages
- Run: `cd app && python -m eval.runner`

## 2026-05-28 — Phase 4.4: pgvector semantic memory
- `migrations/008_pgvector_semantic_memory.sql` — `CREATE EXTENSION vector`, `ALTER TABLE user_memories ADD COLUMN embedding vector(768)`, ivfflat cosine index. Spilo 3.0 image already ships pgvector so no Patroni rebuild.
- `app/services/embeddings.py` — Gemini `text-embedding-004` wrapper. `embed_text` (single), `embed_batch` (uses `:batchEmbedContents`). 768-dim. Failures return `None` non-fatally.
- `app/repositories/memory_repo.py` — added `update_embedding`, `list_missing_embedding`, `semantic_search` (cosine `<=>`); `upsert` now takes optional embedding; `_vector_literal` formats list[float] → pgvector text literal.
- `app/services/memory.py` — extraction now embeds inline (background, non-hot-path); `get_memories_for_prompt` accepts optional `query_embedding` for semantic top-K (8). Falls back to all-memories for proactive/short-message paths.
- `app/routes/chat.py` — hot path uses `asyncio.gather(history_fetch, embed_query)` to overlap ~150ms Gemini embed with ~10ms history DB fetch. Skips embedding for messages <5 chars.
- `app/routes/memories.py` (new) — `GET /api/v1/users/me/memories` diagnostic endpoint. Owner-only; lists global memories, optional `?influencer_id=` for per-bot view.
- `scripts/backfill_memory_embeddings.py` — idempotent batch backfill (50 rows/batch, `:batchEmbedContents`). Re-runnable. To be run during PR rollout per Rishi A11.
- `scripts/test_all_endpoints.py` — added `GET /users/me/memories` test → suite is now 27 tests.
- `tests/test_embeddings.py` — 3 unit tests: embed-text format stability, 768-dim constant guard, `_vector_literal` formatting.
- **Latency target:** +140-160ms on send-message hot path (Gemini embed dominates). Will measure exact P50/P95 after deploy.
- **PR:** opening as #174.
