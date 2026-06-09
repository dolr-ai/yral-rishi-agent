"""Phase 21αβ.H11 — source-pin that cost-alert wiring is intact.

These are static-shape tests, not behavior tests. The real behavior
verification is: deploy → wait a tick → confirm a forced threshold
breach produces a Sentry event. Source-pinning the wiring catches
the most likely class of regression (someone renames a symbol, drops
the kill_switch entry, removes the main.py task).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_cost_alerts_module_exists():
    """services.cost_alerts must define the two checks the loop wires to."""
    src = (REPO / "app" / "services" / "cost_alerts.py").read_text()
    assert "async def cost_alerts_loop" in src
    assert "async def _check_hourly_gemini_cost" in src
    assert "async def _check_async_error_spike" in src


def test_no_email_digest_section_per_rishi_2026_06_09():
    """Rishi 2026-06-09 — no daily email digest section. The two
    periodic Sentry alerts are sufficient. Defend that the digest
    integration stays OUT to avoid quietly reintroducing it."""
    src = (REPO / "app" / "services" / "cost_alerts.py").read_text()
    digest = (REPO / "app" / "services" / "email_digest.py").read_text()
    assert "section_llm_costs_yesterday" not in src
    assert "section_llm_costs_yesterday" not in digest


def test_main_wires_cost_alerts_task():
    """app.main must create + cancel + await the cost_alerts task."""
    main = (REPO / "app" / "main.py").read_text()
    assert "from services.cost_alerts import cost_alerts_loop" in main
    assert "cost_alerts_task = asyncio.create_task(cost_alerts_loop())" in main
    assert "cost_alerts_task.cancel()" in main
    assert "await cost_alerts_task" in main


def test_kill_switch_has_cost_alerts_entry():
    """The kill-switch registry must include cost_alerts so ops can
    mute it without a redeploy. Env name follows the ENABLE_* convention."""
    ks = (REPO / "app" / "kill_switch.py").read_text()
    assert '"cost_alerts": "ENABLE_COST_ALERTS"' in ks


def test_thresholds_env_overridable():
    """Operators must be able to retune thresholds without a code change.
    Env knobs must be honored by name (not buried as literals)."""
    src = (REPO / "app" / "services" / "cost_alerts.py").read_text()
    assert "COST_ALERT_HOURLY_GEMINI_USD" in src
    assert "COST_ALERT_ASYNC_ERROR_COUNT" in src
    assert "COST_ALERT_TICK_SEC" in src
    assert 'os.environ.get("COST_ALERT_HOURLY_GEMINI_USD"' in src
    assert 'os.environ.get("COST_ALERT_ASYNC_ERROR_COUNT"' in src


def test_default_hourly_gemini_threshold_matches_rishi_choice():
    """Rishi 2026-06-09 — default $10/hr. Pin the literal so a future
    refactor doesn't quietly drift back to the original $1/hr."""
    src = (REPO / "app" / "services" / "cost_alerts.py").read_text()
    assert 'os.environ.get("COST_ALERT_HOURLY_GEMINI_USD", "10.0")' in src


def test_nx_dedup_on_both_alerts():
    """Both alerts must dedupe via Redis SET NX. Without dedup, the
    5-min loop would refire the same alert 12 times an hour during a
    sustained threshold breach — Sentry noise + on-call fatigue."""
    src = (REPO / "app" / "services" / "cost_alerts.py").read_text()
    # The helper that does NX is the only SET NX site.
    assert "_try_set_nx" in src
    assert "nx=True" in src
    # Both checks must call it.
    assert src.count("await _try_set_nx(") == 2


def test_fail_open_semantics_documented():
    """If Redis or Sentry is down, alerting must degrade silently —
    never block the loop or raise into the request path."""
    src = (REPO / "app" / "services" / "cost_alerts.py").read_text()
    assert "Fail-open" in src or "fail-open" in src or "best-effort" in src
