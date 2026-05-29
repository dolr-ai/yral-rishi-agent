"""Hourly data-integrity verifier for the chat-ai → v2 ETL.

Three checks per pass; results recorded in etl_integrity_checks:
  1. row_count          — per-table chat_ai vs v2 count diff
  2. sample_conversations — N random recent chat-ai conversations have
                            matching messages in v2 (content + created_at +
                            message_type)
  3. fk_integrity       — v2-side: orphaned conversations (no influencer)
                          and orphaned messages (no conversation)

Status thresholds:
  pass   — exact match (row_count diff <= MAX_DRIFT_ROWS), 0 sample
           mismatches, 0 orphans
  warn   — diff between MAX_DRIFT_ROWS and FAIL_DRIFT_ROWS, OR 1-2 sample
           mismatches
  fail   — diff > FAIL_DRIFT_ROWS, OR 3+ sample mismatches, OR ANY orphans

Reads CHAT_AI_DATABASE_URL via the existing etl_chat_ai pool helper. If
unset, the loop logs once and idles.
"""

import asyncio
import json
import logging
import time

from services.etl_chat_ai import _get_chat_ai_pool, _chat_ai_dsn

logger = logging.getLogger(__name__)


INTEGRITY_INTERVAL_SEC = 60 * 60  # 1 hour
INITIAL_DELAY_SEC = 10 * 60  # 10 min so the ETL warms first

# Row-count thresholds (chat-ai - v2). ETL runs every 5 min; chat-ai writes
# can outpace by a few hundred rows mid-tick. > 5000 = real problem.
MAX_DRIFT_ROWS = 500
FAIL_DRIFT_ROWS = 5000

SAMPLE_CONVERSATIONS = 20
WARN_SAMPLE_MISMATCHES = 1
FAIL_SAMPLE_MISMATCHES = 3

CHECKED_TABLES = ("ai_influencers", "conversations", "messages")


# ─── row_count check ──────────────────────────────────────────────────────


async def _check_row_count(src_pool, v2_pool, table: str) -> dict:
    t0 = time.monotonic()
    src_n = await src_pool.fetchval(f"SELECT COUNT(*) FROM {table}")
    v2_n = await v2_pool.fetchval(f"SELECT COUNT(*) FROM {table}")
    diff = int(src_n) - int(v2_n)
    abs_diff = abs(diff)
    if abs_diff <= MAX_DRIFT_ROWS:
        status = "pass"
    elif abs_diff <= FAIL_DRIFT_ROWS:
        status = "warn"
    else:
        status = "fail"
    return {
        "check_type": "row_count",
        "table_name": table,
        "chat_ai_count": int(src_n),
        "v2_count": int(v2_n),
        "diff": int(diff),
        "details": json.dumps(
            {"max_drift": MAX_DRIFT_ROWS, "fail_drift": FAIL_DRIFT_ROWS}
        ),
        "status": status,
        "runtime_ms": int((time.monotonic() - t0) * 1000),
    }


# ─── sample_conversations check ───────────────────────────────────────────


async def _check_sample_conversations(src_pool, v2_pool) -> dict:
    """Pull N random recent chat-ai conversations, verify each has matching
    messages in v2 by (id, content, created_at, message_type)."""
    t0 = time.monotonic()
    conv_rows = await src_pool.fetch(
        f"""
        SELECT id FROM conversations
        WHERE created_at < NOW() - INTERVAL '15 minutes'
          AND influencer_id IS NOT NULL
        ORDER BY random()
        LIMIT {SAMPLE_CONVERSATIONS}
        """
    )
    if not conv_rows:
        return {
            "check_type": "sample_conversations",
            "table_name": None,
            "chat_ai_count": 0,
            "v2_count": 0,
            "diff": 0,
            "details": json.dumps({"note": "no eligible conversations"}),
            "status": "pass",
            "runtime_ms": int((time.monotonic() - t0) * 1000),
        }

    conv_ids = [r["id"] for r in conv_rows]

    src_msgs = await src_pool.fetch(
        """
        SELECT conversation_id, id, content, created_at, message_type
        FROM messages
        WHERE conversation_id = ANY($1::text[])
        """,
        conv_ids,
    )
    v2_msgs = await v2_pool.fetch(
        """
        SELECT conversation_id, id, content, created_at, message_type
        FROM messages
        WHERE conversation_id = ANY($1::text[])
        """,
        conv_ids,
    )

    def _key(m):
        return (
            str(m["conversation_id"]),
            str(m["id"]),
            (m["content"] or "").strip(),
            m["created_at"].isoformat() if m["created_at"] else None,
            m["message_type"],
        )

    src_set = {_key(m) for m in src_msgs}
    v2_set = {_key(m) for m in v2_msgs}

    missing = src_set - v2_set  # in chat-ai but not v2
    mismatched_conv_ids = sorted({m[0] for m in missing})[:10]
    mismatch_count = len(mismatched_conv_ids)

    if mismatch_count == 0:
        status = "pass"
    elif mismatch_count < FAIL_SAMPLE_MISMATCHES:
        status = "warn"
    else:
        status = "fail"

    return {
        "check_type": "sample_conversations",
        "table_name": None,
        "chat_ai_count": len(src_msgs),
        "v2_count": len(v2_msgs),
        "diff": len(missing),
        "details": json.dumps(
            {
                "sampled_conversations": len(conv_ids),
                "mismatched_conversation_ids": mismatched_conv_ids,
                "missing_message_count": len(missing),
            }
        ),
        "status": status,
        "runtime_ms": int((time.monotonic() - t0) * 1000),
    }


