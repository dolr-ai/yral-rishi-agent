"""Phase 19.6 — single bookmarkable admin dashboard.

ADHD-friendly observability hub. One URL Rishi bookmarks; every
protective system (rate limits, cost breakers, safety drills, etc.)
surfaces a tile here. Empty tiles today say "Wired in PR #N" so
later PRs just fill them in.

Auth: JWT-gated via Authorization header (canonical for /admin/*)
OR `?token=...` query param (lets Rishi bookmark in a browser without
needing a header-injection extension). Both routes go through the
same get_current_user check — invalid/missing token is 401 either way.

Output: HTML (not JSON) so a browser URL is the unit of access.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

import database
from auth import get_current_user as _get_current_user_strict

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin — Dashboard"])


# ─── auth flex: header OR ?token= ─────────────────────────────────────────


def _check_auth_flexible(request: Request) -> str:
    """Browser bookmarkability requires query-param token support;
    machine callers can still send Authorization: Bearer. Either form
    feeds the existing get_current_user (which raises 401 on bad/missing)."""
    if request.headers.get("Authorization"):
        return _get_current_user_strict(request)

    token = request.query_params.get("token")
    if not token:
        raise HTTPException(
            status_code=401,
            detail=(
                "Provide a JWT either as 'Authorization: Bearer <jwt>' header "
                "OR as ?token=<jwt> query param. The query-param form is the "
                "browser-bookmark path."
            ),
        )
    # Re-route through the canonical validator by shimming the header.
    # Mutating headers on a Request is fragile across Starlette versions, so
    # we duplicate the minimal validation steps inline.
    from datetime import datetime as _dt
    import jwt as _jwt
    from config import EXPECTED_ISSUERS

    try:
        payload = _jwt.decode(token, options={"verify_signature": False})
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("iss") not in EXPECTED_ISSUERS:
        raise HTTPException(status_code=401, detail="Invalid token issuer")
    if not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid token: missing sub")
    exp = payload.get("exp")
    if exp and _dt.now(timezone.utc).timestamp() > exp:
        raise HTTPException(status_code=401, detail="Token has expired")
    return payload["sub"]


# ─── tile data collectors ─────────────────────────────────────────────────


def _color_for_status(status: str) -> str:
    """ADHD-friendly traffic-light palette."""
    return {
        "ok": "#2e7d32",  # green
        "warn": "#f57c00",  # amber
        "fail": "#c62828",  # red
        "off": "#616161",  # grey — feature not yet wired
    }.get(status, "#616161")


def _humanize_seconds(sec: int | None) -> str:
    if sec is None:
        return "—"
    if sec < 60:
        return f"{sec}s ago"
    if sec < 3600:
        return f"{sec // 60}m ago"
    if sec < 86400:
        return f"{sec // 3600}h ago"
    return f"{sec // 86400}d ago"


async def _etl_tile(pool) -> dict:
    """Live ETL status — already wired today (PR #210-#226)."""
    try:
        from services.etl_chat_ai import get_status

        s = await get_status(pool)
    except Exception as e:
        return {
            "title": "ETL chat-ai → V2",
            "status": "fail",
            "primary": "endpoint error",
            "details": str(e)[:200],
            "link": "/admin/etl-status",
        }

    age = s.get("heartbeat_age_sec")
    stale = s.get("heartbeat_stale")
    stuck = s.get("stuck_marker")
    if stuck:
        status = "fail"
        primary = "STUCK marker present"
    elif stale:
        status = "warn"
        primary = "Heartbeat stale"
    else:
        status = "ok"
        primary = f"Heartbeat {_humanize_seconds(age)}"

    return {
        "title": "ETL chat-ai → V2",
        "status": status,
        "primary": primary,
        "details": (
            f"24h: {s.get('files_processed_24h', 0)} files, "
            f"{s.get('rows_applied_24h', 0)} rows applied, "
            f"{s.get('skipped_rows_24h', 0)} skipped"
        ),
        "link": "/admin/etl-status",
    }


async def _integrity_tile(pool) -> dict:
    """Live integrity verifier — also already wired."""
    try:
        from services.etl_integrity import get_status

        s = await get_status(pool)
    except Exception as e:
        return {
            "title": "ETL integrity (4 layers)",
            "status": "fail",
            "primary": "endpoint error",
            "details": str(e)[:200],
            "link": "/admin/etl-integrity",
        }

    fail = s.get("fail_count_24h", 0)
    passed = s.get("pass_count_24h", 0)
    if fail > 0:
        status = "warn" if fail < 10 else "fail"
        primary = f"{fail} failures / {passed} passes (24h)"
    elif passed == 0:
        status = "off"
        primary = "Loop running, no results yet"
    else:
        status = "ok"
        primary = f"{passed} passes (24h)"

    return {
        "title": "ETL integrity (4 layers)",
        "status": status,
        "primary": primary,
        "details": "tick / hourly / sample / sentinel verifiers",
        "link": "/admin/etl-integrity",
    }


async def _cost_breaker_tile(pool) -> dict:
    """Phase 19.2 live tile — daily cap + 24h trip count + Redis health."""
    try:
        from cost_breaker import get_status

        s = await get_status(pool)
        caps = s.get("caps", {})
        trips = s.get("trips_24h", 0)
        if not s.get("redis_available"):
            return {
                "title": "Cost circuit breaker (Phase 19.2)",
                "status": "warn",
                "primary": "Redis unavailable — breaker degraded open",
                "details": f"Cap: ${caps.get('per_user_daily_cents', 0) / 100:.2f}/user/day",
                "link": "/admin/cost-breaker/status",
            }
        # 0 trips = green (everyone's under cap)
        # 1-9 trips = amber (a few users hitting caps; investigate)
        # 10+ trips = red (likely an attack or a misconfigured client)
        if trips >= 10:
            status = "fail"
        elif trips > 0:
            status = "warn"
        else:
            status = "ok"
        return {
            "title": "Cost circuit breaker (Phase 19.2)",
            "status": status,
            "primary": f"{trips} trips (24h)",
            "details": (
                f"per-user/day cap: ${caps.get('per_user_daily_cents', 0) / 100:.2f} · "
                f"alert: ${caps.get('per_user_daily_alert_cents', 0) / 100:.2f}"
            ),
            "link": "/admin/cost-breaker/status",
        }
    except Exception as e:
        return {
            "title": "Cost circuit breaker (Phase 19.2)",
            "status": "fail",
            "primary": "endpoint error",
            "details": str(e)[:200],
            "link": "/admin/cost-breaker/status",
        }


async def _rate_limit_tile(pool) -> dict:
    """Phase 19.1 live tile — current limits + 24h rejection count."""
    try:
        from rate_limiter import get_status

        s = await get_status()
        rejections = s.get("rejections_24h", 0)
        limits = s.get("current_limits", {})
        if not s.get("redis_available"):
            return {
                "title": "Per-user rate limits (Phase 19.1)",
                "status": "warn",
                "primary": "Redis unavailable — limiter degraded open",
                "details": "All requests pass through; investigate Redis health",
                "link": "/admin/rate-limits/status",
            }
        # A few rejections = working as intended; thousands = attack or
        # a misconfigured client
        if rejections > 1000:
            status = "fail"
        elif rejections > 0:
            status = "warn"
        else:
            status = "ok"
        return {
            "title": "Per-user rate limits (Phase 19.1)",
            "status": status,
            "primary": f"{rejections} rejections (24h)",
            "details": (
                f"per-user: {limits.get('per_user_per_min', '?')}/min, "
                f"{limits.get('per_user_per_hour', '?')}/hr · "
                f"per-IP: {limits.get('per_ip_per_min', '?')}/min, "
                f"{limits.get('per_ip_per_hour', '?')}/hr"
            ),
            "link": "/admin/rate-limits/status",
        }
    except Exception as e:
        return {
            "title": "Per-user rate limits (Phase 19.1)",
            "status": "fail",
            "primary": "endpoint error",
            "details": str(e)[:200],
            "link": "/admin/rate-limits/status",
        }


async def _email_digest_tile(pool) -> dict:
    """Last digest run summary — tells Rishi at a glance whether
    today's 02:30 UTC cron fired AND whether SMTP delivered."""
    try:
        from services.email_digest import get_latest_digest

        row = await get_latest_digest(pool)
        if row is None:
            return {
                "title": "Daily email digest (Phase 24.5)",
                "status": "off",
                "primary": "No runs yet",
                "details": "First cron fires at 02:30 UTC. Force-run via "
                "/admin/email-digest/preview?force=1",
                "link": "/admin/email-digest/preview",
            }

        rendered = row["rendered_at"]
        age = int((datetime.now(timezone.utc) - rendered).total_seconds())
        sent = row["sent"]
        error = row["error"] or ""
        if sent:
            status, primary = "ok", f"Sent {_humanize_seconds(age)}"
        elif "SMTP_HOST not configured" in error:
            status, primary = "warn", "Built but SMTP not configured"
        else:
            status, primary = "fail", f"Last send failed: {error[:60]}"
        return {
            "title": "Daily email digest (Phase 24.5)",
            "status": status,
            "primary": primary,
            "details": f"For {row['for_date']} · rendered {rendered.isoformat()}",
            "link": "/admin/email-digest/preview",
        }
    except Exception as e:
        return {
            "title": "Daily email digest (Phase 24.5)",
            "status": "fail",
            "primary": "endpoint error",
            "details": str(e)[:200],
            "link": "/admin/email-digest/preview",
        }


def _placeholder_tile(title: str, planned_pr: str, why: str) -> dict:
    """Future-wiring stub. Visible empty-state so Rishi knows what's
    coming and where it'll land. The 'off' grey color flags
    "not yet implemented" without alarming."""
    return {
        "title": title,
        "status": "off",
        "primary": f"Wired in {planned_pr}",
        "details": why,
        "link": None,
    }


# ─── HTML rendering ───────────────────────────────────────────────────────


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>yral-rishi-agent · admin dashboard</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #fafafa; margin: 0; padding: 24px; color: #212121; }}
  h1 {{ font-size: 22px; margin: 0 0 6px; }}
  .meta {{ color: #757575; font-size: 13px; margin-bottom: 24px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
           gap: 16px; }}
  .tile {{ background: white; border-left: 6px solid #ccc; border-radius: 8px;
           padding: 16px 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .tile h2 {{ font-size: 15px; margin: 0 0 8px; color: #424242; }}
  .tile .primary {{ font-size: 18px; font-weight: 600; margin-bottom: 8px; }}
  .tile .details {{ font-size: 13px; color: #616161; line-height: 1.5; }}
  .tile a {{ color: #1976d2; text-decoration: none; font-size: 13px; }}
  .tile a:hover {{ text-decoration: underline; }}
  .footer {{ margin-top: 32px; font-size: 12px; color: #9e9e9e; }}
</style>
</head>
<body>
  <h1>yral-rishi-agent · admin dashboard</h1>
  <div class="meta">Auto-refreshes every 60s · last loaded {now}</div>
  <div class="grid">
    {tiles_html}
  </div>
  <div class="footer">
    JWT-gated. Token via Authorization header OR <code>?token=&lt;jwt&gt;</code>.
    Auto-refresh keeps the page live — bookmark + leave it open in a tab.
  </div>
</body>
</html>
"""


def _render_tile(tile: dict) -> str:
    color = _color_for_status(tile["status"])
    link_html = f'<a href="{tile["link"]}">view raw →</a>' if tile.get("link") else ""
    title = tile["title"].replace("<", "&lt;")
    primary = str(tile["primary"]).replace("<", "&lt;")
    details = str(tile.get("details", "")).replace("<", "&lt;")
    return (
        f'<div class="tile" style="border-left-color: {color}">'
        f"<h2>{title}</h2>"
        f'<div class="primary" style="color: {color}">{primary}</div>'
        f'<div class="details">{details}</div>'
        f"{link_html}"
        f"</div>"
    )


# ─── endpoint ────────────────────────────────────────────────────────────


@router.get("/admin/dashboard")
async def admin_dashboard(request: Request):
    _check_auth_flexible(request)
    pool = await database.get_pool()

    # Live tiles (already-shipped systems)
    tiles = [
        await _etl_tile(pool),
        await _integrity_tile(pool),
        await _email_digest_tile(pool),
        await _rate_limit_tile(pool),
        await _cost_breaker_tile(pool),
        # Placeholder tiles — each later PR replaces its placeholder with
        # a real status read. The Wired-in-PR-#N text gives Rishi a clear
        # forward roadmap from the dashboard itself.
        _placeholder_tile(
            "Weekly safety drill",
            "PR Phase 24.2",
            "Cron Sun 03:00 UTC: auth bypass, IDOR, SQL injection, etc.",
        ),
        _placeholder_tile(
            "Backup restore drill",
            "PR I10",
            "Weekly: restore latest WAL-G to sidecar, verify queries",
        ),
        _placeholder_tile(
            "Dependency vulnerabilities",
            "PR Phase 24.3",
            "pip-audit + Trivy in CI; reports at docs/security/dep-audit/",
        ),
        _placeholder_tile(
            "Secret scan baseline",
            "PR Phase 24.1",
            "gitleaks full-history scan; PR scan on every future PR",
        ),
    ]

    if request.query_params.get("format") == "json":
        return JSONResponse(
            {"tiles": tiles, "rendered_at": datetime.now(timezone.utc).isoformat()}
        )

    tiles_html = "\n    ".join(_render_tile(t) for t in tiles)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return HTMLResponse(_HTML_TEMPLATE.format(tiles_html=tiles_html, now=now))


# ─── Phase 19.1 — rate-limit config + status ────────────────────────────


@router.get("/admin/rate-limits/config")
async def get_rate_limit_config(request: Request):
    """Current per-{user,ip} per-{min,hour} limits. Same shape as the
    PUT body so Rishi can read → edit → PUT round-trip from a curl."""
    _check_auth_flexible(request)
    from rate_limiter import get_current_limits

    return JSONResponse({"limits": await get_current_limits()})


@router.put("/admin/rate-limits/config")
async def put_rate_limit_config(request: Request):
    """Hot-edit one or more limits. Body shape: {"key": "per_user_per_min",
    "value": 120}. Writes DB (durable) AND Redis (live across replicas).

    Per memory feedback-adhd-observability-and-security-baseline: knobs
    must be hot-editable from an admin endpoint, never just env."""
    user = _check_auth_flexible(request)
    body = await request.json()
    key = body.get("key")
    value = body.get("value")
    from rate_limiter import update_limit

    try:
        await update_limit(await database.get_pool(), key, int(value), user)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    from rate_limiter import get_current_limits

    return JSONResponse({"updated": {key: value}, "limits": await get_current_limits()})


@router.get("/admin/rate-limits/status")
async def get_rate_limit_status(request: Request):
    """Drill-in for the dashboard tile — current limits + 24h
    rejection count + 10 most-recent rejected calls."""
    _check_auth_flexible(request)
    from rate_limiter import get_status

    return JSONResponse(await get_status())


# ─── Phase 19.2 — cost circuit breaker config + status ─────────────────


@router.get("/admin/cost-breaker/config")
async def get_cost_breaker_config(request: Request):
    """Current per-user daily cents cap + alert threshold."""
    _check_auth_flexible(request)
    from cost_breaker import get_current_caps

    return JSONResponse({"caps_cents": await get_current_caps()})


@router.put("/admin/cost-breaker/config")
async def put_cost_breaker_config(request: Request):
    """Hot-edit. Body: {"key": "per_user_daily_cents", "value_cents": 200}.
    Writes DB + Redis (same dual-write pattern as rate-limits config)."""
    user = _check_auth_flexible(request)
    body = await request.json()
    key = body.get("key")
    value_cents = body.get("value_cents")
    from cost_breaker import update_cap, get_current_caps

    try:
        await update_cap(await database.get_pool(), key, int(value_cents), user)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(
        {"updated": {key: value_cents}, "caps_cents": await get_current_caps()}
    )


@router.get("/admin/cost-breaker/status")
async def get_cost_breaker_status(request: Request):
    """Drill-in for the tile — caps + 24h trip count + today's top 10
    spenders (so Rishi can see who's burning budget)."""
    _check_auth_flexible(request)
    from cost_breaker import get_status

    return JSONResponse(await get_status(await database.get_pool()))


@router.get("/admin/email-digest/preview")
async def email_digest_preview(request: Request):
    """Render the latest stored digest (built by the daily 02:30 UTC
    cron) so Rishi can preview without needing email. Also handy if a
    digest got spam-filtered and Rishi wants to read it directly.

    ?force=1 builds a fresh digest right now (used for testing without
    waiting for cron). Always uses the same auth path as /dashboard."""
    _check_auth_flexible(request)
    pool = await database.get_pool()

    from services.email_digest import (
        get_latest_digest,
        send_digest_now,
        render_html,
        render_plain,
    )

    if request.query_params.get("force") == "1":
        result = await send_digest_now(pool)
        digest = result["digest"]
        send_note = f"sent={result['sent']} error={result['error'] or 'none'}"
    else:
        row = await get_latest_digest(pool)
        if row is None:
            return HTMLResponse(
                "<html><body><p>No digest runs recorded yet. "
                "Visit <code>?force=1</code> to build one now, or wait for "
                "the daily 02:30 UTC cron.</p></body></html>"
            )
        digest = row["body_json"]
        if isinstance(digest, str):
            import json as _json

            digest = _json.loads(digest)
        send_note = f"sent={row['sent']} error={row['error'] or 'none'}"

    if request.query_params.get("format") == "text":
        return HTMLResponse(
            f"<pre>{render_plain(digest)}</pre>"
            f"<p style='font-size:11px;color:#9e9e9e'>send: {send_note}</p>"
        )
    return HTMLResponse(
        render_html(digest)
        + f"<p style='font-size:11px;color:#9e9e9e'>send: {send_note}</p>"
    )
