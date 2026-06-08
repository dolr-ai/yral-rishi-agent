# ETL Ops Runbook — chat-ai → V2 via S3

How the continuous chat-ai → V2 ETL works, how to investigate when it
goes sideways, and what knobs to turn.

## Architecture

```
rishi-1 (chat-ai swarm)                  Hetzner S3                          rishi-4/5 (V2)
─────────────────────────                ────────────────────                ──────────────────
cron */5 * * * *                         rishi-yral/                         yral-rishi-agent
~/.etl-export/incremental_export.py        yral-chat-ai/                       (ETL fetcher +
  ↓                                          incremental-sync/                  integrity
  docker exec patroni → psql               ↓                                    verifier loop)
  COPY (SELECT * WHERE                       *_<table>.csv.gz                     ↓
   created_at > cursor                       _heartbeat                          /admin/etl-*
   AND created_at <= NOW() - 1m)             STUCK                                endpoints
  ↓                                          _integrity/
  gzip + boto3.put_object                      tick_*.json
                                               hourly_*.json
                                               sample_*.json
                                               sentinel_*.json
```

Why this shape: chat-ai's Postgres lives on a swarm overlay
(`chat-ai-db-internal`) and isn't reachable from rishi-4/5/6. So
chat-ai-side reads happen on rishi-1 (inside that swarm), the deltas
are pushed to S3, and V2 pulls from S3.

## Tables

| Table | Purpose |
|---|---|
| `etl_sync_state` | Per-table cursor (last_sync_ts), display only. Selection logic uses `etl_processed_files` instead. |
| `etl_processed_files` | One row per S3 file V2 has applied. `filename` PK = idempotent re-application. |
| `etl_skipped_rows` | One row per skipped INSERT (Option A audit log). `(filename, table, row_id, reason)` UNIQUE. |
| `etl_integrity_results` | One row per S3 integrity payload V2 has verified. `snapshot_filename` UNIQUE. |
| `etl_integrity_checks` | Legacy from pre-S3-pivot integrity. Not currently written. Keep for historical reads. |

## Cadences

| Component | Period | Notes |
|---|---|---|
| rishi-1 exporter cron | 5 min | Always emits CSV deltas + tick integrity. Hourly/sample/sentinel gated by elapsed time. |
| V2 ETL fetcher loop | 5 min | Polls S3 since `MAX(processed_at)`. INITIAL_DELAY 60s. |
| V2 integrity loop | 5 min | Polls `_integrity/` since `MAX(verified_at)`. INITIAL_DELAY 10 min. |
| Hourly integrity emit | 60 min | Full row counts per table where `created_at < NOW() - 10 min`. |
| Sample integrity emit | 6 h | 20 random conversations + per-message SHA-256 content hash. |
| Sentinel integrity emit | 30 min | Latest message/conversation IDs. |

## Option A: skip duplicate/orphaned rows

chat-ai allows multiple conversations per `(user_id, influencer_id)`
pair; V2's schema enforces at most one
(`idx_unique_user_influencer` UNIQUE WHERE influencer_id IS NOT NULL).
chat-ai also has `idx_unique_human_chat` we don't.

**The decision:** when ETL re-imports a chat-ai conversation whose
`(user, influencer)` pair already exists in V2, we **skip** the
import. V2's existing conversation stays canonical. Subsequent
chat-ai recreations (a user deleted their conversation and started a
new one) are also skipped — respecting V2's "one active conversation
per pair" UX and the user's intent (deleted = deleted).

Messages whose parent conversation didn't land in V2 (either skipped
above or never existed) are recorded as `orphan` skips. Audit trail
in `etl_skipped_rows`.

**Health thresholds:**
- `skipped_rows_24h < 50/day` — fine. Race conditions + rare delete-recreate.
- `skipped_rows_24h > 500/day` — revisit. Likely revisit Option B (remap) or a schema-level discussion.

## Admin endpoints

All JWT-gated.

| Endpoint | Use |
|---|---|
| `GET /admin/etl-status` | Per-table cursors, files processed 24h, rows applied 24h, skip counts by reason+table, heartbeat freshness, STUCK marker |
| `GET /admin/etl-integrity` | Latest result per layer (tick/hourly/sample/sentinel) + 24h pass/fail counts |
| `GET /admin/etl-integrity/details?layer=X&hours=N` | Drill-in to a specific layer's recent results |
| `GET /admin/etl-integrity/stale` | chat-ai latest vs V2 latest + lag_sec |
| `GET /admin/etl-skipped?hours=N&reason=conflict\|orphan` | Recent etl_skipped_rows entries (capped 500) |

## How to investigate

### Symptom: `heartbeat_stale: true` on `/admin/etl-status`

rishi-1 cron isn't publishing. Check:
```
ssh deploy@138.201.137.181
crontab -l | grep incremental_export        # entry present?
tail -50 ~/.etl-export/etl-export.log       # last run's output
ls -la ~/.etl-export/                       # state.json + credentials there?
```

