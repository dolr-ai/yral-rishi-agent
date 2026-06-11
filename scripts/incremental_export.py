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
import hashlib
import io
import json
import logging
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
S3_INTEGRITY_PREFIX = f"{S3_PREFIX}/_integrity"

# Integrity emission cadences (in seconds). Cron runs every 5 min, so
# these gate by elapsed time since last emit — no extra cron entries.
HOURLY_INTERVAL_SEC = 60 * 60
SAMPLE_INTERVAL_SEC = 6 * 60 * 60
SENTINEL_INTERVAL_SEC = 30 * 60

# Layer 1/2 watermarks. Layer 1 (hourly): excludes last 10 min — chat-ai
# writes can still be in flight. Layer 2 (sample): wider 15-min margin
# so the same conversation has finished bursting.
HOURLY_WATERMARK_SEC = 10 * 60
SAMPLE_WATERMARK_SEC = 15 * 60
SAMPLE_CONVERSATION_COUNT = 20

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

# Columns shipped in the sample integrity payload for each conversation.
# Matches V2's etl_chat_ai.SYNCED_TABLES[conversations][columns].
SAMPLE_CONVERSATION_COLUMNS = (
    "id",
    "user_id",
    "influencer_id",
    "conversation_type",
    "participant_b_id",
    "metadata",
    "created_at",
    "updated_at",
)

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
    """State shape:
        {
          # Per-table sync cursor (set by export_table)
          "ai_influencers": ISO, "conversations": ISO, "messages": ISO,
          # Last integrity emission timestamps (set by integrity.py)
          "_last_hourly_emit":   ISO or None,
          "_last_sample_emit":   ISO or None,
          "_last_sentinel_emit": ISO or None,
        }
    Old state files (without the _last_* keys) auto-upgrade via the
    setdefault() defaults below — first tick after upgrade emits all
    three integrity payloads because they're "infinitely overdue".
    """
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    else:
        # First run — Phase 4 bootstrap may overwrite with 2026-05-26.
        state = {t: EPOCH_ISO for t in SYNCED_TABLES}
    state.setdefault("_last_hourly_emit", None)
    state.setdefault("_last_sample_emit", None)
    state.setdefault("_last_sentinel_emit", None)
    return state


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
        check=True,
        capture_output=True,
        text=True,
    )
    for name in result.stdout.splitlines():
        if name.startswith(PATRONI_CONTAINER_PREFIX):
            return name
    raise SystemExit(f"no running container with prefix {PATRONI_CONTAINER_PREFIX!r}")


# ─── pg query + dump ──────────────────────────────────────────────────────


def _psql_value(container: str, password: str, sql: str) -> str:
    """Run a single SELECT through psql, return the unquoted scalar."""
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={password}",
            container,
            "psql",
            "-h",
            "localhost",
            "-U",
            CHAT_AI_USER,
            "-d",
            CHAT_AI_DB,
            "-At",
            "-c",
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
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
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={password}",
            container,
            "psql",
            "-h",
            "localhost",
            "-U",
            CHAT_AI_USER,
            "-d",
            CHAT_AI_DB,
            "-c",
            copy_sql,
        ],
        check=True,
        capture_output=True,
        timeout=300,
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


# ─── per-table export ─────────────────────────────────────────────────────


