"""Phase 21αβ.H10 — /admin/backup-health dashboard + audit-row plumbing.

Source-pin tests for the three-layer data source pattern:
  1. pg_stat_archiver  → WAL archive activity
  2. pg_class          → per-table page-count snapshot
  3. backup_drill_runs → restore-drill audit table (migration 036)

Plus source-pin tests for verdict computation (GREEN / WARN / RED) and
the drill-script audit row START/FINISH writes.

No httpx + no fastapi in local venv — wire-level checks happen in prod
via curl. Pattern matches test_llm_routing_admin.py.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


# ─── migration 036 shape ─────────────────────────────────────────────────


def test_migration_036_creates_backup_drill_runs_table():
    src = _read("migrations/036_backup_drill_runs.sql")
    assert "CREATE TABLE IF NOT EXISTS backup_drill_runs" in src
    for col in (
        "id UUID PRIMARY KEY",
        "drill_type",
        "started_at",
        "finished_at",
        "exit_code",
        "triggered_by",
        "sanity_results",
        "notes",
    ):
        assert col in src, f"migration 036 missing column: {col}"


def test_migration_036_constrains_drill_type():
    """CHECK constraint pins drill_type so a typo can't poison the
    audit table. walg_restore is shipping today; pg_dump_restore is
    pre-wired for the manual-dump tier."""
    src = _read("migrations/036_backup_drill_runs.sql")
    assert "CHECK (drill_type IN ('walg_restore', 'pg_dump_restore'))" in src


def test_migration_036_has_recent_partial_index():
    """Tile query reads MAX(started_at) per drill_type. Partial index
    over (drill_type, started_at DESC) keeps it O(log recent)."""
    src = _read("migrations/036_backup_drill_runs.sql")
    assert "idx_backup_drill_runs_recent" in src
    assert "drill_type, started_at DESC" in src


def test_migration_036_has_squawk_preamble():
    """Per #340 I-Mig2 rule: every migration declares its own
    lock_timeout / statement_timeout so prod ops can read worst-case
    blocking duration from the file alone."""
    src = _read("migrations/036_backup_drill_runs.sql")
    assert "SET lock_timeout = '3s';" in src
    assert "SET statement_timeout = '60s';" in src


# ─── route registration + auth ──────────────────────────────────────────


def test_main_wires_backup_health_admin_router():
    """Without this, the dashboard 404s. Pin both halves: import +
    include_router call."""
    src = _read("app/main.py")
    assert (
        "from routes.backup_health_admin import router as backup_health_admin_router"
        in src
    )
    assert "app.include_router(backup_health_admin_router)" in src


def test_backup_health_routes_under_admin_prefix():
    """Both HTML + JSON routes live under /admin/* so they inherit the
    rate-limiter skip + JWT auth pattern."""
    src = _read("app/routes/backup_health_admin.py")
    assert '@router.get("/admin/backup-health")' in src
    assert '@router.get("/admin/backup-health.json")' in src


def test_backup_health_uses_check_admin_auth():
    """Bearer header OR ?token=… — same pattern as /admin/llm-routing.
    Bookmark-friendly via ?token= so Rishi can pin it in his browser."""
    src = _read("app/routes/backup_health_admin.py")
    assert "_check_admin_auth" in src
    assert "Authorization" in src
    assert 'request.query_params.get("token")' in src


def test_backup_health_calls_auth_on_both_routes():
    """Pin that both handlers gate-check before touching the DB."""
    src = _read("app/routes/backup_health_admin.py")
    assert src.count("_check_admin_auth(request)") >= 2


# ─── data source pinning ────────────────────────────────────────────────


def test_backup_health_reads_pg_stat_archiver():
    """Layer 1 of the 3-layer data source pattern. pg_stat_archiver is
    the cheapest, most direct WAL-G health signal."""
    src = _read("app/routes/backup_health_admin.py")
    assert "pg_stat_archiver" in src
    for col in (
        "archived_count",
        "failed_count",
        "last_archived_wal",
        "last_archived_time",
        "last_failed_wal",
        "last_failed_time",
    ):
        assert col in src, f"WAL archive query missing column: {col}"


def test_backup_health_reads_pg_class_for_floor_tables():
    """Layer 2 — per-table page-count snapshot proves rows exist (not
    just the schema). The floor-table list is the V2 mobile-contract
    surface; if any of these are empty we have a data-loss bug."""
    src = _read("app/routes/backup_health_admin.py")
    assert "pg_class" in src
    assert "_FLOOR_TABLES" in src
    for t in (
        "ai_influencers",
        "conversations",
        "messages",
        "system_instructions_history",
        "llm_costs",
        "coach_messages",
    ):
        assert f'"{t}"' in src, f"_FLOOR_TABLES missing: {t}"


def test_backup_health_reads_backup_drill_runs():
    """Layer 3 — audit-table query for last drill result."""
    src = _read("app/routes/backup_health_admin.py")
    assert "FROM backup_drill_runs" in src
    assert "ORDER BY started_at DESC" in src


def test_backup_health_drill_query_degrades_gracefully():
    """Rule-9 pattern: code may deploy before migration 036 applies.
    The drill query must not 500 the page — wrap in try/except, log
    warning, return empty list."""
    src = _read("app/routes/backup_health_admin.py")
    assert "_latest_drill_runs" in src
    assert "try:" in src
    assert "logger.warning" in src
    assert "migration 036" in src


# ─── verdict thresholds (source-pin) ────────────────────────────────────


def test_verdict_red_threshold_at_60_min_wal_staleness():
    """Hard fail: WAL archive over 60 min stale → RED. This is the
    single most important alert — losing WAL archiving means PITR
    window is shrinking minute by minute."""
    src = _read("app/routes/backup_health_admin.py")
    assert "60 * 60" in src  # 1 hr threshold
    assert "RED" in src
    # Reason string mentions 60 min so an operator reading the dashboard
    # knows which threshold tripped
    assert ">60 min" in src or "> 60 min" in src


def test_verdict_warn_threshold_at_15_min_wal_staleness():
    """Soft warning: WAL archive 15-60 min stale → WARN, not RED.
    Useful for catching slow degradation before it becomes an outage."""
    src = _read("app/routes/backup_health_admin.py")
    assert "15 * 60" in src
    assert "WARN" in src
    assert ">15 min" in src or "> 15 min" in src


def test_verdict_red_on_drill_failure():
    """Drill exit_code != 0 = catastrophic. Even if WAL archiving is
    fine, a failed restore proves the backups are unrestoreable. The
    verdict logic must escalate to RED, not just WARN."""
    src = _read("app/routes/backup_health_admin.py")
    # Both halves of the failed-drill branch: detect exit_code != 0,
    # set verdict RED, and emit a reason that mentions FAIL.
    assert "exit_code" in src
    assert 'verdict = "RED"' in src
    assert "FAILED" in src or "failed" in src


def test_verdict_warn_when_no_drills_recorded():
    """Cold start: backup_drill_runs is empty (or migration 036 not
    applied yet). Don't claim GREEN until we've proved restore works
    at least once — start at WARN with an explanatory reason."""
    src = _read("app/routes/backup_health_admin.py")
    assert "no drill rows recorded yet" in src


def test_verdict_warn_when_drill_in_progress():
    """finished_at IS NULL → drill started but hasn't called back.
    Either hung mid-restore (bad) or actively running (fine). WARN so
    the operator looks — don't auto-page."""
    src = _read("app/routes/backup_health_admin.py")
    assert "in progress" in src
    assert 'finished_at"] is None' in src or '"finished_at"] is None' in src


def test_verdict_warn_when_passing_drill_is_old():
    """A drill that passed 8+ days ago is suspicious — the cron drill
    runs weekly. Mark WARN so the operator catches a broken schedule."""
    src = _read("app/routes/backup_health_admin.py")
    # 8 days in seconds
    assert "8 * 24 * 60 * 60" in src


# ─── drill script audit-row writes ──────────────────────────────────────


def test_drill_script_writes_audit_start_row():
    """Drill script must INSERT a START row before doing any work,
    capturing the resulting UUID so the FINISH UPDATE can target it."""
    src = _read("scripts/walg_restore_drill.sh")
    assert "INSERT INTO backup_drill_runs" in src
    assert "RETURNING id" in src
    assert "DRILL_RUN_ID" in src


def test_drill_script_finish_helper_updates_same_row():
    """finish_audit_row() must UPDATE the row keyed by DRILL_RUN_ID,
    not INSERT a second row — otherwise the dashboard sees two rows per
    drill and the verdict logic breaks."""
    src = _read("scripts/walg_restore_drill.sh")
    assert "finish_audit_row" in src
    assert "UPDATE backup_drill_runs" in src
    assert "WHERE id = '${DRILL_RUN_ID}'::uuid" in src


def test_drill_script_finalises_on_exit_trap():
    """If the drill aborts mid-restore (set -e), the EXIT trap must
    still write FINISH with the real exit code. Otherwise a hung drill
    leaves finished_at=NULL forever."""
    src = _read("scripts/walg_restore_drill.sh")
    assert "_rc=$?" in src
    assert 'finish_audit_row "$_rc" "trap-finalised"' in src


def test_drill_script_explicit_success_finalisation():
    """Happy-path finalisation runs BEFORE the trap fires — pins
    finished_at + exit_code=0 + empty notes."""
    src = _read("scripts/walg_restore_drill.sh")
    assert 'finish_audit_row 0 ""' in src


def test_drill_script_triggered_by_default_manual():
    """TRIGGERED_BY env-var lets the cron + GH workflow tag their runs
    distinctly. Default 'manual' so a CLI invocation is labelled."""
    src = _read("scripts/walg_restore_drill.sh")
    assert 'TRIGGERED_BY="${TRIGGERED_BY:-manual}"' in src


def test_drill_script_audit_row_writes_sanity_jsonb():
    """The FINISH UPDATE writes per-table counts as JSONB so the
    dashboard renders 'AI_COUNT=12, MSG_COUNT=30000' inline."""
    src = _read("scripts/walg_restore_drill.sh")
    assert "jsonb_build_object" in src
    for k in ("AI_COUNT", "CONV_COUNT", "MSG_COUNT"):
        assert k in src


# ─── cross-navigation ───────────────────────────────────────────────────


def test_dashboard_cross_links_to_llm_routing():
    """Sibling-page navigation: bookmarking one means you can hop to
    the other in one click without re-typing ?token=."""
    src = _read("app/routes/backup_health_admin.py")
    assert "/admin/llm-routing" in src
    assert "LLM routing" in src