### Symptom: `stuck_marker` is non-null on `/admin/etl-status`

rishi-1 cron has had 3+ consecutive failures. Read the marker:
```
mc ls s3/rishi-yral/yral-chat-ai/incremental-sync/STUCK     # exists
mc cat s3/rishi-yral/yral-chat-ai/incremental-sync/STUCK    # last_error in metadata
```
Clear after fix:
```
mc rm s3/rishi-yral/yral-chat-ai/incremental-sync/STUCK
rm ~/.etl-export/consecutive_failures  # on rishi-1
```

### Symptom: V2 stops applying (V2 logs show `failed after Xms` repeatedly)

Check container logs:
```
ssh rishi-deploy@138.201.128.108
docker logs --since 10m $(docker ps --format '{{.Names}}' | grep '^yral-rishi-agent\.' | head -1) 2>&1 | grep etl_chat_ai
```
Look for the actual exception type after `failed after Xms:`. Common ones:
- `UniqueViolationError` on a constraint other than `idx_unique_user_influencer` → schema mismatch we don't handle yet; investigate.
- `ForeignKeyViolationError` with non-conversations target → schema add we missed.
- `AmbiguousParameterError` → param-type bug; raise a fix PR.

### Symptom: `skipped_rows_24h > 500`

Pull breakdown:
```
GET /admin/etl-skipped?hours=24&reason=conflict
```
If the conflict count for `conversations` is dominant, chat-ai is
seeing high `(user, influencer)` recreation volume — investigate
whether users are deleting+restarting unusually often (chat-ai bug?)
or whether the Option A boundary is wrong for the use case.

If `orphan` for messages dominates without matching conversation
conflicts, something else is dropping conversation rows. Investigate
recent migrations on chat-ai.

### Symptom: `/admin/etl-integrity` shows non-zero `fail_count_24h`

```
GET /admin/etl-integrity/details?layer=hourly&hours=24
```
Look at `details.per_table.diff` for the failing tables. Drift > 0 by
roughly the skip count is **expected** (skipped rows produce row-count
drift). Drift much larger than skip count = real problem.

## How to pause / resume

### Pause rishi-1 publication
```
ssh deploy@138.201.137.181
crontab -l > /tmp/crontab.bak
crontab -l | grep -v incremental_export | crontab -
```
V2 keeps applying whatever's already in S3, then idles when caught up.

### Pause V2 consumption
```
ssh rishi-deploy@138.201.128.108
docker service update --secret-rm chat_ai_s3_credentials yral-rishi-agent
```
V2's loop continues running but `_load_s3_credentials()` returns None
and the loop logs "credentials not mounted" and idles. rishi-1 keeps
publishing into S3 (cheap — small files; 30-day Hetzner lifecycle
will reap them).

### Re-mount V2 credentials
```
ssh rishi-deploy@138.201.128.108
docker service update --secret-add chat_ai_s3_credentials yral-rishi-agent
```
Backlog catches up over the next few ticks.

### Re-bootstrap rishi-1 cursor (DANGER)
Only if you want to re-export from an earlier date. Touches state.json:
```
ssh deploy@138.201.137.181
cat > ~/.etl-export/state.json <<EOF
{
  "ai_influencers": "2026-05-01T00:00:00+00:00",
  "conversations":  "2026-05-01T00:00:00+00:00",
  "messages":       "2026-05-01T00:00:00+00:00",
  "_last_hourly_emit": null,
  "_last_sample_emit": null,
  "_last_sentinel_emit": null
}
EOF
```
ON CONFLICT semantics make re-application a no-op for already-present
rows, but the first tick after a cursor-rewind may produce a very
large CSV. Watch `/admin/etl-status` for `heartbeat_age_sec` growing.

## Re-export a missed window manually

If you discover a window's CSV failed to upload (e.g., S3 outage):
```
ssh deploy@138.201.137.181
python3 ~/.etl-export/incremental_export.py
```
A single tick run. Reads current state.json cursor → exports → uploads.
Don't run more than 1-2 of these by hand — cron is on a 5-min cadence
and overlapping runs aren't tested.

## Key file paths

| File | Purpose |
|---|---|
| `~/.etl-export/incremental_export.py` | rishi-1 exporter |
| `~/.etl-export/credentials` | rishi-1 KEY=VALUE creds (0600) |
| `~/.etl-export/state.json` | rishi-1 cursor state |
| `~/.etl-export/etl-export.log` | rishi-1 cron stdout |
| `~/.etl-export/consecutive_failures` | rishi-1 failure counter |
| `/run/secrets/chat_ai_s3_credentials` | V2 mounted secret (inside container) |

## Related memory

- `project_etl_option_a_conflict_handling.md` — the (user, influencer) duplicate skip decision
- `project_infra_actual_state_day9.md` — overall infra topology
