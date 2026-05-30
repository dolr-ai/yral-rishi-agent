"""Phase 24.5 — daily email digest framework.

Sibling of Phase 19.6 (the bookmarkable dashboard). Same ADHD-friendly
rule from memory feedback-adhd-observability-and-security-baseline:
every protective system must surface here AND on the dashboard so
Rishi doesn't have to remember to check anything.

What ships in this PR
---------------------
- Plumbing: builder, sender, background loop, config knobs
- Section stubs that future PRs (rate limits, cost breaker, safety
  drill, etc.) fill in
- `GET /admin/email-digest/preview` so Rishi can read the digest in a
  browser without waiting for the cron — useful for testing AND for
  daily skim if email goes to spam

What does NOT ship today
-----------------------
- A working SMTP integration: the SMTP_* config vars must be filled in
  via Swarm secrets (out-of-band setup). Until they are, the loop
  builds the digest each day at 08:00 IST, logs the body, and stores it
  in a small recent-runs table for the preview endpoint to read.
- Real content for the placeholder sections (per the rule, those land
  in the same PR as the underlying protective system).

Send cadence
------------
Daily at 08:00 IST (= 02:30 UTC). Times this so the email lands in
Rishi's morning inbox before his work window starts. Drifting by ±5
min if the server clock is off is fine — Rishi reads digests, not
timestamps.
"""

import asyncio
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


# ─── config (env + secret-file fallback, matches the rest of the codebase) ─

DIGEST_TO_EMAIL = os.environ.get("DIGEST_TO_EMAIL", "rishi@gobazzinga.io")
DIGEST_FROM_EMAIL = os.environ.get("DIGEST_FROM_EMAIL", "agent-noreply@rishi.yral.com")
DIGEST_SUBJECT_PREFIX = "[yral-rishi-agent]"

# 08:00 IST = 02:30 UTC. Cron-target hour:minute in UTC.
DIGEST_TARGET_HOUR_UTC = 2
DIGEST_TARGET_MINUTE_UTC = 30

# How often the background loop wakes up to check whether it's time to
# send. 5 min = ±2.5 min worst-case delay from the target — acceptable.
DIGEST_TICK_INTERVAL_SEC = 5 * 60

# How many recent digest runs the preview endpoint can browse.
DIGEST_HISTORY_KEEP = 30