def export_table(s3, container: str, password: str, table: str, since_iso: str):
    """Returns (uploaded_key_or_None, new_cursor_iso, row_count)."""
    count, max_ts = count_and_max(container, password, table, since_iso)
    if count == 0:
        return None, since_iso, 0

    # Cap the window at the watermark even if MAX < watermark — we already
    # filtered by it in count_and_max, so max_ts IS within the watermark.
    until_iso = max_ts
    csv_bytes = copy_window(container, password, table, since_iso, until_iso)

    # Verify row count matches what count_and_max reported. Must count
    # CSV rows (newline-aware), NOT byte newlines — message content can
    # contain literal `\n` characters which RFC 4180 quotes inline but
    # still produces raw `\n` bytes inside the CSV payload. A byte-newline
    # count overcounts by the number of embedded newlines.
    import csv as _csv

    csv_row_count = sum(1 for _ in _csv.reader(io.StringIO(csv_bytes.decode())))
    actual_data_rows = csv_row_count - 1  # subtract the header row
    if actual_data_rows != count:
        raise RuntimeError(
            f"{table}: count mismatch: psql said {count}, "
            f"COPY produced {actual_data_rows}"
        )

    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", compresslevel=6) as gz:
        gz.write(csv_bytes)
    payload = gz_buf.getvalue()

    ts_compact = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"{S3_PREFIX}/{ts_compact}_{table}.csv.gz"
    upload(
        s3,
        key,
        payload,
        metadata={
            "table": table,
            "rows": count,
            "since": since_iso,
            "until": until_iso,
            "format": "csv-header",
        },
    )
    return key, until_iso, count


# ─── integrity emissions ──────────────────────────────────────────────────


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_overdue(last_iso: str | None, interval_sec: int) -> bool:
    if not last_iso:
        return True
    last = datetime.fromisoformat(last_iso)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() >= interval_sec


def _ts_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def emit_tick_integrity(
    s3, container: str, password: str, watermark_iso: str, per_table_rows: dict
) -> str:
    """Tick payload: per-table max_created_at + rows_in_tick. V2 cross-
    checks against the CSV file it just processed for the same window."""
    tables = {}
    for table in SYNCED_TABLES:
        max_created_at = _psql_value(
            container,
            password,
            f"SELECT COALESCE(MAX(created_at)::text, '') FROM {table} "
            f"WHERE created_at <= '{watermark_iso}'::timestamptz",
        )
        tables[table] = {
            "max_created_at": max_created_at or None,
            "rows_in_tick": int(per_table_rows.get(table, 0)),
        }
    payload = {
        "watermark_iso": watermark_iso,
        "tables": tables,
    }
    key = f"{S3_INTEGRITY_PREFIX}/tick_{_ts_compact()}.json"
    upload(s3, key, json.dumps(payload).encode(), metadata={"layer": "tick"})
    return key


def emit_hourly_integrity(s3, container: str, password: str) -> str:
    """Layer 1 — full row counts per table older than the watermark.
    V2 runs the same COUNT(*) WHERE created_at < watermark and compares."""
    watermark = datetime.now(timezone.utc).timestamp() - HOURLY_WATERMARK_SEC
    watermark_iso = datetime.fromtimestamp(watermark, tz=timezone.utc).isoformat()
    counts = {}
    for table in SYNCED_TABLES:
        n = _psql_value(
            container,
            password,
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE created_at < '{watermark_iso}'::timestamptz",
        )
        counts[table] = int(n)
    payload = {
        "snapshot_iso": _utc_now_iso(),
        "watermark_iso": watermark_iso,
        "layer_1_row_counts": counts,
    }
    key = f"{S3_INTEGRITY_PREFIX}/hourly_{_ts_compact()}.json"
    upload(s3, key, json.dumps(payload).encode(), metadata={"layer": "hourly"})
    return key


