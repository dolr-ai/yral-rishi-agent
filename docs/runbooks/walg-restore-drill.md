# WAL-G restore drill — runbook

**Phase:** 21αβ.H6 (PROD BLOCKER)
**What it proves:** WAL-G can actually restore the V2 Patroni cluster from the Hetzner Object Storage backup. Until this drill passes, "we have backups" is theory.

**How to trigger:** Actions tab → "WAL-G restore drill (PROD BLOCKER H6)" → Run workflow → pick target host + reason + type `RUN WAL-G DRILL`.

**Expected runtime:** 5-15 min (depends on base backup size + how many WAL segments need replaying).

---

## What the drill does (under the hood)

1. SSH to target host (rishi-4, rishi-5, or rishi-6 — operator picks).
2. `docker exec` into the running patroni container on that host. That container already has wal-g, postgres binaries, and the WAL-G S3 credentials mounted via Spilo's standard env vars.
3. Inside the container, fetch the LATEST base backup into `/tmp/walg-drill-<timestamp>/`. **Never** touches `/home/postgres/pgdata/pgroot` (live data dir).
4. Configure a sidecar postgres in that directory:
   - `recovery.signal` file → puts it into archive recovery
   - `restore_command = 'wal-g wal-fetch %f %p'` → pulls each WAL segment via wal-g
   - Custom port 5433 (live Patroni stays on 5432)
   - Custom unix socket dir (no socket conflict)
5. Start the sidecar postgres. Wait up to 5 min for WAL replay to catch up (`pg_is_in_recovery()` returns false).
6. Sanity queries against the sidecar:
   - `COUNT(*)` on users, ai_influencers, conversations, messages — must each exceed minimum
   - `MAX(created_at)` on messages — must be within last 7 days (proves the backup + WAL are fresh)
7. Stop the sidecar, `rm -rf` the drill dir.

**Safety properties:**
- Live data dir is untouched (sidecar reads its own copy in `/tmp`)
- Live Patroni stays on port 5432 (sidecar on 5433)
- No DCS writes — sidecar doesn't join etcd
- Read-only against the S3 bucket — no `wal-g backup-push` or `wal-g wal-push`

---

## What "GREEN" looks like

Workflow exits 0; final summary step prints:

```
─── WAL-G restore drill summary ───
Target:   rishi-6 (162.55.88.112)
Exit:     0
Verdict:  GREEN — restore mechanism proven end-to-end
```

The drill log itself shows row counts:

```
[walg-drill 2026-06-11T15:00:00Z] row counts:
[walg-drill 2026-06-11T15:00:00Z]   users          = 12345
[walg-drill 2026-06-11T15:00:00Z]   ai_influencers = 4521
[walg-drill 2026-06-11T15:00:00Z]   conversations  = 12027
[walg-drill 2026-06-11T15:00:00Z]   messages       = 478359
[walg-drill 2026-06-11T15:00:00Z]   latest message epoch = 1749654000 (2026-06-11 14:00:00)
[walg-drill 2026-06-11T15:00:00Z] ─── drill PASSED ───
```

When GREEN, record the timestamp in DAILY-LOG.md so the cutover checklist (H6) can be marked done.

---

## When the drill fails — diagnosis by exit code

| Exit | Class | What to check |
|---|---|---|
| **1** | Prereqs missing on target | Is the patroni service running on the target host? `docker ps -f name=yral-v2-patroni_patroni`. Are env vars set? `docker exec <CID> env grep WALG_S3_PREFIX`. |
| **2** | wal-g backup-fetch failed | (a) Hetzner S3 credentials drifted — check `/run/secrets/walg-credentials` inside the container; (b) bucket changed — verify `WALG_S3_PREFIX` matches the live setup at `s3://rishi-yral/yral-rishi-agent-walg/`; (c) network — confirm the container can reach `hel1.your-objectstorage.com`. |
| **3** | Sidecar postgres failed | Check the startup log on the container: `docker exec <CID> cat /tmp/walg-drill-<ts>/startup.log`. Common causes: WAL replay couldn't find segments (gaps in archive); permissions on `/tmp` dir; postgresql.conf in the base backup references files that don't exist in the drill dir. |
| **4** | Sanity queries showed missing data | The restore + replay finished but the data isn't there. Likely the backup is corrupt or the WAL stream has gaps. **This is the genuinely scary failure** — it means the safety net isn't safe. Open a P0 incident immediately. |
| **5** | Drill passed but cleanup messy | The verification was successful but the teardown of the sidecar postgres or the `/tmp/walg-drill-<ts>` dir didn't fully clean up. SSH to the target host, `docker exec <CID> ls /tmp/walg-drill-*` to find leftover dirs, `rm -rf` them. Postgres process may still be listening on 5433 — `docker exec <CID> pg_ctl -D <DIR> stop -m immediate`. Not a backup-safety issue, just an operator cleanup. |

---

## Cadence

- **Pre-cutover (today / this week):** run once manually to mark H6 complete.
- **Weekly post-cutover:** run via the workflow. Eventually move to cron-on-schedule once we've seen multiple successful runs (no schedule today by design — we want each run to be operator-attended until trust is established).
- **Before any major migration or DB change:** run before AND after, so we know the backup chain remains intact across schema events.
- **After a real incident or restore:** run to confirm the safety net is still there.

---

## Why this is a PROD BLOCKER

The 2026-06-04 re-bootstrap showed how painful "figure out the mechanism during the incident" can be. WAL-G is supposed to be our PITR safety net, but until we've actually exercised the restore path, we don't know:

- Whether credentials are wired correctly
- Whether the bucket policy lets us read backups (vs just write)
- Whether the wal-g binary in the patroni image actually works against our setup
- Whether WAL segments are contiguous (no gaps from earlier outages)

A real incident is not the place to discover any of those.

---

## Related

- `scripts/walg_restore_drill.sh` — the bash script the workflow invokes
- `.github/workflows/walg-restore-drill.yml` — the workflow itself
- `scripts/backup_restore_drill.sh` — the older pg_dump-based drill (different code path, complementary safety net)
- `bootstrap/scripts/patroni-stack.yml` — defines the WAL-G env vars (`WALG_S3_PREFIX`, `AWS_*`) that the drill inherits