def _smtp_config() -> dict | None:
    """Read SMTP config. None = email send disabled (loop still runs
    and builds the digest body so the preview endpoint works)."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "use_tls": os.environ.get("SMTP_USE_TLS", "true").lower() == "true",
    }


# ─── section builders ────────────────────────────────────────────────────


async def _section_etl(pool) -> dict:
    """Live data: ETL status summary for the last 24h."""
    try:
        from services.etl_chat_ai import get_status

        s = await get_status(pool)
        return {
            "title": "ETL chat-ai → V2",
            "lines": [
                f"Files processed (24h): {s.get('files_processed_24h', 0)}",
                f"Rows applied (24h):    {s.get('rows_applied_24h', 0)}",
                f"Rows skipped (24h):    {s.get('skipped_rows_24h', 0)}",
                f"Heartbeat:             {s.get('heartbeat', 'no signal')}",
                f"STUCK marker:          {s.get('stuck_marker') or 'none'}",
            ],
        }
    except Exception as e:
        return {"title": "ETL chat-ai → V2", "lines": [f"[error: {e}]"]}


async def _section_integrity(pool) -> dict:
    """Live data: integrity verifier 24h pass/fail summary."""
    try:
        from services.etl_integrity import get_status

        s = await get_status(pool)
        return {
            "title": "ETL integrity (4 layers)",
            "lines": [
                f"Passes (24h): {s.get('pass_count_24h', 0)}",
                f"Failures (24h): {s.get('fail_count_24h', 0)}",
                f"Layers reporting: {len(s.get('latest_per_layer', []))}",
            ],
        }
    except Exception as e:
        return {"title": "ETL integrity", "lines": [f"[error: {e}]"]}


def _section_placeholder(title: str, planned_pr: str) -> dict:
    """Sibling of the dashboard placeholder. Same flip-on-PR-merge
    rule applies — the section converts from a stub to live data when
    the underlying protective system ships."""
    return {
        "title": title,
        "lines": [f"(wired in {planned_pr})"],
    }


async def _section_rate_limits(pool) -> dict:
    """Live data: rate-limit config + 24h rejection count."""
    try:
        from rate_limiter import get_status

        s = await get_status()
        limits = s.get("current_limits", {})
        return {
            "title": "Per-user rate limits (Phase 19.1)",
            "lines": [
                f"Redis available:  {s.get('redis_available')}",
                f"Rejections (24h): {s.get('rejections_24h', 0)}",
                f"per-user/min:     {limits.get('per_user_per_min', '?')}",
                f"per-user/hour:    {limits.get('per_user_per_hour', '?')}",
                f"per-ip/min:       {limits.get('per_ip_per_min', '?')}",
                f"per-ip/hour:      {limits.get('per_ip_per_hour', '?')}",
            ],
        }
    except Exception as e:
        return {"title": "Per-user rate limits", "lines": [f"[error: {e}]"]}


async def build_digest(pool) -> dict:
    """Assemble the full digest. Live sections call into existing
    services; placeholder sections are stubs the future PRs replace.

    Returned dict shape:
      {
        "rendered_at": "2026-05-30T02:30:00+00:00",
        "for_date":    "2026-05-30",
        "sections": [
            {"title": "...", "lines": ["...", ...]},
            ...
        ],
      }
    """
    now = datetime.now(timezone.utc)
    sections = [
        await _section_etl(pool),
        await _section_integrity(pool),
        await _section_rate_limits(pool),
        _section_placeholder("Cost circuit breaker", "PR Phase 19.2"),
        _section_placeholder("Weekly safety drill", "PR Phase 24.2"),
        _section_placeholder("Backup restore drill", "PR I10"),
        _section_placeholder("Dependency vulnerabilities", "PR Phase 24.3"),
        _section_placeholder("Secret scan baseline", "PR Phase 24.1"),
    ]
    return {
        "rendered_at": now.isoformat(),
        "for_date": now.strftime("%Y-%m-%d"),
        "sections": sections,
    }


# ─── rendering ──────────────────────────────────────────────────────────


def render_plain(digest: dict) -> str:
    """Plain-text body — most reliable across email clients. Rishi
    reads on phone half the time; plain text always works."""
    lines = [
        f"yral-rishi-agent daily digest — {digest['for_date']}",
        f"Generated {digest['rendered_at']}",
        "Dashboard: https://agent.rishi.yral.com/admin/dashboard",
        "",
    ]
    for sec in digest["sections"]:
        lines.append(f"── {sec['title']}")
        for ln in sec["lines"]:
            lines.append(f"   {ln}")
        lines.append("")
    return "\n".join(lines)


def render_html(digest: dict) -> str:
    """HTML body — same content, easier to skim. <pre> wrapping keeps
    column alignment when Rishi reads on desktop."""
    rows = []
    for sec in digest["sections"]:
        body = "\n".join(sec["lines"])
        rows.append(
            f"<h3 style='margin:18px 0 4px;color:#424242'>{sec['title']}</h3>"
            f"<pre style='margin:0 0 8px;font-family:Menlo,monospace;"
            f"font-size:13px;color:#212121'>{body}</pre>"
        )
    return (
        f"<html><body style='font-family:-apple-system,sans-serif;color:#212121'>"
        f"<h2>yral-rishi-agent daily digest — {digest['for_date']}</h2>"
        f"<p style='color:#757575;font-size:13px'>"
        f"Generated {digest['rendered_at']} · "
        f"<a href='https://agent.rishi.yral.com/admin/dashboard'>Live dashboard</a>"
        f"</p>"
        f"{''.join(rows)}"
        f"</body></html>"
    )


# ─── send ────────────────────────────────────────────────────────────────


def _send_email_sync(digest: dict, smtp_cfg: dict) -> tuple[bool, str]:
    """Blocking SMTP send. Called via asyncio.to_thread so it doesn't
    block the loop. Returns (ok, error_message)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{DIGEST_SUBJECT_PREFIX} digest — {digest['for_date']}"
    msg["From"] = DIGEST_FROM_EMAIL
    msg["To"] = DIGEST_TO_EMAIL
    msg.attach(MIMEText(render_plain(digest), "plain"))
    msg.attach(MIMEText(render_html(digest), "html"))

    try:
        with smtplib.SMTP(smtp_cfg["host"], smtp_cfg["port"], timeout=30) as smtp:
            if smtp_cfg["use_tls"]:
                smtp.starttls()
            if smtp_cfg["user"]:
                smtp.login(smtp_cfg["user"], smtp_cfg["password"])
            smtp.send_message(msg)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def send_digest_now(pool) -> dict:
    """Build + send (if SMTP configured) + record. Used by both the
    cron loop and the manual-trigger endpoint for testing.

    Always builds the digest body and records the run — the preview
    endpoint reads from this record regardless of whether SMTP was
    available. Operator can wire SMTP later without losing days of
    digest content."""
    digest = await build_digest(pool)
    smtp_cfg = _smtp_config()
    sent = False
    error = ""
    if smtp_cfg is None:
        error = "SMTP_HOST not configured; digest built but not sent"
        logger.info("email_digest: %s", error)
    else:
        sent, error = await asyncio.to_thread(_send_email_sync, digest, smtp_cfg)
        if not sent:
            logger.warning("email_digest: send failed: %s", error)
        else:
            logger.info("email_digest: sent to %s", DIGEST_TO_EMAIL)

    await _record_run(pool, digest, sent, error)
    return {"sent": sent, "error": error, "digest": digest}