def emit_sample_integrity(s3, container: str, password: str) -> str:
    """Layer 2 — 20 random conversations + per-message content hash.
    Hashes (not raw content) keep the payload tiny and avoid shipping
    user message content through S3 every 6 hours."""
    watermark = datetime.now(timezone.utc).timestamp() - SAMPLE_WATERMARK_SEC
    watermark_iso = datetime.fromtimestamp(watermark, tz=timezone.utc).isoformat()
    sample_ids_raw = _psql_value(
        container,
        password,
        f"SELECT string_agg(id::text, ',') FROM ("
        f"  SELECT id FROM conversations "
        f"  WHERE created_at < '{watermark_iso}'::timestamptz "
        f"  ORDER BY random() LIMIT {SAMPLE_CONVERSATION_COUNT}"
        f") s",
    )
    sample_ids = [s for s in (sample_ids_raw or "").split(",") if s]
    conversations = []
    for conv_id in sample_ids:
        # Full row for the conversation (small — no message content here)
        col_csv = ", ".join(SAMPLE_CONVERSATION_COLUMNS)
        row_raw = _psql_value(
            container,
            password,
            f"SELECT row_to_json(c) FROM ("
            f"  SELECT {col_csv} FROM conversations WHERE id = '{conv_id}'"
            f") c",
        )
        if not row_raw:
            continue
        full_row = json.loads(row_raw)
        # Per-message hash computed Python-side so we don't require
        # pgcrypto on chat-ai. Network is localhost (docker exec into
        # the same container as Postgres), so shipping raw content
        # in-memory is cheap.
        msgs_raw = _psql_value(
            container,
            password,
            f"SELECT COALESCE(json_agg(json_build_object("
            f"  'id', id, 'created_at', created_at, 'role', role, "
            f"  'message_type', message_type, 'content', content"
            f")), '[]'::json) FROM ("
            f"  SELECT id, created_at, role, message_type, content "
            f"  FROM messages WHERE conversation_id = '{conv_id}' "
            f"  ORDER BY created_at"
            f") m",
        )
        msgs_in = json.loads(msgs_raw) if msgs_raw else []
        msgs_out = []
        for m in msgs_in:
            content_str = m.get("content") or ""
            msgs_out.append(
                {
                    "id": m["id"],
                    "created_at": m["created_at"],
                    "role": m["role"],
                    "message_type": m["message_type"],
                    "content_sha256": hashlib.sha256(
                        content_str.encode("utf-8")
                    ).hexdigest(),
                }
            )
        conversations.append(
            {
                "id": conv_id,
                "full_row_columns": full_row,
                "messages": msgs_out,
            }
        )
    payload = {
        "snapshot_iso": _utc_now_iso(),
        "watermark_iso": watermark_iso,
        "conversations": conversations,
    }
    key = f"{S3_INTEGRITY_PREFIX}/sample_{_ts_compact()}.json"
    upload(s3, key, json.dumps(payload).encode(), metadata={"layer": "sample"})
    return key


def emit_sentinel_integrity(s3, container: str, password: str) -> str:
    """Layer 3 — latest message + latest conversation IDs. V2 looks for
    these and reports staleness if not found within a retry window."""
    raw = _psql_value(
        container,
        password,
        "SELECT json_build_object("
        "  'msg_id', (SELECT id FROM messages ORDER BY created_at DESC LIMIT 1),"
        "  'msg_at', (SELECT created_at FROM messages ORDER BY created_at DESC LIMIT 1),"
        "  'conv_id', (SELECT id FROM conversations ORDER BY created_at DESC LIMIT 1),"
        "  'conv_at', (SELECT created_at FROM conversations ORDER BY created_at DESC LIMIT 1)"
        ")",
    )
    latest = json.loads(raw) if raw else {}
    payload = {
        "snapshot_iso": _utc_now_iso(),
        "latest_message_id": latest.get("msg_id"),
        "latest_message_created_at": latest.get("msg_at"),
        "latest_conversation_id": latest.get("conv_id"),
        "latest_conversation_created_at": latest.get("conv_at"),
    }
    key = f"{S3_INTEGRITY_PREFIX}/sentinel_{_ts_compact()}.json"
    upload(s3, key, json.dumps(payload).encode(), metadata={"layer": "sentinel"})
    return key


# ─── main per-tick ─────────────────────────────────────────────────────────


