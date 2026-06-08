"""Phase 25.4 + 25.9 — admin hot-swap endpoint + HTML dashboard for LLM routing.

Two routes share the URL `/admin/llm-routing`:
  - GET  /admin/llm-routing            → HTML dashboard (Phase 25.9, browser-bookmarkable)
  - GET  /admin/llm-routing.json       → JSON (machine consumers)
  - PATCH /admin/llm-routing/{process} → hot-edit override (machine, Phase 25.4)
  - DELETE /admin/llm-routing/{process} → remove override (machine, Phase 25.4)
  - POST /admin/llm-routing/page/update/{process} → form-submit edit (browser, 25.9)
  - POST /admin/llm-routing/page/delete/{process} → form-submit reset (browser, 25.9)

Per docs/PHASE-25-DESIGN.md and the ADHD-friendly editability rule:
every cap/mapping/limit must be two-click editable from the dashboard.

Persistence: llm_process_config table (migration 026). On a successful
write, the registry's in-memory cache reloads — change takes effect on
the next call, no restart needed.

Cost + rejection stats: read from llm_costs table (migrations 027/028).
"""

import html as _html
import logging
from urllib.parse import quote as _urlquote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from auth import get_current_user
from database import get_pool
from services import llm_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin — LLM routing"])


def _check_admin_auth(request: Request) -> str:
    """Same auth shape as admin_dashboard — header OR ?token=. Returns
    the principal ID for the audit-trail (updated_by column)."""
    if request.headers.get("Authorization"):
        return get_current_user(request)
    token = request.query_params.get("token")
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Provide a JWT either as 'Authorization: Bearer <jwt>' header OR as ?token=<jwt> query param.",
        )
    # Re-route through canonical validator
    fake = type("R", (), {"headers": {"Authorization": f"Bearer {token}"}})()
    return get_current_user(fake)


def _routing_payload() -> dict:
    """The JSON shape that both the .json endpoint and the JS-free HTML
    dashboard read from. Single source of truth for the routing view."""
    return {
        "processes": [
            llm_registry.current_config(p) for p in llm_registry.PROCESS_NAMES
        ],
        "providers": {
            name: {
                "concurrency_cap": meta.get("concurrency_cap"),
                "cost_basis": meta.get("cost_basis"),
                "supports_chat": meta.get("supports_chat", False),
                "supports_stream": meta.get("supports_stream", False),
                "supports_transcribe": meta.get("supports_transcribe", False),
            }
            for name, meta in llm_registry.PROVIDERS.items()
        },
    }


