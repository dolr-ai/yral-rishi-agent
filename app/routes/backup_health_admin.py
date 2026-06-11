"""Phase 21αβ.H10 — /admin/backup-health admin dashboard.

Surfaces "is our backup story actually working?" in 60 seconds. Reads
from three live sources:

  1. pg_stat_archiver  → WAL archive activity (last_archived_wal,
     archived_count, failed_count, last_archived_time, last_failed_time)
  2. pg_class          → per-table page-count snapshot
                          (proof tables actually exist + have data)
  3. backup_drill_runs → last WAL-G restore drill result + age
                          (audit table from migration 036)

Two routes share the URL prefix:
  - GET /admin/backup-health        → HTML dashboard (browser-bookmarkable)
  - GET /admin/backup-health.json   → JSON (machine consumers)

Both JWT-gated. Same auth shape as /admin/llm-routing (header OR
?token=). Page is text-and-table only — no JS, mirrors the JS-free
dashboard pattern from Phase 25.9.

What this does NOT do:
  - Doesn't trigger a drill — that's scripts/walg_restore_drill.sh
  - Doesn't list WAL-G base backups — that needs SSH to a patroni host
    (out of scope; the dashboard surfaces what's queryable from inside
    the agent's DB connection)
  - Doesn't write to the audit table — only reads
"""

import html as _html
import logging
from datetime import datetime, timezone
from urllib.parse import quote as _urlquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from auth import get_current_user
from database import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin — Backup health"])


# ─── auth ─────────────────────────────────────────────────────────────────


def _check_admin_auth(request: Request) -> str:
    """Same pattern as llm_routing_admin._check_admin_auth — JWT either
    as a Bearer header OR as ?token=… so the operator can bookmark
    https://agent.rishi.yral.com/admin/backup-health?token=…"""
    if request.headers.get("Authorization"):
        return get_current_user(request)
    token = request.query_params.get("token")
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Provide a JWT either as 'Authorization: Bearer <jwt>' "
            "header OR as ?token=<jwt> query param.",
        )
    fake = type("R", (), {"headers": {"Authorization": f"Bearer {token}"}})()
    return get_current_user(fake)


# ─── data sources ────────────────────────────────────────────────────────


# Critical tables the V2 mobile contract floors on. The dashboard
# shows pg_class.relpages for each so an operator can confirm at a
# glance that the rows are actually there (not just the schema).
_FLOOR_TABLES = (
    "ai_influencers",
    "conversations",
    "messages",
    # Coach surface — every applied change writes a row here
    "system_instructions_history",
    # Cost + observability — high-volume table; useful for capacity sanity
    "llm_costs",
    # Coach session content
    "coach_messages",
)


async def _wal_archive_status(pool) -> dict:
    """Read pg_stat_archiver — the single most useful WAL-G health signal."""
    row = await pool.fetchrow(
        """
        SELECT archived_count,
               failed_count,
               last_archived_wal,
               last_archived_time,
               last_failed_wal,
               last_failed_time,
               stats_reset
        FROM pg_stat_archiver
        """
    )
    if row is None:
        return {"available": False}
    out = dict(row)
    out["available"] = True
    # Age of latest successful archive — what matters most: "are we
    # still archiving WAL right now?"
    if out["last_archived_time"] is not None:
        out["last_archived_age_seconds"] = (
            datetime.now(timezone.utc) - out["last_archived_time"]
        ).total_seconds()
    else:
        out["last_archived_age_seconds"] = None
    return out


async def _per_table_pages(pool) -> list[dict]:
    """pg_class.relpages = number of 8 KB pages. Cheap snapshot of
    "this table has data" without doing a COUNT(*) on every row.

    relpages is updated by VACUUM/ANALYZE, so it can lag behind reality
    by hours on quiet tables — we show it as a sanity floor, not a
    precise row count. For exact counts, query the table directly."""
    rows = await pool.fetch(
        """
        SELECT c.relname,
               c.relpages,
               c.reltuples::bigint AS reltuples_estimate,
               pg_total_relation_size(c.oid) AS total_bytes
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND c.relname = ANY($1::text[])
        ORDER BY c.relname
        """,
        list(_FLOOR_TABLES),
    )
    return [dict(r) for r in rows]


