#!/usr/bin/env python3
"""Incremental ETL exporter: chat-ai (rishi-1) -> Hetzner S3.

Why this exists: chat-ai's Postgres lives on a swarm overlay and isn't
reachable from V2 (rishi-4/5/6). So we push deltas from inside chat-ai's
swarm instead of pulling from V2.

How it runs:
  cron @ 5 min on rishi-1 (deploy user). pg_dump executes inside the
  chat-ai Patroni container via `docker exec` (where pg_dump + Postgres
  on localhost:5432 already exist). Output is gzipped on the host and
  uploaded to S3 via boto3.

Read-only contract: connects as etl_readonly, which has SELECT-only on
all tables. pg_dump uses --data-only --inserts (no DDL, no writes).

Files used on rishi-1 (created out-of-band by deploy step):
  ~/.etl-export/credentials   shell-style KEY=VALUE, mode 0600
                              required keys: ETL_PG_PASSWORD,
                              BACKUP_S3_ACCESS_KEY, BACKUP_S3_SECRET_KEY
  ~/.etl-export/state.json    {table: ISO8601 last_sync, ...}

S3 layout (bucket=rishi-yral, endpoint=hel1.your-objectstorage.com):
  yral-chat-ai/incremental-sync/<UTC ISO8601 compact>_<table>.sql.gz
  yral-chat-ai/incremental-sync/_heartbeat   (one-line ISO ts, every run)
  yral-chat-ai/incremental-sync/STUCK        (created after 3 consecutive failures)
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ─── config ───────────────────────────────────────────────────────────────

ETL_DIR = Path.home() / ".etl-export"
CRED_FILE = ETL_DIR / "credentials"
STATE_FILE = ETL_DIR / "state.json"
FAILURE_FILE = ETL_DIR / "consecutive_failures"

S3_ENDPOINT = "https://hel1.your-objectstorage.com"
S3_REGION = "hel1"
S3_BUCKET = "rishi-yral"
S3_PREFIX = "yral-chat-ai/incremental-sync"

CHAT_AI_DB = "chat_ai_db"
CHAT_AI_USER = "etl_readonly"
PATRONI_CONTAINER_PREFIX = "chat-ai-db_patroni-rishi-"

# 1-min watermark prevents racing in-flight inserts. Matches V2's
# integrity check window choice (10-min there is more conservative
# because integrity tolerates more lag than freshness does).
WATERMARK_SECONDS = 60

# 120s soft warning (the user-facing spec), 240s hard fail. Below the
# 5-min cron interval so a slow run can't overlap the next one.
RUN_WARN_SEC = 120
RUN_FAIL_SEC = 240

SYNCED_TABLES = ("ai_influencers", "conversations", "messages")

EPOCH_ISO = "1970-01-01T00:00:00+00:00"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("etl-export")


# ─── credentials + state ──────────────────────────────────────────────────


def load_credentials() -> dict:
    """KEY=VALUE shell-style. Quotes optional. Missing file = hard fail."""
    if not CRED_FILE.exists():
        raise SystemExit(f"missing credentials file: {CRED_FILE}")
    creds = {}
    for line in CRED_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        creds[k.strip()] = v.strip().strip("'\"")
    required = ("ETL_PG_PASSWORD", "BACKUP_S3_ACCESS_KEY", "BACKUP_S3_SECRET_KEY")
    missing = [k for k in required if not creds.get(k)]
    if missing:
        raise SystemExit(f"credentials file missing keys: {missing}")
    return creds


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    # First run — initialize per table. Bootstrap step (Phase 4) may
    # overwrite this with the 2026-05-26 timestamp before first cron tick.
    return {t: EPOCH_ISO for t in SYNCED_TABLES}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_FILE)


# ─── patroni container discovery ──────────────────────────────────────────


def find_patroni_container() -> str:
    """Return the local Patroni container name. Any node of the cluster
    works — pg_dump is fine against a replica, and rishi-1 always has its
    own Patroni container regardless of leader role."""
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        check=True, capture_output=True, text=True,
    )
    for name in result.stdout.splitlines():
        if name.startswith(PATRONI_CONTAINER_PREFIX):
            return name
    raise SystemExit(
        f"no running container with prefix {PATRONI_CONTAINER_PREFIX!r}"
    )


# ─── pg query + dump ──────────────────────────────────────────────────────


def _psql_value(container: str, password: str, sql: str) -> str:
    """Run a single SELECT through psql, return the unquoted scalar."""
    result = subprocess.run(
        [
            "docker", "exec", "-i", "-e", f"PGPASSWORD={password}",
            container,
            "psql", "-h", "localhost", "-U", CHAT_AI_USER,
            "-d", CHAT_AI_DB, "-At", "-c", sql,
        ],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip()


def count_and_max(container: str, password: str, table: str, since_iso: str):
    """Returns (row_count_in_window, max_created_at_in_window_iso)."""
    sql = (
        f"SELECT COUNT(*), COALESCE(MAX(created_at)::text, '') "
        f"FROM {table} "
        f"WHERE created_at > '{since_iso}'::timestamptz "
        f"AND created_at <= NOW() - INTERVAL '{WATERMARK_SECONDS} seconds'"
    )
    raw = _psql_value(container, password, sql)
    # psql -At with multi-column returns "count|max" pipe-separated
    count_str, _, max_str = raw.partition("|")
    return int(count_str), (max_str or None)


def copy_window(
    container: str, password: str, table: str, since_iso: str, until_iso: str
) -> bytes:
    """Stream the [since, until] window as CSV-with-header via \\COPY.

    Why COPY instead of pg_dump --data-only --inserts: pg_dump has no
    --where flag, so it can only emit full-table dumps. COPY-with-WHERE
    lets us emit just the delta. The V2 apply step turns CSV rows into
    INSERT ... ON CONFLICT DO NOTHING statements at apply time, which is
    where the idempotency contract lives anyway.
    """
    copy_sql = (
        f"\\COPY (SELECT * FROM {table} "
        f"WHERE created_at > '{since_iso}'::timestamptz "
        f"AND created_at <= '{until_iso}'::timestamptz) "
        f"TO STDOUT WITH (FORMAT csv, HEADER true)"
    )
    result = subprocess.run(
        [
            "docker", "exec", "-i", "-e", f"PGPASSWORD={password}",
            container,
            "psql", "-h", "localhost", "-U", CHAT_AI_USER,
            "-d", CHAT_AI_DB, "-c", copy_sql,
        ],
        check=True, capture_output=True, timeout=300,
    )
    return result.stdout


# ─── S3 ───────────────────────────────────────────────────────────────────


def s3_client(creds: dict):
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        region_name=S3_REGION,
        aws_access_key_id=creds["BACKUP_S3_ACCESS_KEY"],
        aws_secret_access_key=creds["BACKUP_S3_SECRET_KEY"],
    )


def upload(s3, key: str, body: bytes, metadata: dict | None = None) -> None:
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=body,
        Metadata={k: str(v) for k, v in (metadata or {}).items()},
    )


# ─── failure tracking ─────────────────────────────────────────────────────


def bump_failure_counter() -> int:
    n = int(FAILURE_FILE.read_text().strip()) if FAILURE_FILE.exists() else 0
    n += 1
    FAILURE_FILE.write_text(str(n))
    return n


def reset_failure_counter() -> None:
    if FAILURE_FILE.exists():
        FAILURE_FILE.unlink()


# ─── main per-tick ─────────────────────────────────────────────────────────


def export_table(s3, container: str, password: str, table: str, since_iso: str):
    """Returns (uploaded_key_or_None, new_cursor_iso, row_count)."""
    count, max_ts = count_and_max(container, password, table, since_iso)
    if count == 0:
        return None, since_iso, 0

    # Cap the window at the watermark even if MAX < watermark — we already
    # filtered by it in count_and_max, so max_ts IS within the watermark.
    until_iso = max_ts
    csv_bytes = copy_window(container, password, table, since_iso, until_iso)

    # Verify row count matches what count_and_max reported. Header line +
    # data lines: csv_lines == count + 1.
    csv_lines = csv_bytes.count(b"\n")
    if csv_lines != count + 1:
        raise RuntimeError(
            f"{table}: count mismatch: psql said {count}, "
            f"COPY produced {csv_lines - 1}"
        )

    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", compresslevel=6) as gz:
        gz.write(csv_bytes)
    payload = gz_buf.getvalue()

    ts_compact = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"{S3_PREFIX}/{ts_compact}_{table}.csv.gz"
    upload(s3, key, payload, metadata={
        "table": table,
        "rows": count,
        "since": since_iso,
        "until": until_iso,
        "format": "csv-header",
    })
    return key, until_iso, count


def run_once() -> int:
    """Returns 0 on success, non-zero on failure. cron captures stdout."""
    t0 = time.monotonic()
    try:
        creds = load_credentials()
        state = load_state()
        container = find_patroni_container()
        s3 = s3_client(creds)

        new_state = dict(state)
        per_table = {}
        for table in SYNCED_TABLES:
            since = state.get(table, EPOCH_ISO)
            key, new_cursor, n = export_table(
                s3, container, creds["ETL_PG_PASSWORD"], table, since
            )
            new_state[table] = new_cursor
            per_table[table] = {"rows": n, "s3_key": key, "cursor": new_cursor}
            log.info(
                "table=%s rows=%d since=%s -> cursor=%s key=%s",
                table, n, since, new_cursor, key or "-no-data-",
            )

        save_state(new_state)

        # Heartbeat — V2 reads this to detect stalled exports.
        heartbeat = datetime.now(timezone.utc).isoformat()
        upload(s3, f"{S3_PREFIX}/_heartbeat", heartbeat.encode(),
               metadata={"per_table": json.dumps(per_table)})

        reset_failure_counter()
        elapsed = time.monotonic() - t0
        if elapsed > RUN_WARN_SEC:
            log.warning("run took %.1fs (warn threshold %ds)", elapsed, RUN_WARN_SEC)
        log.info("ok elapsed=%.1fs", elapsed)
        return 0

    except Exception as e:
        elapsed = time.monotonic() - t0
        n_fail = bump_failure_counter()
        log.error("FAILED after %.1fs (consecutive=%d): %s: %s",
                  elapsed, n_fail, type(e).__name__, e)
        if n_fail >= 3:
            try:
                creds = load_credentials()
                s3 = s3_client(creds)
                upload(s3, f"{S3_PREFIX}/STUCK",
                       datetime.now(timezone.utc).isoformat().encode(),
                       metadata={"consecutive_failures": n_fail,
                                 "last_error": f"{type(e).__name__}: {e}"})
                log.error("wrote STUCK marker to S3")
            except Exception as e2:
                log.error("could not write STUCK marker: %s", e2)
        return 1


if __name__ == "__main__":
    sys.exit(run_once())
