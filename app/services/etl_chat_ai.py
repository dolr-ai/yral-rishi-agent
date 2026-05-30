"""S3-pull ETL fetcher for V2 (Phase 2 of the S3 pivot).

Replaces the direct asyncpg pull from chat-ai (blocked: chat-ai's Postgres
isn't reachable from rishi-4/5/6). Instead, rishi-1's
scripts/incremental_export.py pushes CSV deltas to S3 every 5 min, and
this loop pulls them down and applies them to V2 Postgres.

How a tick runs:
  1. List S3 objects under yral-chat-ai/incremental-sync/ with
     LastModified > last_processed_at
  2. Filter out _heartbeat / STUCK markers; keep only *.csv.gz
  3. For each, in chronological order:
       a. Download to memory, gunzip, parse CSV header
       b. COPY CSV into a temp staging table (LIKE real_table)
       c. INSERT real FROM staging ON CONFLICT (id) DO NOTHING
       d. Record in etl_processed_files (filename PK = idempotent)
  4. Read _heartbeat / STUCK objects, expose freshness via /admin/etl-status

Cursor display is derived from etl_processed_files at query time —
see get_status. The standalone cursor table (etl_sync_state) was
dropped in migration 022 (it was a constant source of asyncpg type
bugs and provided only display value).

Credentials: file at /run/secrets/chat_ai_s3_credentials, KEY=VALUE format
with BACKUP_S3_ACCESS_KEY + BACKUP_S3_SECRET_KEY. If the file is missing,
the loop logs once and idles — same graceful-disable pattern as the
DSN-secret-file approach we replaced.
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import io
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ─── config ───────────────────────────────────────────────────────────────

SYNC_INTERVAL_SEC = 5 * 60
INITIAL_DELAY_SEC = 60

S3_ENDPOINT = "https://hel1.your-objectstorage.com"
S3_REGION = "hel1"
S3_BUCKET = "rishi-yral"
S3_PREFIX = "yral-chat-ai/incremental-sync"
S3_HEARTBEAT_KEY = f"{S3_PREFIX}/_heartbeat"
S3_STUCK_KEY = f"{S3_PREFIX}/STUCK"

# Heartbeat older than this = exporter stalled. rishi-1 cron is every 5
# min, so 15 min = 3 missed ticks.
HEARTBEAT_STALE_SEC = 15 * 60

S3_CREDENTIALS_FILE_DEFAULT = "/run/secrets/chat_ai_s3_credentials"


# Same shape as before so existing tests (etl_integrity's
# CHECKED_TABLES match, V2's status output) keep working unchanged.
# Column lists are the intersection of chat-ai and V2 schemas — V2-only
# columns (is_proactive, variant_label, ...) get DB defaults.
SYNCED_TABLES: list[dict] = [
    {
        "name": "ai_influencers",
        "columns": [
            "id",
            "name",
            "display_name",
            "avatar_url",
            "description",
            "category",
            "system_instructions",
            "personality_traits",
            "initial_greeting",
            "suggested_messages",
            "is_active",
            "is_nsfw",
            "parent_principal_id",
            "source",
            "metadata",
            "created_at",
            "updated_at",
        ],
        "id_column": "id",
    },
    {
        "name": "conversations",
        "columns": [
            "id",
            "user_id",
            "influencer_id",
            "conversation_type",
            "participant_b_id",
            "metadata",
            "created_at",
            "updated_at",
        ],
        "id_column": "id",
    },
    {
        "name": "messages",
        "columns": [
            "id",
            "conversation_id",
            "user_id",
            "role",
            "content",
            "message_type",
            "metadata",
            "created_at",
        ],
        "id_column": "id",
    },
]
_TABLE_SPEC = {t["name"]: t for t in SYNCED_TABLES}


# Filename format from rishi-1: <YYYYMMDDTHHMMSSZ>_<table>.csv.gz
_FILENAME_RE = re.compile(r"^(?P<ts>\d{8}T\d{6}Z)_(?P<table>[a-z_]+)\.csv\.gz$")


# ─── credentials ──────────────────────────────────────────────────────────


def _s3_credentials_path() -> str:
    return os.environ.get("CHAT_AI_S3_CREDENTIALS_FILE", S3_CREDENTIALS_FILE_DEFAULT)


def _load_s3_credentials() -> dict | None:
    """KEY=VALUE shell-style. None = ETL disabled gracefully."""
    path = _s3_credentials_path()
    if not os.path.exists(path):
        return None
    creds = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            creds[k.strip()] = v.strip().strip("'\"")
    if not creds.get("BACKUP_S3_ACCESS_KEY") or not creds.get("BACKUP_S3_SECRET_KEY"):
        logger.warning("etl_chat_ai: s3 credentials file present but missing keys")
        return None
    return creds


def _make_s3_client(creds: dict):
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        region_name=S3_REGION,
        aws_access_key_id=creds["BACKUP_S3_ACCESS_KEY"],
        aws_secret_access_key=creds["BACKUP_S3_SECRET_KEY"],
    )


# ─── S3 listing + download (run in a thread to avoid blocking the loop) ──


def _list_objects_sync(s3, after_iso: str | None) -> list[dict]:
    """List CSV.gz objects after the cursor. Heartbeat + STUCK filtered out."""
    paginator = s3.get_paginator("list_objects_v2")
    after_dt = datetime.fromisoformat(after_iso) if after_iso else None
    if after_dt is not None and after_dt.tzinfo is None:
        after_dt = after_dt.replace(tzinfo=timezone.utc)
    out = []
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            filename = key.rsplit("/", 1)[-1]
            if not _FILENAME_RE.match(filename):
                # _heartbeat, STUCK, anything malformed — skip here, the
                # heartbeat/STUCK status is fetched separately.
                continue
            if after_dt and obj["LastModified"] <= after_dt:
                continue
            out.append(
                {
                    "key": key,
                    "filename": filename,
                    "last_modified": obj["LastModified"],
                    "etag": obj.get("ETag", "").strip('"'),
                    "size": obj["Size"],
                }
            )
    out.sort(key=lambda o: o["last_modified"])
    return out


def _download_sync(s3, key: str) -> tuple[bytes, dict]:
    resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
    body = resp["Body"].read()
    metadata = resp.get("Metadata", {}) or {}
    return body, metadata


def _read_heartbeat_sync(s3) -> tuple[str | None, str | None]:
    """Returns (heartbeat_iso, stuck_iso). Either may be None."""
    heartbeat = None
    stuck = None
    try:
        resp = s3.get_object(Bucket=S3_BUCKET, Key=S3_HEARTBEAT_KEY)
        heartbeat = resp["Body"].read().decode().strip()
    except Exception:
        pass
    try:
        resp = s3.get_object(Bucket=S3_BUCKET, Key=S3_STUCK_KEY)
        stuck = resp["Body"].read().decode().strip()
    except Exception:
        pass
    return heartbeat, stuck


# ─── CSV → V2 apply ───────────────────────────────────────────────────────


def _parse_csv(body_gz: bytes) -> tuple[list[str], list[list]]:
    """Returns (header_columns, rows). Rows are str (CSV strings) — type
    casting happens at COPY time."""
    raw = gzip.decompress(body_gz)
    reader = csv.reader(io.StringIO(raw.decode()))
    rows = list(reader)
    if not rows:
        return [], []
    header = rows[0]
    return header, rows[1:]


async def _apply_csv(
    v2_pool, table_name: str, header: list[str], data_rows: list[list]
) -> dict:
    """COPY data into a temp staging table, then INSERT into the real
    table with Option A skip-and-log semantics.

    Returns:
        {
            "rows_applied":     int — count actually inserted
            "skipped_conflict": list[str] — row ids that hit a UNIQUE
                                            constraint (existing row stays)
            "skipped_orphan":   list[str] — row ids whose parent FK
                                            doesn't exist in V2
                                            (only messages today)
        }

    See [project_etl_option_a_conflict_handling] for why we use bare
    ON CONFLICT DO NOTHING (no target) — V2 has multiple UNIQUE
    constraints chat-ai doesn't (idx_unique_user_influencer,
    idx_unique_human_chat), and only bare-no-target catches them all.
    """
    if not data_rows:
        return {"rows_applied": 0, "skipped_conflict": [], "skipped_orphan": []}
    spec = _TABLE_SPEC[table_name]
    shared = [c for c in spec["columns"] if c in header]
    if not shared:
        raise RuntimeError(
            f"{table_name}: CSV header has no overlap with V2 schema; "
            f"file header={header} spec={spec['columns']}"
        )
    csv_idx = {c: header.index(c) for c in shared}
    id_column = spec["id_column"]
    shared_csv = ",".join(shared)

    # Emit subset CSV to feed COPY. Use the csv writer so quoting matches
    # Postgres COPY CSV format (RFC 4180-ish).
    sub_buf = io.StringIO()
    sub_writer = csv.writer(sub_buf)
    sub_writer.writerow(shared)
    for row in data_rows:
        sub_writer.writerow([row[csv_idx[c]] for c in shared])
    sub_bytes = sub_buf.getvalue().encode()

    async with v2_pool.acquire() as conn:
        async with conn.transaction():
            staging = f"_etl_staging_{table_name}"
            await conn.execute(
                f"CREATE TEMP TABLE {staging} (LIKE {table_name} INCLUDING DEFAULTS) "
                f"ON COMMIT DROP"
            )
            # asyncpg's copy_to_table accepts source=BytesIO and runs
            # native COPY ... FROM STDIN, so Postgres handles all type
            # coercion (uuid, jsonb, timestamptz) the same way it would
            # for psql \COPY.
            await conn.copy_to_table(
                staging,
                source=io.BytesIO(sub_bytes),
                columns=shared,
                format="csv",
                header=True,
            )

            # Messages need pre-filtering for FK validity. Conversations
            # (and ai_influencers) only need ON CONFLICT for unique-
            # constraint handling. See module docstring for why we use
            # bare ON CONFLICT (no target) — V2 has multiple UNIQUE
            # constraints chat-ai doesn't (idx_unique_user_influencer,
            # idx_unique_human_chat), and only bare-no-target catches them
            # all in a single statement.
            inserted_rows = []
            orphan_ids = []
            if table_name == "messages":
                where_clause = (
                    "WHERE EXISTS (SELECT 1 FROM conversations c "
                    "WHERE c.id = s.conversation_id)"
                )
                inserted_rows = await conn.fetch(
                    f"INSERT INTO {table_name} ({shared_csv}) "
                    f"SELECT {shared_csv} FROM {staging} s "
                    f"{where_clause} "
                    f"ON CONFLICT DO NOTHING "
                    f"RETURNING {id_column}"
                )
                # Orphans: in staging without a matching parent in v2.
                orphans = await conn.fetch(
                    f"SELECT s.{id_column} FROM {staging} s "
                    f"WHERE NOT EXISTS (SELECT 1 FROM conversations c "
                    f"WHERE c.id = s.conversation_id)"
                )
                orphan_ids = [str(r[id_column]) for r in orphans]
            else:
                inserted_rows = await conn.fetch(
                    f"INSERT INTO {table_name} ({shared_csv}) "
                    f"SELECT {shared_csv} FROM {staging} "
                    f"ON CONFLICT DO NOTHING "
                    f"RETURNING {id_column}"
                )

            inserted_ids = {str(r[id_column]) for r in inserted_rows}
            # Conflict skips: in staging, not orphaned, not inserted.
            staging_ids = await conn.fetch(f"SELECT {id_column} FROM {staging}")
            staging_id_set = {str(r[id_column]) for r in staging_ids}
            conflict_ids = list(staging_id_set - inserted_ids - set(orphan_ids))

    return {
        "rows_applied": len(inserted_ids),
        "skipped_conflict": conflict_ids,
        "skipped_orphan": orphan_ids,
    }


async def _record_skipped(
    v2_pool,
    filename: str,
    table_name: str,
    row_ids: list[str],
    reason: str,
):
    """Bulk-insert into etl_skipped_rows. The composite UNIQUE
    (filename, table_name, row_id, reason) keeps re-application of the
    same file idempotent — same skipped row produces the same audit
    entry, no duplicates."""
    if not row_ids:
        return
    await v2_pool.executemany(
        """
        INSERT INTO etl_skipped_rows
            (filename, table_name, row_id, reason)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (filename, table_name, row_id, reason) DO NOTHING
        """,
        [(filename, table_name, rid, reason) for rid in row_ids],
    )


async def _record_processed(
    v2_pool,
    filename: str,
    table_name: str,
    rows_applied: int,
    rows_in_file: int,
    etag: str,
    metadata: dict,
    runtime_ms: int,
):
    await v2_pool.execute(
        """
        INSERT INTO etl_processed_files (
            filename, table_name, rows_applied, rows_in_file,
            file_etag, s3_metadata, processed_at, runtime_ms
        ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, NOW(), $7)
        ON CONFLICT (filename) DO NOTHING
        """,
        filename,
        table_name,
        rows_applied,
        rows_in_file,
        etag,
        json.dumps(metadata),
        runtime_ms,
    )


# Note: _advance_cursor was removed in favor of deriving cursors from
# etl_processed_files at query time (see get_status). It was a constant
# source of asyncpg type quirks (ambiguous params, tz-aware-vs-naive
# datetime, string-vs-datetime) and provided only display value — file
# selection always used MAX(processed_at) FROM etl_processed_files.
# Single source of truth is cleaner than maintaining a parallel
# denormalized cursor table.


# ─── orchestration ────────────────────────────────────────────────────────


async def _get_last_processed_at(v2_pool) -> str | None:
    return await v2_pool.fetchval("SELECT MAX(processed_at) FROM etl_processed_files")


async def _is_already_processed(v2_pool, filename: str) -> bool:
    return bool(
        await v2_pool.fetchval(
            "SELECT 1 FROM etl_processed_files WHERE filename = $1", filename
        )
    )


async def run_once(v2_pool) -> dict:
    """One full S3-fetch pass."""
    creds = _load_s3_credentials()
    if creds is None:
        return {"status": "disabled", "reason": "S3 credentials not mounted"}

    s3 = _make_s3_client(creds)
    cursor = await _get_last_processed_at(v2_pool)
    cursor_iso = cursor.isoformat() if cursor else None

    files = await asyncio.to_thread(_list_objects_sync, s3, cursor_iso)
    if not files:
        logger.info("etl_chat_ai: no new files since %s", cursor_iso or "epoch")
        return {"status": "ok", "files_processed": 0}

    overall = {"status": "ok", "files_processed": 0, "per_table": {}}
    for obj in files:
        filename = obj["filename"]
        match = _FILENAME_RE.match(filename)
        table_name = match["table"]
        if table_name not in _TABLE_SPEC:
            logger.warning("etl_chat_ai: unknown table in filename %s", filename)
            continue
        if await _is_already_processed(v2_pool, filename):
            continue

        t0 = time.monotonic()
        try:
            body, metadata = await asyncio.to_thread(_download_sync, s3, obj["key"])
            header, rows = _parse_csv(body)
            # Defense: rishi-1's exporter put the row count in metadata.
            # If CSV line count doesn't match, something corrupted in transit.
            claimed = int(metadata.get("rows", -1))
            if claimed >= 0 and claimed != len(rows):
                raise RuntimeError(
                    f"{filename}: rows-in-file mismatch: "
                    f"S3 metadata says {claimed}, CSV has {len(rows)}"
                )
            apply_result = await _apply_csv(v2_pool, table_name, header, rows)
            rows_applied = apply_result["rows_applied"]
            conflict_ids = apply_result["skipped_conflict"]
            orphan_ids = apply_result["skipped_orphan"]
            runtime_ms = int((time.monotonic() - t0) * 1000)

            # Record skipped rows BEFORE recording the file as processed,
            # so a crash here leaves the file un-recorded and the next
            # tick retries everything (cleanly idempotent because the
            # skip-log UNIQUE de-dupes too).
            await _record_skipped(
                v2_pool, filename, table_name, conflict_ids, "conflict"
            )
            await _record_skipped(v2_pool, filename, table_name, orphan_ids, "orphan")

            await _record_processed(
                v2_pool,
                filename,
                table_name,
                rows_applied,
                len(rows),
                obj["etag"],
                metadata,
                runtime_ms,
            )
            # Cursors are derived from etl_processed_files at query
            # time — see get_status. No separate write needed here.
            overall["files_processed"] += 1
            overall["per_table"].setdefault(
                table_name,
                {"files": 0, "rows": 0, "skipped_conflict": 0, "skipped_orphan": 0},
            )
            overall["per_table"][table_name]["files"] += 1
            overall["per_table"][table_name]["rows"] += rows_applied
            overall["per_table"][table_name]["skipped_conflict"] += len(conflict_ids)
            overall["per_table"][table_name]["skipped_orphan"] += len(orphan_ids)
            logger.info(
                "etl_chat_ai: applied %s rows=%d skipped_conflict=%d "
                "skipped_orphan=%d in_file=%d runtime_ms=%d",
                filename,
                rows_applied,
                len(conflict_ids),
                len(orphan_ids),
                len(rows),
                runtime_ms,
            )
        except Exception as e:
            runtime_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(
                "etl_chat_ai: %s failed after %dms: %s: %s",
                filename,
                runtime_ms,
                type(e).__name__,
                e,
            )
            # Don't record in etl_processed_files — next tick retries.
            # Don't break the loop — try the next file.
            continue
    return overall


async def etl_loop():
    from database import get_pool
    from kill_switch import is_enabled

    await asyncio.sleep(INITIAL_DELAY_SEC)
    if _load_s3_credentials() is None:
        logger.info(
            "etl_chat_ai: s3 credentials not mounted at %s; ETL idle. "
            "Mount via `docker service update --secret-add chat_ai_s3_credentials`.",
            _s3_credentials_path(),
        )
    while True:
        try:
            # Emergency kill-switch (env symmetry). Non-Gemini, but
            # included so ops can stop the whole background side.
            if not is_enabled("etl"):
                await asyncio.sleep(SYNC_INTERVAL_SEC)
                continue
            v2_pool = await get_pool()
            await run_once(v2_pool)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("etl_chat_ai loop tick failed (non-fatal): %s", e)
        await asyncio.sleep(SYNC_INTERVAL_SEC)


# ─── status helper for /admin/etl-status ──────────────────────────────────


async def get_status(v2_pool) -> dict:
    """Snapshot for operators: cursors, file counts, heartbeat freshness.

    Cursors are derived from etl_processed_files — single source of truth.
    `last_sync_ts` is the max `s3_metadata->>'until'` value (the
    watermarked chat-ai timestamp the exporter actually included), NOT
    `processed_at` (which is V2's apply time).
    """
    creds = _load_s3_credentials()
    cursors = await v2_pool.fetch(
        """
        SELECT
            t.name AS table_name,
            COALESCE(
                MAX(p.s3_metadata->>'until'),
                '1970-01-01T00:00:00+00:00'
            ) AS last_sync_ts,
            MAX(p.processed_at) AS last_run_at,
            COALESCE(SUM(p.rows_applied), 0) AS rows_pulled_total,
            COALESCE(MAX(p.rows_applied) FILTER (
                WHERE p.processed_at = (
                    SELECT MAX(p2.processed_at) FROM etl_processed_files p2
                    WHERE p2.table_name = t.name
                )
            ), 0) AS rows_pulled_last_run,
            COALESCE(MAX(p.runtime_ms) FILTER (
                WHERE p.processed_at = (
                    SELECT MAX(p2.processed_at) FROM etl_processed_files p2
                    WHERE p2.table_name = t.name
                )
            ), NULL) AS last_runtime_ms,
            NULL::text AS last_error
        FROM (VALUES ('ai_influencers'), ('conversations'), ('messages')) AS t(name)
        LEFT JOIN etl_processed_files p ON p.table_name = t.name
        GROUP BY t.name
        ORDER BY t.name
        """
    )
    files_24h = await v2_pool.fetchval(
        """
        SELECT COUNT(*) FROM etl_processed_files
        WHERE processed_at > NOW() - INTERVAL '24 hours'
        """
    )
    rows_24h = await v2_pool.fetchval(
        """
        SELECT COALESCE(SUM(rows_applied), 0) FROM etl_processed_files
        WHERE processed_at > NOW() - INTERVAL '24 hours'
        """
    )
    last_processed = await v2_pool.fetchval(
        "SELECT MAX(processed_at) FROM etl_processed_files"
    )

    # Option A skip counts (24h). Per-reason breakdown so we know whether
    # drift is from conflicts (= V2 already had this user/pair) or orphans
    # (= parent conversation never landed — usually a cascade of a prior
    # conflict).
    skipped_total_24h = await v2_pool.fetchval(
        """
        SELECT COUNT(*) FROM etl_skipped_rows
        WHERE skipped_at > NOW() - INTERVAL '24 hours'
        """
    )
    skipped_by_reason = await v2_pool.fetch(
        """
        SELECT reason, COUNT(*) AS n FROM etl_skipped_rows
        WHERE skipped_at > NOW() - INTERVAL '24 hours'
        GROUP BY reason
        """
    )
    skipped_by_table = await v2_pool.fetch(
        """
        SELECT table_name, reason, COUNT(*) AS n FROM etl_skipped_rows
        WHERE skipped_at > NOW() - INTERVAL '24 hours'
        GROUP BY table_name, reason
        ORDER BY table_name, reason
        """
    )

    heartbeat = None
    stuck = None
    heartbeat_age_sec = None
    if creds is not None:
        try:
            s3 = _make_s3_client(creds)
            heartbeat, stuck = await asyncio.to_thread(_read_heartbeat_sync, s3)
            if heartbeat:
                hb_dt = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
                if hb_dt.tzinfo is None:
                    hb_dt = hb_dt.replace(tzinfo=timezone.utc)
                heartbeat_age_sec = int(
                    (datetime.now(timezone.utc) - hb_dt).total_seconds()
                )
        except Exception as e:
            logger.warning("etl_chat_ai: heartbeat read failed: %s", e)

    return {
        "s3_credentials_mounted": creds is not None,
        "tables": [dict(r) for r in cursors],
        "files_processed_24h": int(files_24h or 0),
        "rows_applied_24h": int(rows_24h or 0),
        "skipped_rows_24h": int(skipped_total_24h or 0),
        "skipped_by_reason": {r["reason"]: int(r["n"]) for r in skipped_by_reason},
        "skipped_by_table": [dict(r) for r in skipped_by_table],
        "last_processed_at": last_processed.isoformat() if last_processed else None,
        "heartbeat": heartbeat,
        "heartbeat_age_sec": heartbeat_age_sec,
        "heartbeat_stale": (
            heartbeat_age_sec is None or heartbeat_age_sec > HEARTBEAT_STALE_SEC
        )
        if creds is not None
        else None,
        "stuck_marker": stuck,
    }


# ─── skip-audit helper for /admin/etl-skipped ────────────────────────────


async def get_skipped(v2_pool, hours: int, reason: str | None) -> dict:
    """Recent skipped rows with filtering. Capped at 500 to bound payload.
    Used by /admin/etl-skipped for audit drill-in."""
    if reason:
        rows = await v2_pool.fetch(
            """
            SELECT filename, table_name, row_id, reason, skipped_at
            FROM etl_skipped_rows
            WHERE reason = $1
              AND skipped_at > NOW() - ($2 || ' hours')::interval
            ORDER BY skipped_at DESC LIMIT 500
            """,
            reason,
            str(hours),
        )
    else:
        rows = await v2_pool.fetch(
            """
            SELECT filename, table_name, row_id, reason, skipped_at
            FROM etl_skipped_rows
            WHERE skipped_at > NOW() - ($1 || ' hours')::interval
            ORDER BY skipped_at DESC LIMIT 500
            """,
            str(hours),
        )
    return {
        "hours": hours,
        "reason": reason,
        "result_count": len(rows),
        "results": [dict(r) for r in rows],
    }