async def _latest_drill_runs(pool, limit: int = 5) -> list[dict]:
    """Last N rows from backup_drill_runs. The MOST RECENT row's
    exit_code + finished_at drive the dashboard's PASS/FAIL/RUNNING
    badge."""
    try:
        rows = await pool.fetch(
            """
            SELECT id, drill_type, started_at, finished_at, exit_code,
                   triggered_by, sanity_results, notes
            FROM backup_drill_runs
            ORDER BY started_at DESC
            LIMIT $1
            """,
            limit,
        )
    except Exception as e:
        # Migration 036 not yet applied — return an empty list with a
        # warning the route can surface, instead of 500ing the page.
        logger.warning("backup_drill_runs query failed (migration 036?): %s", e)
        return []
    return [dict(r) for r in rows]


async def _build_payload(pool) -> dict:
    archive = await _wal_archive_status(pool)
    tables = await _per_table_pages(pool)
    drills = await _latest_drill_runs(pool)
    now = datetime.now(timezone.utc)

    # Verdict: GREEN / WARN / RED for the at-a-glance badge.
    verdict = "GREEN"
    verdict_reasons: list[str] = []

    if archive.get("available"):
        last_age = archive.get("last_archived_age_seconds")
        if last_age is None:
            verdict = "WARN"
            verdict_reasons.append("WAL archiver never archived a segment")
        elif last_age > 60 * 60:  # 1 hr
            verdict = "RED"
            verdict_reasons.append(
                f"last WAL archive was {int(last_age / 60)} min ago (>60 min)"
            )
        elif last_age > 15 * 60:  # 15 min
            verdict = "WARN"
            verdict_reasons.append(
                f"last WAL archive was {int(last_age / 60)} min ago (>15 min)"
            )
        if (archive.get("failed_count") or 0) > 0 and archive.get("last_failed_time"):
            last_fail_age = (now - archive["last_failed_time"]).total_seconds()
            if last_fail_age < 60 * 60:  # failed in last hour
                if verdict != "RED":
                    verdict = "WARN"
                verdict_reasons.append(
                    f"WAL archive failed {int(last_fail_age / 60)} min ago"
                )
    else:
        verdict = "WARN"
        verdict_reasons.append("pg_stat_archiver unavailable")

    if drills:
        latest = drills[0]
        if latest["finished_at"] is None:
            verdict = "WARN" if verdict == "GREEN" else verdict
            verdict_reasons.append("drill in progress (or hung)")
        else:
            exit_code = latest.get("exit_code")
            if exit_code != 0:
                verdict = "RED"
                verdict_reasons.append(f"latest drill FAILED (exit_code={exit_code})")
            else:
                # Drill passed — sanity-check it's recent
                drill_age = (now - latest["started_at"]).total_seconds()
                if drill_age > 8 * 24 * 60 * 60:  # 8 days
                    if verdict == "GREEN":
                        verdict = "WARN"
                    verdict_reasons.append(
                        f"latest passing drill was {int(drill_age / 86400)}d ago"
                    )
    else:
        if verdict == "GREEN":
            verdict = "WARN"
        verdict_reasons.append("no drill rows recorded yet")

    return {
        "as_of": now.isoformat(),
        "verdict": verdict,
        "verdict_reasons": verdict_reasons,
        "wal_archive": archive,
        "tables": tables,
        "drills": drills,
    }


# ─── JSON-serialize helper (datetime → ISO) ──────────────────────────────


