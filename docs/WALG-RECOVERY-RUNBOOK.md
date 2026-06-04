# WAL-G recovery runbook

**Status (2026-06-04 07:35 UTC):** ✅ **WAL-G is LIVE** — streaming to `s3://rishi-yral/yral-rishi-agent-walg/`. First base backup (1.27 GB) + 5+ WAL segments archived, `failed_count=0`.

This runbook covers both:

1. **What was done to enable WAL-G** (the catch-up after Phase 0 shipped without it)
2. **How to restore from WAL-G** (PITR or full cluster reinit) — for use during a real incident

---

## What was actually done (2026-06-04)

The original Phase 0 bootstrap created `/run/secrets/hetzner-s3-access-key-id` containing the literal 25-byte placeholder `walg-disabled-placeholder` (per `bootstrap/scripts/patroni-install.sh:508-510` when `YRAL_PATRONI_WAL_G_ENABLED=false`). `archive_command` was `/bin/true`. No WAL segments were being archived.

The actual enablement was done via direct `docker service update` on the running patroni stack (no bootstrap re-run needed):

1. **Rotated 2 new docker secrets** containing the real Hetzner Object Storage credentials — read from the existing `chat_ai_s3_credentials` secret (which has the same Hetzner access key), piped through stdin to `docker secret create` so the values never landed in shell history:
   - `yral_v2_hetzner_s3_access_key_id_walg_20260604`
   - `yral_v2_hetzner_s3_secret_access_key_walg_20260604`

2. **Rolling restart of all 3 patroni services** with the new secrets + WAL-G env vars (`USE_WALG_BACKUP=true`, `WALG_S3_PREFIX=s3://rishi-yral/yral-rishi-agent-walg`, `AWS_REGION=hel1`, `AWS_ENDPOINT=https://hel1.your-objectstorage.com`, `AWS_S3_FORCE_PATH_STYLE=true`, `USE_WALE_S3_BACKUP=true`). Order: sync-standby (rishi-4) → async replica (rishi-5) → leader (rishi-6, last). Leader failover happened cleanly during rishi-6's restart; cluster reconverged on TL=25.

3. **First failure caught:** WAL-G returned `NoCredentialProviders: no valid providers in chain.` Spilo's `configure_spilo.py` writes `/run/etc/wal-e.d/env/AWS_ACCESS_KEY_ID` from **env vars at container startup**, not from `/run/secrets/` files. The secrets were mounted but never consumed.

4. **Fix:** parallel `docker service update --env-add AWS_ACCESS_KEY_ID=... --env-add AWS_SECRET_ACCESS_KEY=...` on all 3 nodes. Credentials piped through ssh stdin from a running patroni container — never exposed to shell history. Triggered a second rolling restart sequence; cluster reconverged on TL=26 (leader is now rishi-5).

5. **Verified streaming:** Spilo's `postgres_backup.sh` auto-fired the first base backup on startup. By 07:35 UTC:
   - `archived_count=8 failed_count=0`
   - `s3 ls --recursive`: 16 objects, 1.2 GiB (base backup + 5 WAL segments + history file + backup label)

### Security tradeoff

`AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` are now in the service env (visible via `docker service inspect`). Same blast radius as `cat /run/secrets/...` on the manager node, but slightly worse than the file-only path. Long-term improvement: change `bootstrap/scripts/patroni-stack.yml` to mount the secrets with `target=/run/etc/wal-e.d/env/AWS_ACCESS_KEY_ID` directly (envdir style). Tracked as follow-up; not blocking.

---

## Step 1 — Provision Hetzner Object Storage

If a WAL bucket doesn't exist yet:

1. Hetzner Cloud Console → **Object Storage** → **Create Bucket**
2. Name: `yral-v2-postgres-walg` (or whatever you prefer — fed into `YRAL_HETZNER_S3_WAL_BUCKET_NAME` below)
3. Location: `hel1` (same Hetzner location the cluster runs in — matches the existing `yral-profile.hel1.your-objectstorage.com` pattern for avatars)
4. Access policy: **Private** (default)
5. Hetzner Cloud Console → **Security** → **Access Keys** → **Create Access Key**
   - Scope: limit to the bucket above if Hetzner offers per-bucket keys; otherwise account-wide is fine for v2
   - Save **Access Key ID** + **Secret Access Key** — only shown once

Verify with the AWS CLI or `mc` (MinIO client):

```bash
export AWS_ACCESS_KEY_ID=<the-key>
export AWS_SECRET_ACCESS_KEY=<the-secret>
aws s3 ls --endpoint-url=https://hel1.your-objectstorage.com s3://yral-v2-postgres-walg
# expect: empty listing, no error
```

---

## Step 2 — Re-run patroni bootstrap with WAL-G enabled

From `bootstrap/scripts/patroni-install.sh` preamble, the 5 env vars required:

```bash
export YRAL_PATRONI_WAL_G_ENABLED=true
export YRAL_HETZNER_S3_ACCESS_KEY_ID=<from step 1>
export YRAL_HETZNER_S3_SECRET_ACCESS_KEY=<from step 1>
export YRAL_HETZNER_S3_WAL_BUCKET_NAME=yral-v2-postgres-walg
export YRAL_HETZNER_S3_REGION=hel1
export YRAL_HETZNER_S3_ENDPOINT=https://hel1.your-objectstorage.com

# Plus the production-mode flag if you want CONSTRAINTS D2 enforced
export YRAL_PATRONI_PRODUCTION_MODE=true

cd ~/yral-rishi-agent  # or wherever the repo is on rishi-deploy
sudo -E bash bootstrap/scripts/patroni-install.sh
```