def _parse_rendered_at(s: str) -> datetime:
    """Convert the digest's ISO string back to a tz-aware datetime so
    asyncpg accepts it for the TIMESTAMPTZ column. asyncpg validates
    types client-side BEFORE Postgres sees any ::cast, so the cast in
    SQL alone isn't enough — see memory
    feedback_audit_codebase_wide_when_fixing_typecodec for the
    fuller rule + the prior PRs (#217-#222) that traced this family of
    bugs."""
    if "T" not in s and " " in s:
        s = s.replace(" ", "T", 1)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _record_run(pool, digest: dict, sent: bool, error: str):
    """Persist the digest so the preview endpoint can read recent
    runs. The history table is small (max 30 rows kept) — bounded by
    DIGEST_HISTORY_KEEP."""
    import json as _json

    await pool.execute(
        """
        INSERT INTO email_digest_runs (rendered_at, for_date, body_json, sent, error)
        VALUES ($1, $2, $3::jsonb, $4, $5)
        """,
        _parse_rendered_at(digest["rendered_at"]),
        digest["for_date"],
        _json.dumps(digest),
        sent,
        error,
    )
    # Trim oldest runs to bound storage. Simple LIMIT-OFFSET delete.
    await pool.execute(
        """
        DELETE FROM email_digest_runs
        WHERE id IN (
            SELECT id FROM email_digest_runs
            ORDER BY rendered_at DESC
            OFFSET $1
        )
        """,
        DIGEST_HISTORY_KEEP,
    )


# ─── background loop ────────────────────────────────────────────────────


async def digest_loop():
    """Sleep until the next 08:00 IST window, then fire. Idempotent
    via for_date — won't double-send if the loop wakes twice in the
    same target minute."""
    from database import get_pool
    from kill_switch import is_enabled

    while True:
        try:
            await asyncio.sleep(DIGEST_TICK_INTERVAL_SEC)
            # Emergency kill-switch (env symmetry). Non-Gemini.
            if not is_enabled("email_digest"):
                continue
            now = datetime.now(timezone.utc)
            if not (
                now.hour == DIGEST_TARGET_HOUR_UTC
                and DIGEST_TARGET_MINUTE_UTC
                <= now.minute
                < DIGEST_TARGET_MINUTE_UTC + (DIGEST_TICK_INTERVAL_SEC // 60)
            ):
                continue
            pool = await get_pool()
            already = await pool.fetchval(
                "SELECT 1 FROM email_digest_runs WHERE for_date = $1 LIMIT 1",
                now.strftime("%Y-%m-%d"),
            )
            if already:
                continue
            await send_digest_now(pool)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("digest_loop tick failed (non-fatal): %s", e)


# ─── preview helper for /admin/email-digest/preview ─────────────────────


async def get_latest_digest(pool) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT rendered_at, for_date, body_json, sent, error
        FROM email_digest_runs
        ORDER BY rendered_at DESC
        LIMIT 1
        """
    )
    return dict(row) if row else None
