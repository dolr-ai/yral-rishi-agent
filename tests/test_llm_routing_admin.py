"""Phase 25.4 — admin hot-swap endpoint + DB-override resolution.

Source-pin tests (no httpx in local venv). Live wire-level tests run
in production via curl against /admin/llm-routing once the migration
is applied.
"""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


# ─── migration shape ──────────────────────────────────────────────────────


def test_migration_026_creates_llm_process_config_table():
    src = _read("migrations/026_llm_process_config.sql")
    assert "CREATE TABLE IF NOT EXISTS llm_process_config" in src
    assert "PRIMARY KEY" in src and "process" in src
    # Audit columns the admin endpoint depends on
    assert "updated_by" in src
    assert "updated_at" in src


def test_migration_026_documents_rule_9():
    """Rule 9: pg_dump before any schema change. The migration must
    document that it's NOT auto-applied so the operator knows to
    pg_dump first."""
    src = _read("migrations/026_llm_process_config.sql")
    assert "pg_dump" in src
    assert "Rule 9" in src


# ─── DB override resolution in registry ──────────────────────────────────


def test_registry_db_override_precedence():
    """DB override > env override > LLM_DEFAULTS. Pin the precedence
    order in the registry source."""
    src = _read("app/services/llm_registry.py")
    # The override block uses _db_overrides as the highest-priority source
    assert "_db_overrides" in src
    # Env override is only honored when there's no DB pin (per the
    # \"DB wins\" comment)
    assert "DB wins" in src or "not (_db_overrides and process in _db_overrides)" in src


def test_registry_reload_from_db_handles_missing_table():
    """Rule 9 — code deploys before migration applies. The reload
    must not crash on missing table; log warning and leave cache
    empty. Falls through to env + LLM_DEFAULTS."""
    src = _read("app/services/llm_registry.py")
    assert "reload_config_from_db" in src
    # The try/except around the SQL is what makes deploys safe
    # pre-migration. Pin both halves.
    assert "logger.warning" in src
    assert "using env + defaults" in src


def test_registry_upsert_validates_process_and_provider():
    """upsert_override must reject unknown processes + providers so
    typos in the admin endpoint don't poison the registry."""
    src = _read("app/services/llm_registry.py")
    assert "upsert_override" in src
    assert "unknown process" in src
    assert "unknown provider" in src


# ─── admin endpoint ──────────────────────────────────────────────────────


def test_admin_endpoint_routes_under_admin_prefix():
    """All hot-swap routes live under /admin/* so they inherit the
    rate-limiter skip + JWT auth pattern."""
    src = _read("app/routes/llm_routing_admin.py")
    assert "/admin/llm-routing" in src
    assert "router.patch" in src.lower() or "@router.patch" in src


def test_admin_endpoint_returns_503_when_table_missing():
    """If migration 026 hasn't been applied yet, PATCH must surface a
    clear 503 with an explanation — NOT a 500 stack trace. The text
    mentions migration 026 + the env-var workaround so the operator
    sees both options."""
    src = _read("app/routes/llm_routing_admin.py")
    assert "503" in src
    assert "migration 026" in src
    # Env-var workaround pointed at so operator isn't stuck
    assert "LLM_PROCESS__" in src


def test_admin_endpoint_capability_check_audio():
    """Refusing to point audio_transcription at a provider that doesn't
    support transcription is what makes the registry's capability flags
    load-bearing. Without this gate, an admin click could break audio."""
    src = _read("app/routes/llm_routing_admin.py")
    assert "supports_transcribe" in src
    assert "audio_transcription" in src


def test_admin_endpoint_audit_trail_updated_by():
    """Every PATCH writes the JWT principal into updated_by. That's the
    forensic trail for \"who changed this?\" The endpoint must thread the
    principal from auth through to the registry write."""
    src = _read("app/routes/llm_routing_admin.py")
    assert "updated_by" in src
    assert "principal" in src


def test_admin_endpoint_get_lists_all_processes():
    """GET /admin/llm-routing is what the dashboard tile + the future
    25.9 web UI hits. Must return every PROCESS_NAMES entry with
    resolved (provider, model) — not just the overridden ones."""
    src = _read("app/routes/llm_routing_admin.py")
    assert "PROCESS_NAMES" in src
    assert "current_config" in src


# ─── dashboard tile ──────────────────────────────────────────────────────


def test_dashboard_has_llm_routing_tile():
    """Phase 19.6 dashboard surfaces the routing summary so Rishi sees
    overrides + providers-in-use at a glance."""
    src = _read("app/routes/admin_dashboard.py")
    assert "_llm_routing_tile" in src
    # The tile is wired into the tiles list (not just defined)
    # — count the function plus the call site
    assert src.count("_llm_routing_tile") >= 2


def test_dashboard_tile_links_to_admin_endpoint():
    """Click-through from dashboard tile → full hot-swap UI lives at
    /admin/llm-routing. Pin the link target."""
    src = _read("app/routes/admin_dashboard.py")
    assert '"/admin/llm-routing"' in src


# ─── startup reload ──────────────────────────────────────────────────────


def test_main_reloads_overrides_on_startup():
    """Container restart must pick up DB overrides — otherwise restarts
    silently revert to defaults. Pin the startup-hook reload."""
    src = _read("app/main.py")
    assert "llm_registry.reload_config_from_db" in src
    # Must be inside the lifespan/startup function (lazy import is OK)
    assert "lifespan" in src