# ─── fk_integrity check (v2-side) ─────────────────────────────────────────


async def _check_fk_integrity(v2_pool) -> dict:
    t0 = time.monotonic()
    # Orphaned conversations: influencer_id set but no matching ai_influencers row
    orphan_convs = await v2_pool.fetchval(
        """
        SELECT COUNT(*) FROM conversations c
        WHERE c.influencer_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM ai_influencers i WHERE i.id = c.influencer_id)
        """
    )
    # Orphaned messages: conversation_id set but no matching conversations row
    orphan_msgs = await v2_pool.fetchval(
        """
        SELECT COUNT(*) FROM messages m
        WHERE NOT EXISTS (SELECT 1 FROM conversations c WHERE c.id = m.conversation_id)
        """
    )
    orphans = int(orphan_convs) + int(orphan_msgs)
    status = "pass" if orphans == 0 else "fail"
    return {
        "check_type": "fk_integrity",
        "table_name": None,
        "chat_ai_count": None,
        "v2_count": int(orphans),
        "diff": int(orphans),
        "details": json.dumps(
            {
                "orphan_conversations": int(orphan_convs),
                "orphan_messages": int(orphan_msgs),
            }
        ),
        "status": status,
        "runtime_ms": int((time.monotonic() - t0) * 1000),
    }


# ─── run_once + loop + file logger ────────────────────────────────────────


async def _record(v2_pool, check: dict):
    await v2_pool.execute(
        """
        INSERT INTO etl_integrity_checks (
            check_type, table_name, chat_ai_count, v2_count, diff,
            details, status, runtime_ms
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
        """,
        check["check_type"],
        check["table_name"],
        check["chat_ai_count"],
        check["v2_count"],
        check["diff"],
        check["details"],
        check["status"],
        check["runtime_ms"],
    )


def _log_to_file(check: dict):
    """Append a one-line summary to /tmp/etl_integrity.log. Per Rishi's
    spec — file-only, no alerts. Operator tails this when investigating."""
    try:
        with open("/tmp/etl_integrity.log", "a") as f:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            f.write(
                f"{ts} [{check['status'].upper():4s}] {check['check_type']} "
                f"table={check['table_name']} diff={check['diff']} "
                f"runtime_ms={check['runtime_ms']}\n"
            )
    except Exception as e:
        logger.debug(f"etl_integrity: log write failed: {e}")


async def run_once(v2_pool) -> dict:
    src_pool = await _get_chat_ai_pool()
    if src_pool is None:
        return {"status": "disabled", "reason": "CHAT_AI_DATABASE_URL not set"}

    results = []
    # Row count per table
    for table in CHECKED_TABLES:
        try:
            check = await _check_row_count(src_pool, v2_pool, table)
            await _record(v2_pool, check)
            _log_to_file(check)
            results.append(check)
        except Exception as e:
            logger.warning(f"etl_integrity row_count[{table}] failed: {e}")

    # Sample conversations
    try:
        check = await _check_sample_conversations(src_pool, v2_pool)
        await _record(v2_pool, check)
        _log_to_file(check)
        results.append(check)
    except Exception as e:
        logger.warning(f"etl_integrity sample_conversations failed: {e}")

    # FK integrity (v2-side, doesn't need src_pool)
    try:
        check = await _check_fk_integrity(v2_pool)
        await _record(v2_pool, check)
        _log_to_file(check)
        results.append(check)
    except Exception as e:
        logger.warning(f"etl_integrity fk_integrity failed: {e}")

    return {"status": "ok", "checks": results}


async def integrity_loop():
    from database import get_pool

    await asyncio.sleep(INITIAL_DELAY_SEC)
    if not _chat_ai_dsn():
        logger.info(
            "etl_integrity: CHAT_AI_DATABASE_URL not set; integrity verifier idle"
        )

    while True:
        try:
            v2_pool = await get_pool()
            await run_once(v2_pool)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"etl_integrity loop tick failed (non-fatal): {e}")
        await asyncio.sleep(INTEGRITY_INTERVAL_SEC)


# ─── status helper for /admin/etl-integrity endpoint ──────────────────────


async def get_status(v2_pool) -> dict:
    """Latest result per check_type + counts of failed checks in the last
    24h. Operator-facing snapshot."""
    rows = await v2_pool.fetch(
        """
        SELECT DISTINCT ON (check_type, table_name)
            check_type, table_name, chat_ai_count, v2_count, diff,
            details, status, runtime_ms, checked_at
        FROM etl_integrity_checks
        ORDER BY check_type, table_name, checked_at DESC
        """
    )
    fail_24h = await v2_pool.fetchval(
        """
        SELECT COUNT(*) FROM etl_integrity_checks
        WHERE status = 'fail' AND checked_at > NOW() - INTERVAL '24 hours'
        """
    )
    warn_24h = await v2_pool.fetchval(
        """
        SELECT COUNT(*) FROM etl_integrity_checks
        WHERE status = 'warn' AND checked_at > NOW() - INTERVAL '24 hours'
        """
    )
    return {
        "chat_ai_database_url_set": bool(_chat_ai_dsn()),
        "fail_count_24h": int(fail_24h or 0),
        "warn_count_24h": int(warn_24h or 0),
        "latest_per_check": [dict(r) for r in rows],
    }
