"""Phase 21α.B6 — admin endpoint for the cost circuit breaker.

Three surfaces:

  GET  /admin/cost-breaker            → HTML dashboard (config + recent
                                        trips). Bookmarkable via ?token=.
  GET  /admin/cost-breaker.json       → JSON for machine consumers.
  PATCH /admin/cost-breaker/config    → hot-edit one config row at a
                                        time. Writes DB + Redis cache;
                                        single SQL UPDATE disables B6
                                        in 1 second.

Auth pattern mirrors `/admin/llm-routing` + `/admin/backup-health` —
Bearer header OR `?token=` query param so the operator can bookmark
`https://agent.rishi.yral.com/admin/cost-breaker?token=…`.

The PATCH path is the OPERATOR INTERFACE FOR THE 7-DAY SHADOW REVIEW:
once Sarvesh confirms mobile 503 UX (per 2026-06-16 Q2) + the
shadow event log shows zero YRAL-team trips for ≥7 days (per Q3),
flip `b6_enforce=true` via a single PATCH.
"""

import html as _html
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from auth import get_current_user
from services import cost_breaker

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin — Cost circuit breaker"])


def _check_admin_auth(request: Request) -> str:
    """Same shape as other /admin/* — Bearer header OR ?token=. Returns
    the principal ID so PATCH writes can stamp updated_by."""
    if request.headers.get("Authorization"):
        return get_current_user(request)
    token = request.query_params.get("token")
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Provide JWT as 'Authorization: Bearer <jwt>' header OR ?token=<jwt>.",
        )
    fake = type("R", (), {"headers": {"Authorization": f"Bearer {token}"}})()
    return get_current_user(fake)


# ─── GET JSON ───────────────────────────────────────────────────────────


@router.get("/admin/cost-breaker.json")
async def get_cost_breaker_json(request: Request):
    """Machine-readable status. Same payload the HTML page renders.
    Pure read; no production state change."""
    _check_admin_auth(request)
    summary = await cost_breaker.status_summary()
    events = await cost_breaker.recent_events(limit=50, since_hours=24)
    return JSONResponse(
        content={
            "as_of": datetime.now(timezone.utc).isoformat(),
            "config": summary["config"],
            "yral_team_principal_ids": summary["yral_team_principal_ids"],
            "last_24h_trips": summary["last_24h_trips"],
            "recent_events": [_serialize_event(e) for e in events],
        },
        headers={"Cache-Control": "no-store"},
    )


def _serialize_event(e: dict) -> dict:
    out = dict(e)
    occurred_at = out.get("occurred_at")
    if isinstance(occurred_at, datetime):
        out["occurred_at"] = occurred_at.isoformat()
    cost = out.get("cost_seen_usd")
    if cost is not None:
        out["cost_seen_usd"] = float(cost)
    threshold = out.get("threshold_usd")
    if threshold is not None:
        out["threshold_usd"] = float(threshold)
    return out


# ─── PATCH config (hot-edit) ────────────────────────────────────────────