async def _cost_stats_per_process(pool) -> dict[str, dict]:
    """Per-process 24h call count + failure count + 24h cost + 7d cost.
    Returns {} on missing-table (Rule 9 — code deploys before migration).
    Safe to render dashboard tile with zero data."""
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                  process,
                  count(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') AS calls_24h,
                  count(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours' AND outcome != 'success') AS failures_24h,
                  COALESCE(sum(cost_usd) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours'), 0) AS cost_24h,
                  COALESCE(sum(cost_usd) FILTER (WHERE created_at > NOW() - INTERVAL '7 days'), 0) AS cost_7d
                FROM llm_costs
                WHERE created_at > NOW() - INTERVAL '7 days'
                GROUP BY process
                """
            )
    except Exception as e:
        logger.warning("_cost_stats_per_process skipped: %s", e)
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        calls = int(r["calls_24h"] or 0)
        fails = int(r["failures_24h"] or 0)
        out[r["process"]] = {
            "calls_24h": calls,
            "failures_24h": fails,
            "rejection_pct": round(100.0 * fails / calls, 2) if calls else 0.0,
            "cost_24h_usd": float(r["cost_24h"] or 0),
            "cost_7d_usd": float(r["cost_7d"] or 0),
        }
    return out


async def _summary_stats(pool) -> dict:
    """Top-of-page summary: real $/24h, synthetic compute share/24h,
    total calls, overall rejection rate."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  COALESCE(sum(cost_usd) FILTER (WHERE cost_basis = 'real'), 0) AS real_24h,
                  COALESCE(sum(cost_usd) FILTER (WHERE cost_basis = 'synthetic'), 0) AS synthetic_24h,
                  count(*) AS calls_24h,
                  count(*) FILTER (WHERE outcome != 'success') AS failures_24h
                FROM llm_costs
                WHERE created_at > NOW() - INTERVAL '24 hours'
                """
            )
    except Exception as e:
        logger.warning("_summary_stats skipped: %s", e)
        return {
            "real_24h_usd": 0.0,
            "synthetic_24h_usd": 0.0,
            "calls_24h": 0,
            "failures_24h": 0,
            "rejection_pct": 0.0,
        }
    calls = int(row["calls_24h"] or 0)
    fails = int(row["failures_24h"] or 0)
    return {
        "real_24h_usd": float(row["real_24h"] or 0),
        "synthetic_24h_usd": float(row["synthetic_24h"] or 0),
        "calls_24h": calls,
        "failures_24h": fails,
        "rejection_pct": round(100.0 * fails / calls, 2) if calls else 0.0,
    }


def _render_html_page(
    *, routing: dict, costs: dict, summary: dict, token: str | None
) -> str:
    """Render the Phase 25.9 ADHD-friendly LLM routing dashboard.

    No JS. Per-row inline edit form posts to /admin/llm-routing/page/update/{process}.
    Reset (delete override) posts to /admin/llm-routing/page/delete/{process}.
    JWT threads through every form action via ?token=... so the browser-bookmark
    flow works without header injection."""
    token_q = f"?token={_urlquote(token)}" if token else ""
    rows_html: list[str] = []
    providers = sorted(routing["providers"].keys())

    for cfg in routing["processes"]:
        p = cfg["process"]
        c = costs.get(p, {})
        # Inline form per row — provider dropdown + model + timeout + Save/Reset.
        options = "".join(
            f'<option value="{pv}"{" selected" if pv == cfg["provider"] else ""}>{pv}</option>'
            for pv in providers
        )
        rej_pct = c.get("rejection_pct", 0.0)
        rej_color = (
            "#2e7d32" if rej_pct < 1 else ("#f57c00" if rej_pct < 10 else "#c62828")
        )
        rows_html.append(f"""
<tr>
  <td><code>{_html.escape(p)}</code></td>
  <td>
    <form method="post" action="/admin/llm-routing/page/update/{_urlquote(p)}{token_q}" style="display:flex;gap:6px;align-items:center;">
      <select name="provider">{options}</select>
      <input type="text" name="model" value="{_html.escape(cfg["model"])}" size="32">
      <input type="number" name="timeout_sec" value="{cfg.get("timeout_sec", 60.0)}" step="0.1" size="5">
      <button type="submit" style="background:#1976d2;color:#fff;border:0;padding:4px 10px;border-radius:3px;cursor:pointer;">Save</button>
    </form>
  </td>
  <td style="text-align:right;">{c.get("calls_24h", 0)}</td>
  <td style="text-align:right;color:{rej_color};">{c.get("failures_24h", 0)} ({rej_pct}%)</td>
  <td style="text-align:right;">${c.get("cost_24h_usd", 0):.6f}</td>
  <td style="text-align:right;">${c.get("cost_7d_usd", 0):.6f}</td>
  <td><code>{cfg.get("cost_basis", "real")}</code></td>
  <td>
    <form method="post" action="/admin/llm-routing/page/delete/{_urlquote(p)}{token_q}" style="display:inline;" onsubmit="return confirm('Reset {_html.escape(p)} to LLM_DEFAULTS?');">
      <button type="submit" style="background:#fff;border:1px solid #c62828;color:#c62828;padding:4px 8px;border-radius:3px;cursor:pointer;">Reset</button>
    </form>
  </td>
</tr>""")

    summary_html = f"""
<div style="display:flex;gap:24px;margin:16px 0;padding:16px;background:#f5f5f5;border-radius:6px;align-items:center;">
  <div><b>Real $/24h:</b> <span style="color:#c62828;font-size:1.3em;">${summary["real_24h_usd"]:.4f}</span></div>
  <div><b>Synthetic compute/24h:</b> <span style="color:#2e7d32;font-size:1.3em;">${summary["synthetic_24h_usd"]:.6f}</span></div>
  <div><b>Calls/24h:</b> {summary["calls_24h"]}</div>
  <div><b>Failures/24h:</b> {summary["failures_24h"]} ({summary["rejection_pct"]}%)</div>
  <div style="margin-left:auto;">
    <a href="/admin/llm-routing/db-overrides{token_q}" style="background:#fff;border:1px solid #1976d2;color:#1976d2;padding:6px 12px;border-radius:3px;text-decoration:none;font-size:13px;">View raw DB overrides →</a>
  </div>
</div>"""

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><title>LLM routing — yral-rishi-agent</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 20px; max-width: 1400px; }}
  h1 {{ font-size: 1.4em; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ padding: 8px 6px; text-align: left; border-bottom: 1px solid #e0e0e0; }}
  th {{ background: #fafafa; font-weight: 600; }}
  code {{ font-family: 'SF Mono', Menlo, monospace; font-size: 12px; }}
  select, input {{ padding: 3px 6px; font-size: 12px; border: 1px solid #ccc; border-radius: 3px; }}
  .note {{ color: #666; font-size: 12px; margin: 12px 0; }}
</style>
</head><body>
<h1>LLM routing — Phase 25.9</h1>
<p class="note">Hot-edit which provider/model serves each process. Saves write to <code>llm_process_config</code>; in-memory cache reloads on every save. No redeploy needed. Cost columns read from <code>llm_costs</code> (24h / 7d windows).</p>
{summary_html}
<table>
<thead>
<tr><th>Process</th><th>Provider / Model / Timeout</th><th>Calls 24h</th><th>Failures 24h</th><th>$/24h</th><th>$/7d</th><th>Basis</th><th>Reset</th></tr>
</thead>
<tbody>{"".join(rows_html)}</tbody>
</table>
<p class="note">cost_basis = "real" → vendor $ (Gemini/OpenAI/OpenRouter). "synthetic" → compute share (internal_vllm/Ollama) priced at the per-1k-token rate in <code>llm_registry.PROVIDERS</code>. The two are tracked separately so the cap math doesn't blur paid spend with self-hosted load.</p>
</body></html>"""


@router.get("/admin/llm-routing", response_class=HTMLResponse)
async def llm_routing_page(request: Request):
    """Phase 25.9 — browser-bookmarkable HTML dashboard. JWT-gated.

    Reloads the routing-config cache from the DB BEFORE rendering. This
    fixes the multi-replica drift bug (2026-06-08): without the reload,
    a refresh that lands on a stale replica would show the pre-Save
    state, making the operator think the Save didn't persist when the DB
    actually has it. ~5ms overhead per dashboard load, completely
    negligible at operator-action volume."""
    _check_admin_auth(request)
    pool = await get_pool()
    try:
        await llm_registry.reload_config_from_db(pool)
    except Exception as e:
        logger.warning("llm_routing_page: reload skipped: %s", e)
    routing = _routing_payload()
    costs = await _cost_stats_per_process(pool)
    summary = await _summary_stats(pool)
    token = request.query_params.get("token")
    return HTMLResponse(
        content=_render_html_page(
            routing=routing, costs=costs, summary=summary, token=token
        )
    )


@router.get("/admin/llm-routing.json")
async def llm_routing_json(request: Request):
    """Phase 25.4 — JSON shape for machine/API consumers. JWT-gated.
    Reloads cache from DB before serving — same rationale as the HTML
    page above. Machine consumers (e.g. ops scripts) need accuracy even
    more than humans."""
    _check_admin_auth(request)
    pool = await get_pool()
    try:
        await llm_registry.reload_config_from_db(pool)
    except Exception as e:
        logger.warning("llm_routing_json: reload skipped: %s", e)
    return _routing_payload()


async def _read_raw_db_overrides(pool) -> list[dict]:
    """Read raw rows from `llm_process_config`. Returns empty list if the
    table is empty or doesn't exist (e.g. migration 026 not yet applied).
    Used by the View-DB-overrides page so Rishi can verify what's pinned at
    the DB level vs what's just a code default."""
    try:
        rows = await pool.fetch(
            "SELECT process, provider, model, timeout_sec, updated_at, updated_by "
            "FROM llm_process_config ORDER BY process"
        )
        return [dict(r) for r in rows]
    except Exception:
        return []


def _render_db_overrides_page(*, rows: list[dict], token: str | None) -> str:
    """Plain table view of raw `llm_process_config` rows. Linked from the
    main routing dashboard. Adds visibility for the non-programmer
    operator: "is this routing decision a DB pin or a code default?"

    Rows in this table take precedence over the LLM_DEFAULTS in
    llm_registry.py. If a process is NOT in this table, it falls through
    to env override (LLM_PROCESS__<NAME>) then to LLM_DEFAULTS.
    """
    token_q = f"?token={_urlquote(token)}" if token else ""

    if not rows:
        body = """
<div style="margin:32px 0;padding:24px;background:#e8f5e9;border:1px solid #66bb6a;border-radius:6px;">
  <h2 style="margin-top:0;color:#2e7d32;">No DB overrides — table is empty</h2>
  <p>Every process is using the code default from <code>app/services/llm_registry.py:LLM_DEFAULTS</code>. This is the cleanest state — no hidden pinning to remember about later.</p>
  <p>To pin a process to a specific (provider, model): go back to the routing dashboard, change the dropdown, and click <b>Save</b>.</p>
</div>"""
    else:
        # Build the table.
        row_html: list[str] = []
        for r in rows:
            timeout = r.get("timeout_sec")
            timeout_str = f"{float(timeout):.1f}s" if timeout is not None else "—"
            updated_at = r.get("updated_at")
            updated_str = (
                updated_at.strftime("%Y-%m-%d %H:%M UTC") if updated_at else "—"
            )
            updated_by = _html.escape(str(r.get("updated_by") or "?"))
            row_html.append(f"""
<tr>
  <td><code>{_html.escape(r["process"])}</code></td>
  <td><code>{_html.escape(r["provider"])}</code></td>
  <td><code>{_html.escape(r.get("model") or "")}</code></td>
  <td style="text-align:right;">{timeout_str}</td>
  <td>{updated_str}</td>
  <td><code>{updated_by}</code></td>
</tr>""")
        body = f"""
<p class="note">Each row is a hot-pin in the <code>llm_process_config</code> table. These take precedence over the code defaults in <code>llm_registry.LLM_DEFAULTS</code>. Use the <b>Reset</b> button on the main routing dashboard to delete a row from this table.</p>
<table>
<thead>
<tr><th>Process</th><th>Provider</th><th>Model</th><th>Timeout</th><th>Updated at</th><th>Updated by</th></tr>
</thead>
<tbody>{"".join(row_html)}</tbody>
</table>
<p class="note">Total overrides: {len(rows)}. Any process not listed here uses its code default from <code>LLM_DEFAULTS</code> — see <code>app/services/llm_registry.py</code>.</p>"""

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><title>DB overrides — yral-rishi-agent</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 20px; max-width: 1400px; }}
  h1 {{ font-size: 1.4em; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ padding: 8px 6px; text-align: left; border-bottom: 1px solid #e0e0e0; }}
  th {{ background: #fafafa; font-weight: 600; }}
  code {{ font-family: 'SF Mono', Menlo, monospace; font-size: 12px; }}
  .note {{ color: #666; font-size: 12px; margin: 12px 0; }}
  a.back {{ color: #1976d2; text-decoration: none; font-size: 13px; }}
  a.back:hover {{ text-decoration: underline; }}
</style>
</head><body>
<p><a class="back" href="/admin/llm-routing{token_q}">← Back to routing dashboard</a></p>
<h1>Raw DB overrides — <code>llm_process_config</code> table</h1>
{body}
</body></html>"""


@router.get("/admin/llm-routing/db-overrides", response_class=HTMLResponse)
async def llm_routing_db_overrides(request: Request):
    """View raw rows of `llm_process_config` — what's actually pinned in
    the DB. JWT-gated. Read-only — no edit/delete buttons here; for that
    use the main routing dashboard. The point of this page is verification
    transparency for the non-programmer operator: 'is X a code default or
    a DB pin?'"""
    _check_admin_auth(request)
    pool = await get_pool()
    rows = await _read_raw_db_overrides(pool)
    token = request.query_params.get("token")
    return HTMLResponse(content=_render_db_overrides_page(rows=rows, token=token))


@router.patch("/admin/llm-routing/{process}")
async def patch_routing(process: str, body: dict, request: Request):
    """Hot-edit one process's provider/model. Body shape:
        {"provider": "gemini", "model": "gemini-2.5-flash", "timeout_sec": 60}
    timeout_sec is optional; omit to inherit the LLM_DEFAULTS value.

    Capability check: if the new provider doesn't support the modality
    that process needs (e.g. switching audio_transcription to a provider
    without supports_transcribe), we reject with 400 so the operator
    sees the mismatch immediately."""
    principal = _check_admin_auth(request)

    provider = (body.get("provider") or "").strip()
    model = (body.get("model") or "").strip()
    timeout_sec = body.get("timeout_sec")

    if not provider:
        raise HTTPException(status_code=400, detail="'provider' is required")
    if not model:
        raise HTTPException(status_code=400, detail="'model' is required")
    if process not in llm_registry.PROCESS_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown process '{process}'. Known: {sorted(llm_registry.PROCESS_NAMES)}",
        )
    if provider not in llm_registry.PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown provider '{provider}'. Known: {sorted(llm_registry.PROVIDERS.keys())}",
        )

    # Capability check — refuse to break the modality contract.
    provider_meta = llm_registry.PROVIDERS[provider]
    if process == "audio_transcription" and not provider_meta.get(
        "supports_transcribe", False
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"provider '{provider}' does not support audio transcription. "
                f"Today only providers with supports_transcribe=True work for "
                f"the audio_transcription process."
            ),
        )
    # Phase 21αβ.H12 — vision capability guard. Same shape as the
    # audio guard above. The 2026-06-08 bug surfaced because there was
    # no such guard: Rishi flipped user_chat_main → runpod_vllm
    # (supports_vision=False) and image chats silently failed. This
    # gate refuses analogous flips for user_chat_main_multimodal.
    if process == "user_chat_main_multimodal" and not provider_meta.get(
        "supports_vision", False
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"provider '{provider}' does not support vision/image input. "
                f"Today only providers with supports_vision=True work for "
                f"the user_chat_main_multimodal process."
            ),
        )

    if isinstance(timeout_sec, str):
        try:
            timeout_sec = float(timeout_sec)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="'timeout_sec' must be a number"
            ) from None

    pool = await get_pool()
    try:
        await llm_registry.upsert_override(
            pool,
            process=process,
            provider=provider,
            model=model,
            timeout_sec=timeout_sec,
            updated_by=principal,
        )
    except Exception as e:
        # Most likely cause: llm_process_config table doesn't exist yet
        # (Rule 9 — Rishi hasn't pg_dumped + applied migration 026 yet).
        logger.error("llm_registry.upsert_override failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail=(
                "llm_process_config table not available yet (migration 026 "
                "pending). Hot-edits will work after Rishi applies the "
                "migration; meanwhile, use LLM_PROCESS__<NAME>=<provider>/<model> "
                "env vars."
            ),
        ) from e

    logger.info(
        "llm_routing PATCH: process=%s provider=%s model=%s by=%s",
        process,
        provider,
        model,
        principal,
    )
    return {"process": process, "current": llm_registry.current_config(process)}


@router.delete("/admin/llm-routing/{process}")
async def delete_routing(process: str, request: Request):
    """Remove an override — process falls back to env + LLM_DEFAULTS."""
    principal = _check_admin_auth(request)
    if process not in llm_registry.PROCESS_NAMES:
        raise HTTPException(status_code=400, detail=f"unknown process '{process}'")
    pool = await get_pool()
    try:
        deleted = await llm_registry.delete_override(
            pool, process=process, updated_by=principal
        )
    except Exception as e:
        logger.error("llm_registry.delete_override failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail="llm_process_config table not available yet (migration 026 pending)",
        ) from e

    return {
        "process": process,
        "deleted": deleted,
        "current": llm_registry.current_config(process),
    }


# ─── Phase 25.9 — form-submit handlers for the HTML dashboard ────────────
#
# HTML <form method="post"> can't do PATCH/DELETE natively, so the
# browser-friendly path uses POST with explicit "update" / "delete"
# sub-paths. They delegate to the same registry helpers as the JSON
# PATCH/DELETE endpoints. Auth is the same (header OR ?token=).
# Response is a 303 redirect back to /admin/llm-routing so the table
# refreshes with the new state on the next browser GET.


@router.post("/admin/llm-routing/page/update/{process}")
async def page_update_routing(
    process: str,
    request: Request,
    provider: str = Form(...),
    model: str = Form(...),
    timeout_sec: str = Form(""),
):
    """Form-submit version of PATCH /admin/llm-routing/{process}. The
    JS-free dashboard at /admin/llm-routing posts here on Save."""
    principal = _check_admin_auth(request)
    if process not in llm_registry.PROCESS_NAMES:
        raise HTTPException(status_code=400, detail=f"unknown process '{process}'")
    if provider not in llm_registry.PROVIDERS:
        raise HTTPException(status_code=400, detail=f"unknown provider '{provider}'")

    # Capability check — same as PATCH endpoint
    provider_meta = llm_registry.PROVIDERS[provider]
    if process == "audio_transcription" and not provider_meta.get(
        "supports_transcribe", False
    ):
        raise HTTPException(
            status_code=400,
            detail=f"provider '{provider}' does not support audio transcription",
        )
    # Phase 21αβ.H12 — vision capability guard (form endpoint).
    if process == "user_chat_main_multimodal" and not provider_meta.get(
        "supports_vision", False
    ):
        raise HTTPException(
            status_code=400,
            detail=f"provider '{provider}' does not support vision/image input",
        )

    tsec: float | None
    if timeout_sec and timeout_sec.strip():
        try:
            tsec = float(timeout_sec)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="timeout_sec must be a number"
            ) from None
    else:
        tsec = None

    pool = await get_pool()
    try:
        await llm_registry.upsert_override(
            pool,
            process=process,
            provider=provider,
            model=(model or "").strip(),
            timeout_sec=tsec,
            updated_by=principal,
        )
    except Exception as e:
        logger.error("llm_registry.upsert_override failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)[:300]) from e

    logger.info(
        "llm_routing page-update: process=%s provider=%s model=%s by=%s",
        process,
        provider,
        model,
        principal,
    )
    token = request.query_params.get("token")
    target = "/admin/llm-routing"
    if token:
        target += f"?token={_urlquote(token)}"
    return RedirectResponse(url=target, status_code=303)


@router.post("/admin/llm-routing/page/delete/{process}")
async def page_delete_routing(process: str, request: Request):
    """Form-submit version of DELETE /admin/llm-routing/{process}. The
    JS-free dashboard at /admin/llm-routing posts here on Reset."""
    principal = _check_admin_auth(request)
    if process not in llm_registry.PROCESS_NAMES:
        raise HTTPException(status_code=400, detail=f"unknown process '{process}'")
    pool = await get_pool()
    try:
        await llm_registry.delete_override(pool, process=process, updated_by=principal)
    except Exception as e:
        logger.error("llm_registry.delete_override failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)[:300]) from e

    logger.info("llm_routing page-delete: process=%s by=%s", process, principal)
    token = request.query_params.get("token")
    target = "/admin/llm-routing"
    if token:
        target += f"?token={_urlquote(token)}"
    return RedirectResponse(url=target, status_code=303)