def run_once(force_all_integrity: bool = False) -> int:
    """Returns 0 on success, non-zero on failure. cron captures stdout.

    `force_all_integrity` (Piece A of the on-demand drain system):
    emit ALL integrity layers regardless of their time-gate. The drain
    workflow uses this so the GREEN verdict can require fresh layer
    results in the reconciliation report — without it, the last
    sentinel/hourly/sample might be 30-360 min stale and the verdict
    would be unprovable."""
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
                table,
                n,
                since,
                new_cursor,
                key or "-no-data-",
            )

        # Tick integrity — always. Tiny payload (~200 bytes). Watermark
        # is "now minus 1 min" — same logic count_and_max used to cap
        # what we exported, so V2 can compare counts in the same window.
        watermark = datetime.now(timezone.utc).timestamp() - WATERMARK_SECONDS
        watermark_iso = datetime.fromtimestamp(watermark, tz=timezone.utc).isoformat()
        tick_per_table_rows = {t: per_table[t]["rows"] for t in SYNCED_TABLES}
        emit_tick_integrity(
            s3,
            container,
            creds["ETL_PG_PASSWORD"],
            watermark_iso,
            tick_per_table_rows,
        )

        # Time-gated emissions. _is_overdue handles None → emit on first
        # tick after the script first runs (or after an upgrade).
        # `force_all_integrity` bypasses the gates so a drain run emits
        # fresh layer payloads regardless of when they last ran.
        if force_all_integrity or _is_overdue(
            new_state["_last_sentinel_emit"], SENTINEL_INTERVAL_SEC
        ):
            emit_sentinel_integrity(s3, container, creds["ETL_PG_PASSWORD"])
            new_state["_last_sentinel_emit"] = _utc_now_iso()
            log.info(
                "emitted sentinel integrity%s",
                " (forced)" if force_all_integrity else "",
            )
        if force_all_integrity or _is_overdue(
            new_state["_last_hourly_emit"], HOURLY_INTERVAL_SEC
        ):
            emit_hourly_integrity(s3, container, creds["ETL_PG_PASSWORD"])
            new_state["_last_hourly_emit"] = _utc_now_iso()
            log.info(
                "emitted hourly integrity%s",
                " (forced)" if force_all_integrity else "",
            )
        if force_all_integrity or _is_overdue(
            new_state["_last_sample_emit"], SAMPLE_INTERVAL_SEC
        ):
            emit_sample_integrity(s3, container, creds["ETL_PG_PASSWORD"])
            new_state["_last_sample_emit"] = _utc_now_iso()
            log.info(
                "emitted sample integrity%s",
                " (forced)" if force_all_integrity else "",
            )

        save_state(new_state)

        # Heartbeat — V2 reads this to detect stalled exports.
        heartbeat = datetime.now(timezone.utc).isoformat()
        upload(
            s3,
            f"{S3_PREFIX}/_heartbeat",
            heartbeat.encode(),
            metadata={"per_table": json.dumps(per_table)},
        )

        reset_failure_counter()
        elapsed = time.monotonic() - t0
        if elapsed > RUN_WARN_SEC:
            log.warning("run took %.1fs (warn threshold %ds)", elapsed, RUN_WARN_SEC)
        log.info("ok elapsed=%.1fs", elapsed)
        return 0

    except Exception as e:
        elapsed = time.monotonic() - t0
        n_fail = bump_failure_counter()
        log.error(
            "FAILED after %.1fs (consecutive=%d): %s: %s",
            elapsed,
            n_fail,
            type(e).__name__,
            e,
        )
        if n_fail >= 3:
            try:
                creds = load_credentials()
                s3 = s3_client(creds)
                upload(
                    s3,
                    f"{S3_PREFIX}/STUCK",
                    datetime.now(timezone.utc).isoformat().encode(),
                    metadata={
                        "consecutive_failures": n_fail,
                        "last_error": f"{type(e).__name__}: {e}",
                    },
                )
                log.error("wrote STUCK marker to S3")
            except Exception as e2:
                log.error("could not write STUCK marker: %s", e2)
        return 1


if __name__ == "__main__":
    # `--force` (Piece A of the on-demand drain) — same export tick but
    # emits all integrity layers regardless of time-gate. Used by the
    # etl-drain.yml workflow via scripts/etl-manual-trigger.sh. Cron
    # runs without this flag.
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Force-emit ALL integrity layers (hourly + sample + sentinel) "
            "regardless of their normal time-gates. Used by manual drain "
            "runs so the reconciliation report has fresh evidence on every "
            "layer."
        ),
    )
    args = parser.parse_args()
    sys.exit(run_once(force_all_integrity=args.force))
