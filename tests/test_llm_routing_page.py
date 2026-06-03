"""Phase 25.9 — browser-bookmarkable LLM routing dashboard.

Source-pin tests for HTML rendering, cost-stats queries, and the
form-POST update/delete handlers that back the no-JS dashboard.
"""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


# ─── route shape ──────────────────────────────────────────────────────────


def test_get_routing_returns_html():
    """GET /admin/llm-routing must return HTML (browser-bookmarkable).
    JSON moved to /admin/llm-routing.json for machine consumers."""
    src = _read("app/routes/llm_routing_admin.py")
    assert "response_class=HTMLResponse" in src
    assert '@router.get("/admin/llm-routing"' in src


def test_json_endpoint_preserved_at_dot_json():
    """The JSON shape kept for back-compat at /admin/llm-routing.json
    so any future automation that scrapes the routing state isn't broken
    by the HTML pivot."""
    src = _read("app/routes/llm_routing_admin.py")
    assert "/admin/llm-routing.json" in src
    assert "async def llm_routing_json" in src


def test_routing_payload_single_source_of_truth():
    """Both HTML and JSON routes read from the same _routing_payload()
    helper. If a future refactor diverges them, this test breaks."""
    src = _read("app/routes/llm_routing_admin.py")
    assert "def _routing_payload(" in src


# ─── cost stats ───────────────────────────────────────────────────────────


def test_cost_stats_query_handles_missing_table():
    """Rule 9 again: dashboard must render gracefully even when
    llm_costs table doesn't exist (e.g. fresh env pre-migration-028)."""
    src = _read("app/routes/llm_routing_admin.py")
    fn_start = src.find("async def _cost_stats_per_process(")
    fn_body = src[fn_start : fn_start + 2500]
    assert "try:" in fn_body
    assert "except Exception" in fn_body
    assert "return {}" in fn_body


def test_cost_stats_24h_and_7d_windows():
    """Dashboard table shows both 24h (today's spend) and 7d (rolling).
    Pin both window filters."""
    src = _read("app/routes/llm_routing_admin.py")
    fn_start = src.find("async def _cost_stats_per_process(")
    fn_body = src[fn_start : fn_start + 2500]
    assert "INTERVAL '24 hours'" in fn_body
    assert "INTERVAL '7 days'" in fn_body


def test_cost_stats_computes_rejection_pct():
    """rejection_pct is the column Rishi shares with Anshuman. Pin the
    math + guard against division by zero on a fresh process."""
    src = _read("app/routes/llm_routing_admin.py")
    fn_start = src.find("async def _cost_stats_per_process(")
    fn_body = src[fn_start : fn_start + 2500]
    assert "rejection_pct" in fn_body
    assert "if calls else 0.0" in fn_body or "if calls" in fn_body


def test_summary_stats_splits_real_vs_synthetic():
    """Top-of-page summary shows real $ separately from synthetic
    compute share. Pin both filters."""
    src = _read("app/routes/llm_routing_admin.py")
    fn_start = src.find("async def _summary_stats(")
    fn_body = src[fn_start : fn_start + 2000]
    assert "cost_basis = 'real'" in fn_body
    assert "cost_basis = 'synthetic'" in fn_body


# ─── HTML render ──────────────────────────────────────────────────────────


def test_html_render_includes_per_process_form():
    """Each row has an inline form posting to the update endpoint —
    that's how Save works without JS."""
    src = _read("app/routes/llm_routing_admin.py")
    fn_start = src.find("def _render_html_page(")
    fn_body = src[fn_start : fn_start + 6000]
    assert "/admin/llm-routing/page/update/" in fn_body
    assert "/admin/llm-routing/page/delete/" in fn_body
    assert 'method="post"' in fn_body


def test_html_render_threads_token_through_form_actions():
    """Browser-bookmark flow uses ?token=<jwt> in URL. After Save, the
    redirect target must also carry the token so the page reloads
    authed. Pin the token threading."""
    src = _read("app/routes/llm_routing_admin.py")
    fn_start = src.find("def _render_html_page(")
    fn_body = src[fn_start : fn_start + 6000]
    assert "token_q" in fn_body
    assert "_urlquote(token)" in fn_body or "urlquote" in fn_body


def test_html_render_escapes_process_names():
    """Process names come from PROCESS_NAMES (static constants today),
    but XSS hygiene matters if a future override let user-controlled
    text leak into a process key."""
    src = _read("app/routes/llm_routing_admin.py")
    fn_start = src.find("def _render_html_page(")
    fn_body = src[fn_start : fn_start + 6000]
    assert "_html.escape" in fn_body


def test_html_render_color_codes_rejection_rate():
    """ADHD-friendly: rejection% rendered in green/amber/red so Rishi
    sees problems at a glance, not by reading numbers."""
    src = _read("app/routes/llm_routing_admin.py")
    fn_start = src.find("def _render_html_page(")
    fn_body = src[fn_start : fn_start + 6000]
    # Traffic-light palette (same as admin_dashboard._color_for_status)
    assert "#2e7d32" in fn_body  # green
    assert "#f57c00" in fn_body  # amber
    assert "#c62828" in fn_body  # red


# ─── form-POST handlers ──────────────────────────────────────────────────


def test_page_update_handler_exists_with_form_kwargs():
    """The form-POST handler that browser Save buttons hit. FastAPI Form
    extraction is what pulls fields out of the submitted body."""
    src = _read("app/routes/llm_routing_admin.py")
    assert "page_update_routing" in src
    assert "provider: str = Form(" in src
    assert "model: str = Form(" in src


def test_page_update_redirects_to_dashboard_303():
    """After Save: 303 redirect back to /admin/llm-routing so the
    browser does a GET and shows the freshly-updated row. The 303
    status (not 302) is the canonical PRG pattern."""
    src = _read("app/routes/llm_routing_admin.py")
    fn_start = src.find("async def page_update_routing(")
    fn_body = src[fn_start : fn_start + 3000]
    assert "RedirectResponse" in fn_body
    assert "status_code=303" in fn_body


def test_page_update_preserves_capability_check():
    """The HTML form path has the same audio_transcription capability
    guard as the JSON PATCH path. Otherwise the dashboard could let
    an operator break audio with one click."""
    src = _read("app/routes/llm_routing_admin.py")
    fn_start = src.find("async def page_update_routing(")
    fn_body = src[fn_start : fn_start + 3000]
    assert "supports_transcribe" in fn_body


def test_page_delete_handler_exists():
    src = _read("app/routes/llm_routing_admin.py")
    assert "page_delete_routing" in src


def test_page_delete_redirects_to_dashboard_303():
    src = _read("app/routes/llm_routing_admin.py")
    fn_start = src.find("async def page_delete_routing(")
    fn_body = src[fn_start : fn_start + 2000]
    assert "RedirectResponse" in fn_body
    assert "status_code=303" in fn_body
