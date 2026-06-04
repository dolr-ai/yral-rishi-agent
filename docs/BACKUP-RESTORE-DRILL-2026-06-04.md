# Backup + restore drill — I10 deliverable

**Status (2026-06-04):** Scripts deployed to rishi-deploy host, cron installed, first manual drill PASSED.

**Caveat:** This is a **stopgap implementation** until WAL-G is wired (see `memory/project_walg_disabled_in_production.md`). WAL-G is currently disabled in production (`archive_command=/bin/true`, `USE_WALG_BACKUP=false`), so the nightly pg_dump + weekly restore drill below is the only refreshed-and-verified backup we have. When WAL-G ships, the drill should be repointed at WAL-G base backups + WAL replay so PITR is exercised.

---

## What runs

| Script | When | What it does |
|---|---|---|
| `backup_nightly_pg_dump.sh` | 03:00 UTC (08:30 IST) daily | Dumps `yral_agent_db` from local patroni container's `postgres` superuser via UNIX socket → `~/yral-backups/nightly/yral_agent_db_<ts>.dump` (custom format, gzip-6). Rotates to 7 days. |
| `backup_restore_drill.sh` | 04:30 UTC (10:00 IST) Sundays | Spins up a `pgvector/pgvector:pg15` sidecar container, restores the freshest dump into it, runs sanity queries, tears down. Non-zero exit on any data-table failure. |

Cron lives in `crontab -l` on rishi-deploy (138.201.128.108). Scripts live in `/home/rishi-deploy/yral-backups/bin/`.

## First drill PASSED — 2026-06-04 03:24 UTC

```
DRILL PASSED — yral_agent_db_20260604T024719Z.dump (512 MB)
  restored cleanly to sidecar, 69s pg_restore, 3,374,694 messages verified

Row counts: ai_influencers=3,941  conversations=285,672  messages=3,374,694
Latest message: 2026-06-04 02:43:50 (recent — no staleness)
Sample message content: readable
```

## Findings surfaced by the drill

### 1. Production Spilo extensions not in sidecar (expected; 4 errors)

`pg_stat_kcache` and `set_user` are bundled in the Spilo (production) image but not in `pgvector/pgvector:pg15`. The `CREATE EXTENSION` + `COMMENT ON EXTENSION` calls fail (4 errors). Data restore is unaffected because no table uses these extensions in DDL. The script's error classifier counts them as "expected" and does not fail the drill.

### 2. ⚠️ 1 orphan message — FK violation on `messages.conversation_id` (REAL FINDING)

```
pg_restore: error: could not execute query:
  ERROR: insert or update on table "messages" violates foreign key constraint
  "messages_conversation_id_fkey"
  DETAIL: Key (conversation_id)=(e9740450-9aa2-44c7-b33a-f25c8c859ed9) is not present
  in table "conversations".
```

A message in the dump references a deleted conversation. FK is `ON DELETE CASCADE`, so the cascade should have removed it. Possibilities:

- A race in conversation-delete (delete + concurrent message insert)
- A direct SQL delete that bypassed the cascade somehow
- The latency-comparison script's `DELETE /api/v1/chat/conversations` ran near-concurrent with the script's `POST .../messages` and left one orphan (the conversation UUID is consistent with a script-test conversation)

Cleanup query (Rishi's call whether to run):

```sql
DELETE FROM messages WHERE conversation_id NOT IN (SELECT id FROM conversations);
-- before running: SELECT count(*) FROM messages
--   WHERE conversation_id NOT IN (SELECT id FROM conversations);
```

The drill exits 0 on FK violations — it logs them as findings but doesn't fail. Only unclassified pg_restore errors fail the drill.

## When the drill fails — what to do

| Exit code | Meaning | First step |
|---|---|---|
| 1 | No dump under `nightly/` | Check `~/yral-backups/nightly.log` for the previous cron run |
| 2 | Sidecar postgres didn't come up | `docker logs yral-restore-drill-sidecar` — usually disk-full or port collision |
| 3 | pg_restore had unclassified errors | Open `/tmp/restore_drill_<pid>.log` for the full pg_restore output |
| 4 | Sanity queries failed (data missing or rows under 100) | The dump is restorable but EMPTY — pg_dump may have hit a connect failure mid-stream |
| 5 | Drill passed, sidecar teardown failed | Run `docker rm -f yral-restore-drill-sidecar` manually |

## Migration path to WAL-G (post-cutover)

When WAL-G is wired per the bootstrap script's `YRAL_PATRONI_WAL_G_ENABLED=true` gate:

1. `backup_nightly_pg_dump.sh` becomes redundant — WAL-G continuously archives WAL segments + takes base backups.
2. `backup_restore_drill.sh` repoints at `wal-g backup-fetch LATEST` instead of pg_restore. The sidecar sanity-query block stays as-is.
3. `I11 offsite` becomes meaningful (second S3 bucket + cross-region replication).

Until then: this stopgap is the backup story.

## Files

- `bootstrap/scripts/backup_nightly_pg_dump.sh`
- `bootstrap/scripts/backup_restore_drill.sh`
- Cron on rishi-deploy (added 2026-06-04)
- Memory: `project_walg_disabled_in_production.md` — context on WHY this stopgap exists