What this does (per `patroni-install.sh:497-503`):

1. Rotates `hetzner-s3-access-key-id` + `hetzner-s3-secret-access-key` swarm secrets to the real values (SHA-8 suffix rotation per `create_or_rotate_swarm_secrets_with_sha8_suffix`)
2. Renders `WALG_S3_PREFIX=s3://yral-v2-postgres-walg/yral-v2-postgres`
3. Sets `USE_WALG_BACKUP=true` + `USE_WALG_RESTORE=true` + `USE_WALE_S3_BACKUP=true`
4. `docker stack deploy` rolls patroni-rishi-4, -5, -6 onto the new env

**Rolling restart order:** Docker Swarm restarts services per-replica with the stack-file's `update_config.order` setting. For the patroni stack (replicas: 1 per node, pinned by hostname), it'll restart all three approximately simultaneously which is wrong for a Patroni cluster. **MANUAL workaround:** if you want strict leader-last, drain non-leader replicas first via `patronictl pause` + restart each individually.

For the FIRST enable, since the cluster is already running fine, a simultaneous restart is acceptable — Patroni's etcd lease will reconverge on a leader within ~10s of the kafkaesque restart.

---

## Step 3 — Verify first WAL segment streams

After the stack converges (`patronictl list` shows all 3 running):

```bash
# Inside any patroni container:
docker exec patroni-rishi-4 wal-g backup-list 2>&1
# Initially shows nothing — no base backup yet

# Trigger the first base backup explicitly so we don't wait for the
# scheduled cron inside Spilo:
docker exec patroni-rishi-6 envdir /run/etc/wal-e.d/env wal-g backup-push /home/postgres/pgdata/pgroot/data
# expect: "Wrote backup with name base_..."

# Verify WAL archiving is now active:
docker exec patroni-rishi-6 psql -U postgres -c "SELECT pg_walfile_name(pg_current_wal_lsn());"
# expect: a WAL segment name like 000000180000000A0000004F

# After ~30 seconds the next archive_timeout cycle should fire:
docker exec patroni-rishi-6 envdir /run/etc/wal-e.d/env wal-g wal-show --backup-name LATEST 2>&1 | head -5
# expect: WAL segments listed
```

If `wal-g` reports `NoSuchBucket` or `InvalidAccessKeyId` — credentials wrong, rotate again.
If `wal-g` reports `AccessDenied` — bucket exists but the key lacks Write permission.

---

## Step 4 — Restoring from WAL-G

### Scenario A: PITR ("restore to 10 minutes before the disaster")

Patroni doesn't expose PITR directly; the procedure is:

1. Provision a new empty Postgres node (or wipe an existing one — `rm -rf /home/postgres/pgdata/pgroot/data`).
2. Set Spilo env vars `WALG_S3_PREFIX` + `USE_WALG_RESTORE=true` + `WAL_RESTORE_COMMAND='envdir /run/etc/wal-e.d/env wal-g wal-fetch %f %p'` (already set if Step 2 was done correctly).
3. Create a `recovery.conf` (PG <12) or `standby.signal` + `postgresql.auto.conf` overrides (PG >=12) with:
   ```
   recovery_target_time = '2026-06-04 04:30:00 UTC'
   recovery_target_action = 'promote'
   ```
4. Restart Postgres. It'll restore the latest base backup before the target time, then replay WAL segments up to that point, then promote.
5. Verify with `SELECT pg_is_in_recovery();` — should return `false` after promotion.

### Scenario B: full cluster reinit (e.g. all 3 nodes lost)

1. Bring up rishi-4 with `docker compose up patroni-rishi-4` — it'll see no etcd cluster, pull the latest base backup from WAL-G, replay WAL, become leader.
2. Bring up rishi-5 + rishi-6 — they'll join via the normal `patronictl reinit` path (basebackup from rishi-4).

---

## Step 5 — Drift signal to monitor

Once WAL-G is running, watch for:

- `archive_command` failures in Spilo logs (`docker service logs yral-v2-patroni_patroni-rishi-6 | grep -i archive`). Repeated failures = S3 unreachable or credentials wrong. If the failure persists, WAL segments will pile up on disk and eventually fill `/data/patroni-data` → cluster halt.
- `pg_stat_archiver.failed_count` in Postgres:
   ```sql
   SELECT * FROM pg_stat_archiver;
   ```
   Healthy: `failed_count` is 0 and `last_archived_time` is recent.

---

## Once WAL-G ships

This document gets a "last updated" + which step was followed in production. The I10 backup-restore drill (`bootstrap/scripts/backup_restore_drill.sh`) should be repointed at `wal-g backup-fetch LATEST` instead of `pg_restore` of the nightly dump — that's the real cutover-grade drill.

I11 (offsite S3) becomes meaningful once primary WAL-G is producing — that's "duplicate the same WAL stream to a second bucket with a different access key, ideally in a different Hetzner location."

## Memory linkage

- `project_walg_disabled_in_production.md` — the finding entry; should be marked RESOLVED once WAL-G lands.
- `BACKUP-RESTORE-DRILL-2026-06-04.md` — the stopgap I10 that exists until WAL-G ships.