def _iso(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _iso(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_iso(v) for v in obj]
    return obj


# ─── routes ──────────────────────────────────────────────────────────────


@router.get("/admin/backup-health.json")
async def backup_health_json(request: Request):
    """Machine-readable view. Same shape as the HTML dashboard reads."""
    _check_admin_auth(request)
    pool = await get_pool()
    payload = await _build_payload(pool)
    return _iso(payload)


@router.get("/admin/backup-health")
async def backup_health_html(request: Request):
    """JS-free HTML dashboard. Bookmark-friendly via ?token=…

    Pinned narrow (max-width 980px) to match the visual rhythm of
    /admin/llm-routing — same fonts, same card shape, same verdict
    color coding so the operator's eyes don't have to re-learn the
    page each time they bounce between them."""
    _check_admin_auth(request)
    pool = await get_pool()
    payload = await _build_payload(pool)
    token = request.query_params.get("token")
    return HTMLResponse(content=_render_html(payload, token=token))


def _verdict_color(v: str) -> str:
    return {"GREEN": "#2e7d32", "WARN": "#f57c00", "RED": "#c62828"}.get(v, "#666")


def _format_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}" if isinstance(n, float) else f"{n} {unit}"
        n /= 1024.0  # type: ignore[assignment]
    return f"{n:.1f} PB"


