"""S3-based integrity verifier (Phase 3 of the S3 ETL pivot).

rishi-1's incremental_export.py emits four kinds of integrity payloads
to s3://rishi-yral/yral-chat-ai/incremental-sync/_integrity/, since V2
can't query chat-ai directly:

  tick      every 5 min   per-table max_created_at + rows in this tick
  hourly    every 60 min  full row counts where created_at < watermark
  sample    every 6h      20 random conversations + per-msg sha256
  sentinel  every 30 min  latest message + conversation IDs

V2's loop polls the prefix every SYNC_INTERVAL_SEC, downloads each
new payload, runs the matching verifier against V2's own DB, and
writes one row to etl_integrity_results per file.

Decisions are passed/fail + drift_count + details (JSONB). The
endpoints in routes/health.py surface the latest result per layer
and let an operator drill into specific failures.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ─── config ───────────────────────────────────────────────────────────────

INTEGRITY_INTERVAL_SEC = 5 * 60  # poll cadence (matches export tick)
INITIAL_DELAY_SEC = 10 * 60  # ETL warms first, then integrity starts

# Drift thresholds. We're strict: a missing row is a missing row. The
# bands let us distinguish "tolerable lag from in-flight inserts" from
# "actual divergence". rishi-1 already excludes the last 10-15 min via
# its watermarks, so legitimate drift should be near-zero.
# Tick verifier removed 2026-06-26 — see _verify_hourly docstring for the
# architectural rationale. The 5-min window is too narrow to distinguish
# "chat-ai hasn't ETL'd yet" from "v2 wrote net-new" once `agent.rishi.yral.com`
# became a peer writer. Sentinel canary covers the freshness signal.
HOURLY_DRIFT_TOLERANCE = 50  # 50-row tolerance per hour
SAMPLE_MISMATCH_TOLERANCE = 0  # any hash mismatch = real divergence
SENTINEL_STALENESS_LIMIT_SEC = 15 * 60  # latest message must arrive in 15 min

CHECKED_TABLES = ("ai_influencers", "conversations", "messages")

# S3 config — copied from etl_chat_ai (Phase 2). Avoids importing from
# there to keep the modules decoupled.
S3_ENDPOINT = "https://hel1.your-objectstorage.com"
S3_REGION = "hel1"
S3_BUCKET = "rishi-yral"
S3_INTEGRITY_PREFIX = "yral-chat-ai/incremental-sync/_integrity"
S3_CREDENTIALS_FILE_DEFAULT = "/run/secrets/chat_ai_s3_credentials"

_FILENAME_RE = re.compile(
    r"^(?P<layer>tick|hourly|sample|sentinel)_(?P<ts>\d{8}T\d{6}Z)\.json$"
)


# Backwards-compat constants — old tests still pin these. They no longer
# drive behavior (the new model is per-layer above) but the values are
# preserved so callers reading them don't break.
MAX_DRIFT_ROWS = HOURLY_DRIFT_TOLERANCE * 10
FAIL_DRIFT_ROWS = HOURLY_DRIFT_TOLERANCE * 100
SAMPLE_CONVERSATIONS = 20
WARN_SAMPLE_MISMATCHES = 1
FAIL_SAMPLE_MISMATCHES = 3


# ─── credentials + s3 client (mirror etl_chat_ai) ─────────────────────────


def _s3_credentials_path() -> str:
    return os.environ.get("CHAT_AI_S3_CREDENTIALS_FILE", S3_CREDENTIALS_FILE_DEFAULT)


def _load_s3_credentials() -> dict | None:
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


# ─── S3 listing ───────────────────────────────────────────────────────────


def _list_integrity_sync(s3, since_iso: str | None) -> list[dict]:
    paginator = s3.get_paginator("list_objects_v2")
    since_dt = None
    if since_iso:
        since_dt = datetime.fromisoformat(since_iso)
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=timezone.utc)
    out = []
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=f"{S3_INTEGRITY_PREFIX}/"):
        for obj in page.get("Contents", []):
            filename = obj["Key"].rsplit("/", 1)[-1]
            m = _FILENAME_RE.match(filename)
            if not m:
                continue
            if since_dt and obj["LastModified"] <= since_dt:
                continue
            out.append(
                {
                    "key": obj["Key"],
                    "filename": filename,
                    "layer": m["layer"],
                    "last_modified": obj["LastModified"],
                }
            )
    out.sort(key=lambda o: o["last_modified"])
    return out


def _download_json_sync(s3, key: str) -> dict:
    resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
    return json.loads(resp["Body"].read())


# ─── datetime adapters for asyncpg ───────────────────────────────────────
#
# asyncpg validates parameter types client-side before sending to Postgres.
# A SQL `::cast` in the query doesn't save you — by the time Postgres sees
# the cast, asyncpg has already rejected the param. The rules:
#
#   TIMESTAMP (without time zone) column → naive datetime (no tzinfo)
#   TIMESTAMPTZ (with time zone) column  → aware datetime (with tzinfo)
#   either column type with a str param  → asyncpg won't coerce; parse first
#
# Two adapters below normalize from "whatever we have" to "what asyncpg
# expects for this column type." See
# memory/feedback_audit_codebase_wide_when_fixing_typecodec.md for the
# rule + the cascade of fix PRs (#217-#220, this one) that led to it.


def _to_naive_utc(dt_or_iso) -> datetime:
    """Normalize to NAIVE UTC datetime — for `TIMESTAMP` columns
    (e.g. messages.created_at, conversations.created_at). Accepts a
    datetime or an ISO string; either way the result has no tzinfo and
    represents UTC wall-clock."""
    if isinstance(dt_or_iso, str):
        s = dt_or_iso.strip()
        if "T" not in s and " " in s:
            s = s.replace(" ", "T", 1)
        dt = datetime.fromisoformat(s)
    else:
        dt = dt_or_iso
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _canonicalize_for_compare(val) -> str | None:
    """Stable string form of a field for Layer 2 sample comparison.

    The rishi-1 export serializes timestamps via Postgres `row_to_json`,
    which renders microseconds with trailing zeros TRIMMED:
        '2026-03-18T01:48:25.53554'    (chat-ai side, 5-digit μs)
    V2 reads via asyncpg as a datetime, then .isoformat() always pads:
        '2026-03-18T01:48:25.535540'   (V2 side, 6-digit μs)
    Same instant, different render — without this normalize step the
    Layer 2 sample check flags a spurious mismatch.

    Strategy: if the value LOOKS like an ISO timestamp, parse it and
    re-emit at fixed 6-digit microsecond precision UTC. Otherwise plain
    str(). datetime instances also flow through the same parse path so
    chat-ai-string vs V2-datetime end up at identical bytes."""
    if val is None:
        return None
    if isinstance(val, datetime):
        dt = val
    elif isinstance(val, str):
        s = val.strip()
        # Postgres wire format uses space, ISO uses T — both work
        looks_like_ts = (
            len(s) >= 10
            and s[4] == "-"
            and s[7] == "-"
            and (len(s) == 10 or s[10] in ("T", " "))
        )
        if not looks_like_ts:
            return s
        try:
            normalized = s.replace(" ", "T", 1) if "T" not in s else s
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return s
    else:
        return str(val)
    # Normalize to UTC-aware then emit fixed 6-digit microsecond ISO.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _to_aware_utc(dt_or_iso) -> datetime:
    """Normalize to AWARE UTC datetime — for `TIMESTAMPTZ` columns
    (e.g. etl_integrity_results.snapshot_iso). Same string/datetime
    flexibility, result always has tzinfo=UTC."""
    if isinstance(dt_or_iso, str):
        s = dt_or_iso.strip()
        if "T" not in s and " " in s:
            s = s.replace(" ", "T", 1)
        dt = datetime.fromisoformat(s)
    else:
        dt = dt_or_iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


# ─── verifiers ────────────────────────────────────────────────────────────


async def _verify_hourly(v2_pool, payload: dict) -> tuple[bool, int, dict]:
    """V2 runs the same COUNT(*) WHERE created_at < watermark and
    compares each table to the chat-ai count rishi-1 reported.

    ## Architecture: v2 ⊇ ETL(chat_ai), NOT v2 == chat_ai

    Chat-ai is a peer of v2, not its source of truth. V2 receives
    chat-ai's rows via ETL AND independently accepts its own writes
    from `agent.rishi.yral.com` (Caddy routes the public hostname
    directly to v2/rishi-4-5). Once that v2-native traffic exists,
    `v2_count >= chat_ai_count + N` for N>0 is by-design — proactive
    loops, nudge loops, takeover system messages, plus real user
    chats that originated on v2 with no chat-ai ancestor. The
    `messages` table grew by +30K rows above chat-ai over the 8 days
    leading up to 2026-06-26 (Sentry issue YRAL-RISHI-AGENT-56);
    that's healthy growth, not a data loss.

    The ONE meaningful A4-violation signal is `v2_count <
    chat_ai_count`: chat-ai shipped rows that V2 didn't apply.
    That's what this verifier fires on — a one-sided assertion
    using a negative tolerance bound. The +diff side is silent by
    design.

    A future PR that restores the symmetric `abs(diff) > tol` check
    will re-introduce 600+ Sentry events per week of false-positive
    drift alerts. Don't.
    """
    watermark_iso = payload["watermark_iso"]
    # created_at columns are TIMESTAMP (naive). See _to_naive_utc.
    watermark_dt = _to_naive_utc(watermark_iso)
    drifts = {}
    total_drift = 0
    for table, expected in payload["layer_1_row_counts"].items():
        if table not in CHECKED_TABLES:
            continue
        actual = await v2_pool.fetchval(
            f"SELECT COUNT(*) FROM {table} WHERE created_at < $1",
            watermark_dt,
        )
        diff = int(actual) - int(expected)
        # One-sided: only fire when v2 is BEHIND chat-ai
        # (i.e. ETL dropped rows). v2 ahead = v2-native growth, expected.
        # Threshold unchanged at 50 rows — the direction was wrong, not
        # the magnitude.
        if diff < -HOURLY_DRIFT_TOLERANCE:
            drifts[table] = {"chat_ai": int(expected), "v2": int(actual), "diff": diff}
            total_drift += abs(diff)
    passed = not drifts
    return passed, total_drift, {"per_table": drifts, "watermark_iso": watermark_iso}


async def _verify_sample(v2_pool, payload: dict) -> tuple[bool, int, dict]:
    """For each conversation in the payload, V2 fetches its own row +
    messages, hashes content, compares against the chat-ai hashes.

    full_row_columns is compared field-by-field (excluding fields V2
    might rewrite on apply — currently none, but we tolerate updated_at
    skew since V2 might re-touch it later).
    """
    mismatches = []
    for conv in payload["conversations"]:
        conv_id = conv["id"]
        v2_conv_row = await v2_pool.fetchrow(
            "SELECT id, user_id, influencer_id, conversation_type, "
            "participant_b_id, metadata, created_at FROM conversations "
            "WHERE id = $1",
            conv_id,
        )
        if v2_conv_row is None:
            mismatches.append({"conversation_id": conv_id, "reason": "missing in v2"})
            continue
        # Column compare (skipping updated_at since it may legitimately
        # drift if V2 re-touches the conversation).
        chat = conv["full_row_columns"]
        for col in (
            "user_id",
            "influencer_id",
            "conversation_type",
            "participant_b_id",
            "created_at",
        ):
            chat_val = chat.get(col)
            v2_val = v2_conv_row.get(col)
            # See _canonicalize_for_compare — handles the chat-ai
            # (postgres-row-to-json, trimmed-trailing-zero microseconds)
            # vs V2 (asyncpg datetime → isoformat, always 6 digits)
            # rendering gap.
            chat_str = _canonicalize_for_compare(chat_val)
            v2_str = _canonicalize_for_compare(v2_val)
            if chat_str != v2_str:
                mismatches.append(
                    {
                        "conversation_id": conv_id,
                        "reason": f"column {col} differs",
                        "chat_ai": chat_str,
                        "v2": v2_str,
                    }
                )
                break
        # Per-message hash compare
        v2_msgs = await v2_pool.fetch(
            "SELECT id, content, role, message_type, created_at "
            "FROM messages WHERE conversation_id = $1 ORDER BY created_at",
            conv_id,
        )
        v2_by_id = {str(m["id"]): m for m in v2_msgs}
        for chat_msg in conv.get("messages", []):
            mid = chat_msg["id"]
            v2_msg = v2_by_id.get(str(mid))
            if v2_msg is None:
                mismatches.append(
                    {
                        "conversation_id": conv_id,
                        "message_id": mid,
                        "reason": "missing in v2",
                    }
                )
                continue
            v2_hash = hashlib.sha256(
                (v2_msg["content"] or "").encode("utf-8")
            ).hexdigest()
            if v2_hash != chat_msg["content_sha256"]:
                mismatches.append(
                    {
                        "conversation_id": conv_id,
                        "message_id": mid,
                        "reason": "content_sha256 mismatch",
                        "chat_ai": chat_msg["content_sha256"],
                        "v2": v2_hash,
                    }
                )
    passed = len(mismatches) <= SAMPLE_MISMATCH_TOLERANCE
    return (
        passed,
        len(mismatches),
        {
            "conversation_count": len(payload.get("conversations", [])),
            "mismatches": mismatches[:50],  # cap details size
            "total_mismatch_count": len(mismatches),
        },
    )


async def _verify_sentinel(v2_pool, payload: dict) -> tuple[bool, int, dict]:
    """Look for latest_message_id in V2. Not found yet = staleness
    bigger than the sync lag we tolerate.

    We don't retry inside this single verifier — staleness IS the
    answer. The "10 min retries" from the spec happen naturally: this
    runs every tick, so if the message arrives within the next 2-3
    ticks, the next sentinel verifier sees it.
    """
    latest_msg_id = payload.get("latest_message_id")
    latest_msg_at = payload.get("latest_message_created_at")
    if not latest_msg_id:
        return True, 0, {"reason": "chat-ai has no messages"}
    v2_has = await v2_pool.fetchval(
        "SELECT 1 FROM messages WHERE id = $1", latest_msg_id
    )
    if v2_has:
        return (
            True,
            0,
            {
                "latest_message_id": latest_msg_id,
                "chat_ai_created_at": latest_msg_at,
            },
        )
    # Not found yet — how stale is this?
    snapshot_iso = payload.get("snapshot_iso", "")
    snapshot_dt = datetime.fromisoformat(snapshot_iso) if snapshot_iso else None
    if snapshot_dt and snapshot_dt.tzinfo is None:
        snapshot_dt = snapshot_dt.replace(tzinfo=timezone.utc)
    age_sec = None
    if snapshot_dt:
        age_sec = int((datetime.now(timezone.utc) - snapshot_dt).total_seconds())
    passed = age_sec is not None and age_sec < SENTINEL_STALENESS_LIMIT_SEC
    return (
        passed,
        0 if passed else 1,
        {
            "latest_message_id": latest_msg_id,
            "chat_ai_created_at": latest_msg_at,
            "age_sec": age_sec,
            "staleness_limit_sec": SENTINEL_STALENESS_LIMIT_SEC,
        },
    )


# Tick layer is no longer dispatched (2026-06-26). The 5-min window
# is too narrow to distinguish ETL-not-yet-arrived from v2-native-write
# once `agent.rishi.yral.com` became a peer writer. Sentinel canary
# (15-min staleness check) covers the data-flow signal that tick
# previously claimed. Tick files keep arriving from rishi-1's exporter;
# the dispatch loop skips them via the `verifier is None` guard below.
_VERIFIERS = {
    "hourly": _verify_hourly,
    "sample": _verify_sample,
    "sentinel": _verify_sentinel,
}


# ─── orchestration ────────────────────────────────────────────────────────


async def _is_processed(v2_pool, filename: str) -> bool:
    return bool(
        await v2_pool.fetchval(
            "SELECT 1 FROM etl_integrity_results WHERE snapshot_filename = $1",
            filename,
        )
    )


async def _last_verified_at(v2_pool):
    return await v2_pool.fetchval("SELECT MAX(verified_at) FROM etl_integrity_results")


async def _record(
    v2_pool,
    layer: str,
    filename: str,
    snapshot_iso: str,
    passed: bool,
    drift_count: int,
    details: dict,
    runtime_ms: int,
):
    # snapshot_iso column is TIMESTAMPTZ → asyncpg wants an aware
    # datetime, not a string (the $3::timestamptz cast can't save us).
    await v2_pool.execute(
        """
        INSERT INTO etl_integrity_results (
            layer, snapshot_filename, snapshot_iso, passed, drift_count,
            details, runtime_ms
        ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
        ON CONFLICT (snapshot_filename) DO NOTHING
        """,
        layer,
        filename,
        _to_aware_utc(snapshot_iso),
        passed,
        drift_count,
        json.dumps(details),
        runtime_ms,
    )


# Coarse buckets for drift-count NX-dedup. The hourly verifier produces
# ~30-50 drifts per tick at steady state; without dedup that's ~12k
# Sentry events / week (Issue YRAL-RISHI-AGENT-1T + 1S, 2026-06-18
# triage). The buckets are coarse enough that a steady drift count
# collapses to ONE event per hour but a real jump (e.g. 30 → 500)
# fires a fresh event so ops still notices the escalation.
_DRIFT_BUCKETS = (1, 10, 50, 100, 500, 5000)


def _drift_bucket(count: int) -> str:
    """Map a raw drift count to a bucket label for the dedup key."""
    if count <= 0:
        return "0"
    for upper in _DRIFT_BUCKETS:
        if count <= upper:
            return f"1-{upper}" if upper == 1 else f"{_prev_bucket(upper)}-{upper}"
    return f"{_DRIFT_BUCKETS[-1]}+"


def _prev_bucket(upper: int) -> int:
    """Lower bound for a bucket whose upper bound is `upper`."""
    idx = _DRIFT_BUCKETS.index(upper)
    return 1 if idx == 0 else _DRIFT_BUCKETS[idx - 1] + 1


_SENTRY_DEDUP_TTL_SEC = 60 * 60  # 1h — one event per (layer, bucket) per hour


async def _try_set_nx(key: str, ttl_sec: int) -> bool:
    """Acquire a Redis NX key so only ONE replica fires the Sentry
    event in any given window. Returns True if THIS process owns the
    capture; False if another replica already fired OR Redis is down.
    Fail-closed-on-alert (mirrors cost_alerts._try_set_nx pattern —
    better to drop an alert than fire 5 of them during an outage)."""
    try:
        from services.session_memory import _get_redis

        redis = await _get_redis()
        if redis is None:
            return False
        result = await redis.set(key, "1", nx=True, ex=ttl_sec)
        return bool(result)
    except Exception as e:
        logger.warning(
            "etl_integrity: Sentry NX dedupe failed (treating as not-acquired): %s",
            e,
        )
        return False


async def _capture_sentry(layer: str, filename: str, drift_count: int, details: dict):
    """Soft Sentry capture, NX-deduped on (layer, drift_bucket) with
    1-hour TTL. Without dedup the hourly verifier produced ~12k events
    / week of essentially the same "etl_integrity tick FAILED" alert
    (YRAL-RISHI-AGENT-1T + 1S, 2026-06-18). Bucketing the count means
    a steady drift collapses to one event per hour but a real jump
    crosses bucket boundary and re-fires."""
    bucket = _drift_bucket(drift_count)
    nx_key = f"sentry:etl_integrity:{layer}:bucket={bucket}"
    if not await _try_set_nx(nx_key, ttl_sec=_SENTRY_DEDUP_TTL_SEC):
        return  # another replica or window already covered this fingerprint
    try:
        import sentry_sdk

        sentry_sdk.capture_message(
            f"etl_integrity {layer} FAILED ({drift_count} drifts) {filename}",
            level="error",
            extras={
                "layer": layer,
                "filename": filename,
                "drift_count": drift_count,
                "drift_bucket": bucket,
                "details": details,
            },
        )
    except Exception:
        pass


async def run_once(v2_pool) -> dict:
    """One full integrity pass — list new files, verify each, record."""
    creds = _load_s3_credentials()
    if creds is None:
        return {"status": "disabled", "reason": "S3 credentials not mounted"}
    s3 = _make_s3_client(creds)

    cursor = await _last_verified_at(v2_pool)
    cursor_iso = cursor.isoformat() if cursor else None
    files = await asyncio.to_thread(_list_integrity_sync, s3, cursor_iso)
    if not files:
        return {"status": "ok", "files_verified": 0}

    counts = {"verified": 0, "passed": 0, "failed": 0}
    for obj in files:
        if await _is_processed(v2_pool, obj["filename"]):
            continue
        verifier = _VERIFIERS.get(obj["layer"])
        if verifier is None:
            # tick files keep arriving from rishi-1; the verifier was
            # dropped 2026-06-26. Skip silently.
            continue
        t0 = time.monotonic()
        try:
            payload = await asyncio.to_thread(_download_json_sync, s3, obj["key"])
            snapshot_iso = payload.get("snapshot_iso") or payload.get("watermark_iso")
            if not snapshot_iso:
                snapshot_iso = obj["last_modified"].isoformat()
            passed, drift_count, details = await verifier(v2_pool, payload)
            runtime_ms = int((time.monotonic() - t0) * 1000)
            await _record(
                v2_pool,
                obj["layer"],
                obj["filename"],
                snapshot_iso,
                passed,
                drift_count,
                details,
                runtime_ms,
            )
            counts["verified"] += 1
            counts["passed" if passed else "failed"] += 1
            if not passed:
                # Downgrade to WARNING (was ERROR) so Sentry's logging
                # integration doesn't auto-capture in addition to the
                # explicit _capture_sentry call below. Without this,
                # every failed tick produced TWO Sentry events (the log
                # line + the explicit message) which doubled the noise
                # YRAL-RISHI-AGENT-1T + 1S were generating.
                logger.warning(
                    "etl_integrity %s FAILED file=%s drift=%d details=%s",
                    obj["layer"],
                    obj["filename"],
                    drift_count,
                    details,
                )
                await _capture_sentry(
                    obj["layer"], obj["filename"], drift_count, details
                )
            else:
                logger.info(
                    "etl_integrity %s passed file=%s runtime_ms=%d",
                    obj["layer"],
                    obj["filename"],
                    runtime_ms,
                )
        except Exception as e:
            logger.warning(
                "etl_integrity %s verifier crashed on %s: %s: %s",
                obj["layer"],
                obj["filename"],
                type(e).__name__,
                e,
            )
    return {"status": "ok", **counts}


async def integrity_loop():
    from database import get_pool
    from kill_switch import is_enabled

    await asyncio.sleep(INITIAL_DELAY_SEC)
    if _load_s3_credentials() is None:
        logger.info("etl_integrity: s3 credentials not mounted; verifier idle")
    while True:
        try:
            # Emergency kill-switch — env symmetry. Non-Gemini, but
            # included so ops can quiesce the whole background side.
            if not is_enabled("integrity"):
                await asyncio.sleep(INTEGRITY_INTERVAL_SEC)
                continue
            v2_pool = await get_pool()
            await run_once(v2_pool)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("etl_integrity loop tick failed (non-fatal): %s", e)
        await asyncio.sleep(INTEGRITY_INTERVAL_SEC)


# ─── status helpers for /admin/etl-integrity* endpoints ──────────────────


async def get_status(v2_pool) -> dict:
    """Summary across all four layers — latest result per layer + 24h
    pass/fail counts. Cheap query: one row per layer at most."""
    creds_mounted = _load_s3_credentials() is not None
    latest = await v2_pool.fetch(
        """
        SELECT DISTINCT ON (layer) layer, snapshot_filename, snapshot_iso,
               passed, drift_count, details, runtime_ms, verified_at
        FROM etl_integrity_results
        ORDER BY layer, verified_at DESC
        """
    )
    fail_24h = await v2_pool.fetchval(
        """
        SELECT COUNT(*) FROM etl_integrity_results
        WHERE passed = false AND verified_at > NOW() - INTERVAL '24 hours'
        """
    )
    pass_24h = await v2_pool.fetchval(
        """
        SELECT COUNT(*) FROM etl_integrity_results
        WHERE passed = true AND verified_at > NOW() - INTERVAL '24 hours'
        """
    )
    return {
        "s3_credentials_mounted": creds_mounted,
        "pass_count_24h": int(pass_24h or 0),
        "fail_count_24h": int(fail_24h or 0),
        "latest_per_layer": [dict(r) for r in latest],
    }


async def get_details(v2_pool, layer: str, hours: int) -> dict:
    """Drill-in: every result for `layer` in the last `hours` hours,
    newest first. Capped at 500 to avoid runaway payload."""
    rows = await v2_pool.fetch(
        """
        SELECT layer, snapshot_filename, snapshot_iso, passed, drift_count,
               details, runtime_ms, verified_at
        FROM etl_integrity_results
        WHERE layer = $1 AND verified_at > NOW() - ($2 || ' hours')::interval
        ORDER BY verified_at DESC
        LIMIT 500
        """,
        layer,
        str(hours),
    )
    return {
        "layer": layer,
        "hours": hours,
        "result_count": len(rows),
        "results": [dict(r) for r in rows],
    }


async def get_staleness(v2_pool) -> dict:
    """How stale is V2 vs. chat-ai's latest? Pulls the most recent
    sentinel pass and compares V2's max(messages.created_at) against
    chat-ai's latest_message_created_at."""
    sentinel = await v2_pool.fetchrow(
        """
        SELECT details, snapshot_iso, verified_at
        FROM etl_integrity_results
        WHERE layer = 'sentinel' AND passed = true
        ORDER BY verified_at DESC LIMIT 1
        """
    )
    v2_latest_msg = await v2_pool.fetchval("SELECT MAX(created_at) FROM messages")
    if sentinel is None:
        return {
            "has_sentinel_data": False,
            "v2_latest_message_created_at": (
                v2_latest_msg.isoformat() if v2_latest_msg else None
            ),
        }
    details = sentinel["details"]
    if isinstance(details, str):
        details = json.loads(details)
    chat_ai_latest_iso = details.get("chat_ai_created_at")
    lag_sec = None
    if chat_ai_latest_iso and v2_latest_msg:
        chat_dt = datetime.fromisoformat(chat_ai_latest_iso)
        if chat_dt.tzinfo is None:
            chat_dt = chat_dt.replace(tzinfo=timezone.utc)
        v2_dt = v2_latest_msg
        if v2_dt.tzinfo is None:
            v2_dt = v2_dt.replace(tzinfo=timezone.utc)
        lag_sec = int((chat_dt - v2_dt).total_seconds())
    return {
        "has_sentinel_data": True,
        "last_sentinel_at": sentinel["verified_at"].isoformat(),
        "chat_ai_latest_message_created_at": chat_ai_latest_iso,
        "v2_latest_message_created_at": (
            v2_latest_msg.isoformat() if v2_latest_msg else None
        ),
        "lag_sec": lag_sec,
    }
