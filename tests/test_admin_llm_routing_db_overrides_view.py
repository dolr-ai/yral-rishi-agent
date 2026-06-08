"""Source-level pins for the View-DB-overrides admin page.

Non-programmer Rishi needs an in-dashboard way to verify what's pinned at
the DB level vs what's a code default. This is the source-of-truth view
that complements the main routing dashboard.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(p: str) -> str:
    return (ROOT / p).read_text()


def test_endpoint_registered_under_llm_routing_path():
    """New page lives under /admin/llm-routing/db-overrides so it inherits
    the same JWT-gated path-prefix pattern as the existing dashboard."""
    src = _read("app/routes/llm_routing_admin.py")
    assert '/admin/llm-routing/db-overrides' in src
    assert "async def llm_routing_db_overrides(" in src


def test_endpoint_is_jwt_gated():
    """Same auth check as every other admin endpoint in this file —
    leaking the DB override list would not be catastrophic, but routing
    decisions are operationally sensitive. Match the existing pattern."""
    src = _read("app/routes/llm_routing_admin.py")
    # Find the new endpoint's body.
    start = src.find("async def llm_routing_db_overrides(")
    assert start > 0
    # The next 600 chars must contain the auth check.
    body = src[start : start + 600]
    assert "_check_admin_auth(request)" in body, (
        "View-DB-overrides endpoint must call _check_admin_auth like every "
        "other admin endpoint in this file"
    )


def test_read_helper_returns_empty_list_when_table_missing():
    """If migration 026 hasn't been applied (e.g. a fresh dev environment),
    the helper must NOT crash — return [] and let the page render the
    'no overrides' message."""
    src = _read("app/routes/llm_routing_admin.py")
    start = src.find("async def _read_raw_db_overrides(")
    assert start > 0
    end = src.find("\n\n\n", start)
    body = src[start:end]
    # The try/except wrapper is the safety net.
    assert "try:" in body
    assert "except Exception" in body
    assert "return []" in body


def test_render_handles_empty_rows_with_friendly_message():
    """When the DB has zero overrides (the cleanest state — everything on
    code defaults), the page must surface that as a clear positive signal,
    not show an empty table that looks like the page is broken."""
    src = _read("app/routes/llm_routing_admin.py")
    start = src.find("def _render_db_overrides_page(")
    assert start > 0
    end = src.find("\n\n\n", start)
    body = src[start:end]
    # Empty-state copy must be present.
    assert "No DB overrides" in body or "table is empty" in body
    assert "LLM_DEFAULTS" in body, (
        "empty-state must point at the code default location so the user "
        "knows where the routing decision comes from instead"
    )


def test_render_includes_critical_columns_when_rows_present():
    """The whole point of the page is to show what's in each DB row. If a
    column is missing the page is useless. Pin the 6 columns: process,
    provider, model, timeout, updated_at, updated_by."""
    src = _read("app/routes/llm_routing_admin.py")
    start = src.find("def _render_db_overrides_page(")
    end = src.find("\n\n\n", start)
    body = src[start:end]
    for col in ("Process", "Provider", "Model", "Timeout", "Updated at", "Updated by"):
        assert col in body, f"missing column header: {col}"


def test_dashboard_links_to_db_overrides_page():
    """Main routing dashboard must have a discoverable link to the new
    view. If the link is buried or missing, Rishi won't find it."""
    src = _read("app/routes/llm_routing_admin.py")
    # Look in the _render_html_page function (main dashboard renderer).
    start = src.find("def _render_html_page(")
    end = src.find("\n\n\n", start)
    body = src[start:end]
    assert "/admin/llm-routing/db-overrides" in body
    assert "View raw DB overrides" in body or "View DB overrides" in body


def test_db_overrides_page_has_back_link_to_dashboard():
    """One-click return to the main dashboard so the operator doesn't
    have to type the URL or hit Back-then-history."""
    src = _read("app/routes/llm_routing_admin.py")
    start = src.find("def _render_db_overrides_page(")
    end = src.find("\n\n\n", start)
    body = src[start:end]
    assert "Back to routing dashboard" in body
    assert 'href="/admin/llm-routing' in body