def _format_age(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 60 * 60:
        return f"{int(seconds / 60)} min ago"
    if seconds < 24 * 60 * 60:
        return f"{int(seconds / 3600)} hr ago"
    return f"{int(seconds / 86400)}d ago"


def _render_html(payload: dict, token: str | None) -> str:
    archive = payload.get("wal_archive") or {}
    tables = payload.get("tables") or []
    drills = payload.get("drills") or []
    verdict = payload.get("verdict") or "WARN"

    token_q = f"?token={_urlquote(token)}" if token else ""

    # Cross-link to the sibling /admin/llm-routing dashboard
    cross_link = f'<a href="/admin/llm-routing{token_q}">← LLM routing dashboard</a>'

    # WAL archive card
    last_archived_age = archive.get("last_archived_age_seconds")
    last_failed_age = None
    if archive.get("last_failed_time"):
        last_failed_age = (
            datetime.now(timezone.utc) - archive["last_failed_time"]
        ).total_seconds()

    wal_rows = "".join(
        f"<tr><td>{k}</td><td><code>{_html.escape(str(v))}</code></td></tr>"
        for k, v in (
            ("archived_count", archive.get("archived_count")),
            ("failed_count", archive.get("failed_count")),
            ("last_archived_wal", archive.get("last_archived_wal") or "—"),
            (
                "last_archived_time",
                f"{archive.get('last_archived_time') or '—'} ({_format_age(last_archived_age)})",
            ),
            ("last_failed_wal", archive.get("last_failed_wal") or "—"),
            (
                "last_failed_time",
                f"{archive.get('last_failed_time') or '—'} ({_format_age(last_failed_age)})",
            ),
            ("stats_reset", archive.get("stats_reset") or "—"),
        )
    )

    # Per-table card
    table_rows = "".join(
        f"<tr>"
        f"<td><code>{_html.escape(t['relname'])}</code></td>"
        f"<td style='text-align:right'>{t.get('relpages') or 0}</td>"
        f"<td style='text-align:right'>{t.get('reltuples_estimate') or 0:,}</td>"
        f"<td style='text-align:right'>{_format_bytes(t.get('total_bytes'))}</td>"
        f"</tr>"
        for t in tables
    )
    if not table_rows:
        table_rows = "<tr><td colspan='4'>(no rows — check public schema)</td></tr>"

    # Drill rows
    drill_rows_html: list[str] = []
    for d in drills:
        finished = d.get("finished_at")
        if finished is None:
            status_badge = '<span style="color:#f57c00">RUNNING</span>'
        elif d.get("exit_code") == 0:
            status_badge = '<span style="color:#2e7d32">PASS</span>'
        else:
            status_badge = (
                f'<span style="color:#c62828">FAIL (exit {d.get("exit_code")})</span>'
            )
        # Sanity_results may be JSON-string or already-dict depending on
        # asyncpg JSONB decoding. Stringify either way.
        sanity = d.get("sanity_results")
        if isinstance(sanity, dict):
            sanity_str = ", ".join(f"{k}={v}" for k, v in sanity.items())
        else:
            sanity_str = str(sanity or "")
        drill_rows_html.append(
            f"<tr>"
            f"<td>{status_badge}</td>"
            f"<td>{_html.escape(str(d.get('started_at') or '—'))}</td>"
            f"<td>{_html.escape(str(d.get('triggered_by') or '—'))}</td>"
            f"<td>{_html.escape(d.get('drill_type') or '—')}</td>"
            f"<td style='font-size:0.85em'>{_html.escape(sanity_str)}</td>"
            f"<td style='font-size:0.85em'>{_html.escape(d.get('notes') or '')}</td>"
            f"</tr>"
        )
    drill_rows_str = (
        "".join(drill_rows_html)
        or "<tr><td colspan='6'>No drill rows recorded yet (run scripts/walg_restore_drill.sh).</td></tr>"
    )

    reasons_html = (
        "<ul>"
        + "".join(f"<li>{_html.escape(r)}</li>" for r in payload["verdict_reasons"])
        + "</ul>"
        if payload["verdict_reasons"]
        else "<em>(no issues)</em>"
    )

    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>Backup health</title>
<style>
  body {{
    font-family: -apple-system, system-ui, sans-serif;
    max-width: 980px; margin: 2em auto; padding: 0 1em;
    color: #222; line-height: 1.4;
  }}
  h1 {{ margin-bottom: 0; }}
  .verdict {{
    display: inline-block; padding: 4px 12px; border-radius: 4px;
    color: white; font-weight: bold; margin: 0 8px;
  }}
  .card {{
    border: 1px solid #ddd; border-radius: 4px;
    padding: 1em; margin: 1em 0; background: #fafafa;
  }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{
    padding: 6px 10px; text-align: left;
    border-bottom: 1px solid #eee;
  }}
  th {{ background: #f0f0f0; }}
  code {{ background: #f4f4f4; padding: 1px 4px; border-radius: 3px; font-size: 0.92em; }}
  .nav {{ font-size: 0.9em; margin-bottom: 1.5em; }}
  .nav a {{ color: #1976d2; text-decoration: none; }}
  .nav a:hover {{ text-decoration: underline; }}
</style>
</head><body>

<div class="nav">{cross_link}</div>

<h1>Backup health
  <span class="verdict" style="background:{_verdict_color(verdict)}">{verdict}</span>
</h1>
<p style="color:#666; font-size:0.85em">
  Snapshot as of {_html.escape(payload["as_of"])} —
  refresh the page to recompute.
</p>

<div class="card">
  <h2 style="margin-top:0">Why this verdict</h2>
  {reasons_html}
</div>

<div class="card">
  <h2 style="margin-top:0">WAL archive status (pg_stat_archiver)</h2>
  <p style="color:#666; font-size:0.9em">
    What WAL-G has shipped to S3 since the last stats reset.
    A healthy cluster archives a segment every few minutes.
  </p>
  <table>{wal_rows}</table>
</div>

<div class="card">
  <h2 style="margin-top:0">Per-table snapshot (pg_class)</h2>
  <p style="color:#666; font-size:0.9em">
    Page counts are updated by VACUUM/ANALYZE so they can lag —
    use this as a sanity floor, not a precise row count.
  </p>
  <table>
    <thead><tr>
      <th>table</th>
      <th style="text-align:right">pages (8 KB)</th>
      <th style="text-align:right">tuples (est.)</th>
      <th style="text-align:right">total size</th>
    </tr></thead>
    {table_rows}
  </table>
</div>

<div class="card">
  <h2 style="margin-top:0">Recent restore drills</h2>
  <p style="color:#666; font-size:0.9em">
    Written by <code>scripts/walg_restore_drill.sh</code>.
    "RUNNING" = the drill started but hasn't called back with a result —
    likely hung or in flight.
  </p>
  <table>
    <thead><tr>
      <th>status</th>
      <th>started_at</th>
      <th>triggered_by</th>
      <th>type</th>
      <th>sanity</th>
      <th>notes</th>
    </tr></thead>
    {drill_rows_str}
  </table>
</div>

</body></html>"""
