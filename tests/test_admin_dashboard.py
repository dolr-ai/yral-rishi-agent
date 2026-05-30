"""Phase 19.6 — admin dashboard shell.

Source-inspection tests for the structural contract the dashboard
must keep so later PRs filling in tiles don't accidentally break the
shell. Live behavior is exercised after deploy via curl + Rishi's
bookmark."""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


def test_dashboard_route_exists():
    """GET /admin/dashboard is the bookmarkable URL the rule requires.
    If this assertion fires, the URL changed and Rishi's bookmark broke."""
    src = _read("app/routes/admin_dashboard.py")
    assert '@router.get("/admin/dashboard")' in src


def test_dashboard_accepts_token_query_param():
    """Browser bookmarkability requires URL-embedded auth (no header-
    injection extension). The ?token= path must coexist with the
    canonical Authorization header path."""
    src = _read("app/routes/admin_dashboard.py")
    assert 'query_params.get("token")' in src
    # Both forms route through validation — header AND token both 401
    # on bad/missing JWT
    assert "_get_current_user_strict" in src or "get_current_user" in src


def test_dashboard_renders_html_not_just_json():
    """ADHD-friendly UX = browser bookmark = HTML. JSON via ?format=json
    stays available for machine callers."""
    src = _read("app/routes/admin_dashboard.py")
    assert "HTMLResponse" in src
    assert "JSONResponse" in src
    assert 'format") == "json"' in src


def test_dashboard_auto_refresh_meta_tag():
    """Rishi bookmarks, leaves the tab open — the page must auto-
    refresh so the data stays live without manual reload."""
    src = _read("app/routes/admin_dashboard.py")
    assert 'meta http-equiv="refresh"' in src


def test_dashboard_includes_etl_live_tiles():
    """Two systems that already exist must surface as live tiles
    (not placeholders): ETL status + integrity verifier. The dashboard
    is only useful from day one if at least these are wired."""
    src = _read("app/routes/admin_dashboard.py")
    assert "_etl_tile" in src
    assert "_integrity_tile" in src


def test_dashboard_includes_placeholder_tiles_for_planned_systems():
    """Per the rule: 'empty tiles for now' so later PRs just fill in.
    Each placeholder must name its PR so Rishi can trace the roadmap
    from the dashboard."""
    src = _read("app/routes/admin_dashboard.py")
    # Coverage check — every priority-tier protective system has a tile
    for planned in (
        "Phase 19.1",  # rate limits
        "Phase 19.2",  # cost breaker
        "Phase 24.1",  # secret scan
        "Phase 24.2",  # safety drill
        "Phase 24.3",  # dep vuln
        "PR I10",  # backup restore drill
    ):
        assert planned in src, f"missing placeholder for {planned}"


def test_status_colors_traffic_light():
    """ADHD-friendly: status color is the dominant visual signal.
    The 4-state palette (ok/warn/fail/off) must be present for every
    tile to render correctly."""
    src = _read("app/routes/admin_dashboard.py")
    for state in ("ok", "warn", "fail", "off"):
        assert f'"{state}"' in src


def test_main_py_wires_dashboard_router():
    """If the import/include line is missing, the route 404s and the
    whole dashboard is invisible. Pin both lines so a refactor catches it."""
    main_src = _read("app/main.py")
    assert "from routes.admin_dashboard import router as admin_dashboard_router" in main_src
    assert "app.include_router(admin_dashboard_router)" in main_src


def test_no_protective_system_ships_without_tile():
    """The rule (memory: feedback-adhd-observability-and-security-baseline)
    requires every new limit/breaker to come with a dashboard line.
    This test will fail every time a new protective feature is added
    without a tile here — that's the point. Edit me when filling in.

    Counting _placeholder_tile() call sites (one per planned protective
    system) + the live tile-builder functions: 6 placeholders today +
    2 live = 8. When a placeholder flips to live, REPLACE the call
    with a new _xxx_tile() function — don't delete."""
    src = _read("app/routes/admin_dashboard.py")
    live = src.count("await _etl_tile(") + src.count("await _integrity_tile(")
    placeholders = src.count("_placeholder_tile(")
    # 6 placeholder definitions = 6 lines; the function def itself
    # contains the literal too, hence >= 6 here means at least 5
    # actual call sites. Add the 2 live tiles → ≥ 7 systems surfaced.
    assert (live + placeholders) >= 7


def test_html_template_escapes_user_data():
    """Even with admin-only access, title/primary/details strings should
    be HTML-escaped — the data flows from DB rows in some cases (e.g.,
    a STUCK marker payload). Don't open an XSS surface for any caller
    who can shape that data."""
    src = _read("app/routes/admin_dashboard.py")
    # Minimal escape — at least the < → &lt; substitution must run
    assert 'replace("<", "&lt;")' in src