@router.patch("/admin/cost-breaker/config")
async def patch_cost_breaker_config(request: Request):
    """Hot-edit a single config row.

    Body: {"key": "<one of the b6_* keys>", "value": "<string>"}

    Validation:
      - `key` must be in the known set (see cost_breaker._DEFAULTS).
      - `value` shape is NOT validated here — `cost_breaker._parse_*`
        helpers handle malformed values by falling back to defaults
        (FAIL OPEN per the 5 hard properties).

    Audit: `updated_by` stamped with the JWT principal_id.

    THE 1-second kill switch:
        PATCH /admin/cost-breaker/config {"key": "b6_enabled", "value": "false"}
    """
    principal_id = _check_admin_auth(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    key = body.get("key")
    value = body.get("value")
    if not isinstance(key, str):
        raise HTTPException(status_code=422, detail="`key` required, must be string")
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="`value` required, must be string")
    try:
        await cost_breaker.update_config(key=key, value=value, updated_by=principal_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return JSONResponse(
        content={
            "updated": True,
            "key": key,
            "value": value,
            "updated_by": principal_id,
        }
    )


# ─── GET HTML dashboard ─────────────────────────────────────────────────


@router.get("/admin/cost-breaker", response_class=HTMLResponse)
async def get_cost_breaker_html(request: Request):
    """Operator-facing HTML. Renders config table + recent trips table
    + 24h trip-count summary + the 7-day shadow review checklist."""
    _check_admin_auth(request)
    summary = await cost_breaker.status_summary()
    events = await cost_breaker.recent_events(limit=50, since_hours=24)
    cfg = summary["config"]
    yral_team = summary["yral_team_principal_ids"]
    counts = summary["last_24h_trips"]

    enabled = cfg.get("b6_enabled", "false").lower() in ("true", "1", "yes")
    enforce = cfg.get("b6_enforce", "false").lower() in ("true", "1", "yes")

    status_chip = (
        '<span style="background:#c62828;color:white;padding:3px 8px;border-radius:3px">ENFORCE</span>'
        if enforce
        else '<span style="background:#f57c00;color:white;padding:3px 8px;border-radius:3px">SHADOW</span>'
        if enabled
        else '<span style="background:#666;color:white;padding:3px 8px;border-radius:3px">DISABLED</span>'
    )

    config_rows = "".join(
        f"<tr><td><code>{_html.escape(k)}</code></td>"
        f"<td><code>{_html.escape(str(cfg.get(k) or ''))}</code></td></tr>"
        for k in sorted(cfg.keys())
    )

    event_rows = (
        "".join(_render_event_row(e, yral_team) for e in events)
        or "<tr><td colspan='7'>No trips in last 24h.</td></tr>"
    )

    yral_team_html = (
        "<ul>"
        + "".join(f"<li><code>{_html.escape(p)}</code></li>" for p in yral_team)
        + "</ul>"
        if yral_team
        else "<em>(empty — Rishi to add Sarvesh/Saikat/Neha principal_ids)</em>"
    )

    return HTMLResponse(
        content=f"""<!doctype html>
<html><head>
<meta charset='utf-8'>
<title>Cost circuit breaker (B6)</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1100px;
         margin: 2em auto; padding: 0 1em; line-height: 1.4; color: #222; }}
  h1 {{ margin-bottom: 0.2em; }}
  .card {{ border: 1px solid #ddd; border-radius: 4px; padding: 1em; margin: 1em 0;
          background: #fafafa; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ padding: 6px 10px; text-align: left; border-bottom: 1px solid #eee; }}
  th {{ background: #f0f0f0; }}
  code {{ background: #f4f4f4; padding: 1px 4px; border-radius: 3px; font-size: 0.92em; }}
  .yral-team {{ background: #fff9c4; }}
  .blocked  {{ background: #ffe5e5; }}
</style>
</head><body>

<h1>Cost circuit breaker (B6) {status_chip}</h1>
<p style="color:#666;font-size:0.9em">
  Snapshot as of {_html.escape(datetime.now(timezone.utc).isoformat())}.
  Refresh to recompute.
</p>

<div class='card'>
  <h2 style='margin-top:0'>Last 24h trips</h2>
  <p>per_user_daily: <b>{counts.get("per_user_daily", 0)}</b> |
     global_hourly: <b>{counts.get("global_hourly", 0)}</b> |
     blocked: <b>{counts.get("blocked", 0)}</b></p>
  <p style='color:#666;font-size:0.85em'>
    Shadow trips count too (call still ran). Only `blocked` rows
    actually returned 503 to the client.
  </p>
</div>

<div class='card'>
  <h2 style='margin-top:0'>Recent trips (last 24h, up to 50)</h2>
  <table>
    <thead><tr>
      <th>occurred_at</th><th>scope</th><th>user_id</th><th>process</th>
      <th>provider</th><th>cost / threshold</th><th>mode</th>
    </tr></thead>
    {event_rows}
  </table>
  <p style='color:#666;font-size:0.85em'>
    Highlighted rows: yellow = YRAL-team principal_id (any trip here
    BLOCKS the enforce-flip per 2026-06-16 brief Q3). Red = actually
    blocked (`enforce_mode=true`).
  </p>
</div>

<div class='card'>
  <h2 style='margin-top:0'>Current config</h2>
  <table><thead><tr><th>key</th><th>value</th></tr></thead>{config_rows}</table>
  <p style='color:#666;font-size:0.85em'>
    Hot-edit via <code>PATCH /admin/cost-breaker/config</code>
    with body <code>{{"key": "...", "value": "..."}}</code>.
    Changes propagate in &lt;60s (cache TTL).
  </p>
</div>

<div class='card'>
  <h2 style='margin-top:0'>YRAL team principal_ids (zero-trip gate)</h2>
  {yral_team_html}
  <p style='color:#666;font-size:0.85em'>
    Per 2026-06-16 brief Q3: the enforce-flip is BLOCKED until
    (a) 7 days of shadow data AND (b) zero trips on any of the
    principal_ids listed above. Add more via
    <code>PATCH config b6_yral_team_principal_ids</code>.
  </p>
</div>

<div class='card'>
  <h2 style='margin-top:0'>The 1-second kill switch</h2>
  <pre style='background:#eee;padding:8px;border-radius:3px;overflow:auto'>
PATCH https://agent.rishi.yral.com/admin/cost-breaker/config
Authorization: Bearer &lt;admin JWT&gt;
Content-Type: application/json

{{"key": "b6_enabled", "value": "false"}}</pre>
  <p style='color:#666;font-size:0.85em'>
    Same effect via raw SQL:
    <code>UPDATE circuit_breaker_config SET value='false' WHERE key='b6_enabled';</code>.
    Workers pick up the change within 60s (config cache TTL).
  </p>
</div>

</body></html>"""
    )


def _render_event_row(e: dict, yral_team: list[str]) -> str:
    user_id = e.get("user_id") or ""
    is_yral = user_id and user_id in yral_team
    is_blocked = bool(e.get("call_blocked"))
    cls = "yral-team" if is_yral else "blocked" if is_blocked else ""
    cost = float(e.get("cost_seen_usd") or 0)
    threshold = float(e.get("threshold_usd") or 0)
    occurred = e.get("occurred_at")
    occurred_str = (
        occurred.isoformat() if isinstance(occurred, datetime) else str(occurred)
    )
    mode_str = "ENFORCE-BLOCK" if is_blocked else "SHADOW"
    return (
        f"<tr class='{cls}'>"
        f"<td>{_html.escape(occurred_str)}</td>"
        f"<td><code>{_html.escape(str(e.get('scope') or ''))}</code></td>"
        f"<td><code>{_html.escape(user_id)}</code>"
        f"{' ⚠️ YRAL team' if is_yral else ''}</td>"
        f"<td>{_html.escape(str(e.get('process') or ''))}</td>"
        f"<td>{_html.escape(str(e.get('provider') or ''))}</td>"
        f"<td>${cost:.4f} / ${threshold:.4f}</td>"
        f"<td>{mode_str}</td>"
        f"</tr>"
    )
