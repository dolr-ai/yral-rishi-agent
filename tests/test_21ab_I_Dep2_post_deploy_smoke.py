"""Phase 21αβ.I-Dep2 — source-pin the post-deploy smoke wiring.

Behavior verification is the workflow run itself: every deploy
exercises the script. These tests defend the shape (triggers after
deploy, auto-rolls on failure, uses the right script).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WF = REPO / ".github" / "workflows" / "post-deploy-smoke.yml"
SCRIPT = REPO / "scripts" / "test_all_endpoints.py"


def test_workflow_exists():
    assert WF.exists(), ".github/workflows/post-deploy-smoke.yml missing"


def test_smoke_script_exists():
    """The workflow invokes scripts/test_all_endpoints.py — must exist."""
    assert SCRIPT.exists(), "scripts/test_all_endpoints.py missing"


def test_triggered_after_deploy_via_workflow_run():
    """Same indirection pattern Deploy uses to fire after CI: the smoke
    must fire after Deploy completes, not on push/PR. Otherwise the
    smoke would run BEFORE the deploy is live."""
    body = WF.read_text()
    assert "workflow_run:" in body
    assert 'workflows: ["Deploy to production"]' in body
    assert "branches: [main]" in body


def test_only_runs_on_successful_deploy():
    """If the triggering deploy failed, deploy.yml's own auto-rollback
    handled it. The smoke must skip that case (else we'd fire ANOTHER
    rollback on top of the in-flight one)."""
    body = WF.read_text()
    assert "github.event.workflow_run.conclusion == 'success'" in body


def test_invokes_full_endpoint_script_with_base_url():
    """The whole point of I-Dep2 is reusing the existing 24/24 endpoint
    script — not maintaining a parallel narrower smoke. Pin the call."""
    body = WF.read_text()
    assert "scripts/test_all_endpoints.py" in body
    assert "--base-url" in body
    assert "agent.rishi.yral.com" in body


def test_auto_rollback_on_smoke_failure():
    """Failing endpoint = service is in a half-broken state. Must
    trigger the same Rollback workflow deploy.yml's /health-failure
    branch uses. Mirror the exact step shape so the safety profile is
    identical."""
    body = WF.read_text()
    assert "Auto-rollback if smoke failed" in body
    assert "rollback.yml" in body
    assert "gh workflow run" in body
    # The trigger uses GH_TOKEN env (same as deploy.yml)
    assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in body


def test_workflow_dispatch_for_manual_re_run():
    """If the smoke fires a spurious failure (e.g. transient DNS),
    operators need to be able to re-run it manually. Without
    workflow_dispatch, the only way to re-trigger is to deploy again —
    too heavy."""
    body = WF.read_text()
    assert "workflow_dispatch:" in body


def test_settles_before_smoke():
    """Deploy's /health poll confirms the service is up, but rolling
    restarts may still be finishing on the trailing replica when
    /health first returned 200. Tiny settle delay avoids sporadic 5xx
    from the in-flight-restart replica."""
    body = WF.read_text()
    assert "sleep 10" in body


def test_smoke_script_supports_no_token_path():
    """Smoke runs WITHOUT a JWT — authenticated endpoints are out of
    scope (long-lived test JWTs in CI are a separate decision). The
    script must support the no-token path natively, exiting 1 only on
    public-endpoint failures."""
    body = SCRIPT.read_text()
    assert 'default=None, help="JWT Bearer' in body
    assert "if not args.token:" in body
    # The exit code 1 on failure is the CI gate.
    assert "sys.exit(1)" in body
