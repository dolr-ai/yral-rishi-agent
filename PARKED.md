# Parked

Everything we stopped mid-way and intend to pick up. **One file, on purpose** —
PROGRESS.md and DAILY-LOG.md were deleted on 2026-09-11 because nobody read
them and keeping them current cost more than it returned. The history lives in
git log and in merged PR descriptions, which are written anyway.

Rules: add a row when you park something, delete the row when it's done.
If a row has been here three months untouched, it isn't parked — it's declined.
Delete it.

---

## Needs Rishi

| What | Why it's blocked | Context |
|---|---|---|
| **Rotate object-storage + Postgres credentials** | In progress, Rishi's action | Exposed in a session transcript 2026-09-11 (a wrapped shell command dumped the container env). `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` are read+write on the whole backup store; `PGPASSWORD_SUPERUSER`/`_STANDBY` also exposed. Procedure: `docs/runbooks/secret-rotation.md` |

## Known problems, not yet fixed

| What | Detail |
|---|---|
| **1 FK violation in production** | The weekly restore drill reports `fk_violations=1` — orphan rows in `yral_agent_db`. Harmless to backups, but it's real inconsistency. See `~/yral-backups/drill.log` on rishi-4 |
| **93 WAL segments permanently lost** | Timeline 61, from the 2026-09-09 → 09-11 archiving outage. Point-in-time recovery inside that window is gone for good. Nightly `pg_dump`s still cover it at daily granularity. Nothing to fix — recorded so nobody re-investigates |
| **`weekly-security-drill` fails every Sunday** | Fails at step 13, *"Open / update tracking issue"* — a token permission. The gitleaks/pip-audit/Trivy scans all pass. Six weeks of an alarm about the wrong thing |
| **`walg-restore-drill.yml` never runs** | It's `workflow_dispatch`-only, last run 2026-06-11. The *real* restore drill is the cron on rishi-4 (`backup_restore_drill.sh`, Sundays 04:30) which passes. Either wire the workflow to a schedule or delete it |
| **Reboot drift moves swarm replicas** | Ansible reboots a node weekly; Swarm never rebalances back, so replicas pile onto whichever box stayed up. Found when all 3 edge Caddy replicas ended up on rishi-5. `backup-monitor.sh` and the cert monitor now detect the symptom; the cause needs a post-reboot rebalance step in the Ansible flow |
| **Trivy scans the previous image** | It runs on push to main against the published `:stable` tag, but the deploy that publishes the new image runs *after* CI. So a CVE is found only after it's live |
| **`build-and-push` / `Trivy` can't gate** | Push-only, so they can't be required checks. Five PR-time checks are required on `main`; these two are advisory by construction |

## Planned work, sized

| What | Why | Size |
|---|---|---|
| **HTTP + real-DB test harness** | The single biggest lever on quality. `routes/` is 22% covered — the only layer a user reaches, and where every bug in Sept lived. Only 2 of 145 test files drive real HTTP; 5 touch a real Postgres. Pattern to copy: `tests/test_validate_generate_null_reason.py` (drives the real route, fails on the unfixed code). Start with `influencers.py` | weeks |
| **Convert source-text tests by risk** | 535 assertions grep our own source and pass whether or not the code works. Ratcheted by `scripts/ci/check_source_text_assertions.py` so it can only fall. Target `chat.py` (15%), `influencers.py` (20%), `creator_coach.py` (9%) — ~120 assertions that matter more than the other 400 | weeks |
| **Unwind #501's plain-default conversions** | #504 collapses `anyOf:[T,null]` at publication, so distorting Python types is no longer needed. Each remaining conversion is a latent copy of the Sentry #602 bug (a `None` at runtime against a non-nullable model → 500) | days |
| **Absorb Ansuman's service** | Specs already in the repo history from 2026-08-08 | — |
| **Billing: absorb ownership, do NOT rewrite** | 8,277 lines of Rust implementing a working ledger. Decided 2026-09-10 against a Python rewrite: Rust's compile-time guarantees and exact-decimal discipline are load-bearing for money, and a reconciliation bug costs real cash. Take over deploys/monitoring/on-call instead. If a rewrite ever happens, run both in shadow with daily reconciliation | — |

## Decided, so we stop re-litigating

- **Billing is not being rewritten in Python.** See above.
- **0% coverage is not evidence of dead code.** `variant_repo.py` reads 0% and has 7 call sites — they're lazy in-function imports, so the module never loads under test. A real dead-code scan (`vulture --min-confidence 80`) finds almost nothing; this codebase is live code that is under-tested, not bloated with corpses.
- **Every merge deploys**, docs-only included. `workflow_run` triggers cannot have a path filter.
