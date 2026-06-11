"""Source-pin tests for the on-demand ETL drain workflow.

These tests don't run the workflow; they pin its shape so a future
refactor can't silently drop the typed-confirmation, ssh-keyscan, or
manager-failover guards. Mirror of test_21ab_I_Dep1_stable_tag.py /
test_migration_runner_refusal_distinguishable.py.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WF = REPO / ".github" / "workflows" / "etl-drain.yml"


def _body() -> str:
    return WF.read_text()


def test_workflow_file_exists():
    assert WF.exists(), "etl-drain.yml missing"


def test_workflow_is_manual_dispatch_only():
    """No `push:` / `workflow_run:` triggers — this is operator-initiated."""
    body = _body()
    # `on:` block is workflow_dispatch only
    assert "workflow_dispatch:" in body
    assert "push:" not in body
    assert "workflow_run:" not in body


def test_typed_confirmation_required():
    """Symmetric with bootstrap-schema-migrations.yml (`BOOTSTRAP`) /
    rotate-runpod-vllm-key.yml (`ROTATE KEY`). Refuses to start without
    the literal phrase typed."""
    body = _body()
    assert "i_understand" in body
    assert 'DRAIN ETL' in body
    # And the validation step exits if the phrase is wrong
    assert "Confirmation phrase mismatched" in body


def test_mode_input_validation():
    """`mode` must be one of routine/cutover. Validate both at the
    UI layer (choice type) and the script layer (belt + braces)."""
    body = _body()
    assert "type: choice" in body
    # The two valid modes are in the options list
    assert "- routine" in body
    assert "- cutover" in body
    # Script-side validation as the second guard
    assert "mode must be 'routine' or 'cutover'" in body


def test_ssh_keyscan_all_four_hosts():
    """3 V2 swarm managers + rishi-1. ssh-keyscan picks up whichever
    host-key algorithms each offers (RSA / ED25519 / ECDSA) — same
    pattern bootstrap-schema-migrations.yml uses since 2026-06-09."""
    body = _body()
    assert "ssh-keyscan" in body
    # All 4 env vars referenced in the keyscan loop
    for host_var in ("SWARM_MANAGER_1", "SWARM_MANAGER_2", "SWARM_MANAGER_3", "RISHI_1_HOST"):
        assert host_var in body


def test_manager_failover_loop_for_drain_call():
    """The drain step picks the first responding swarm manager — if
    one is down or not a manager, we move to the next. Same pattern
    used by rotate-runpod-vllm-key.yml's Pick step."""
    body = _body()
    pos = body.find("Pick a responding swarm manager")
    assert pos != -1
    block = body[pos : pos + 2000]
    assert "for host in" in block
    assert "LocalNodeState" in block
    assert "active" in block


def test_drain_call_uses_jwt_from_secret():
    """Admin endpoints are JWT-gated. Workflow reads from
    `secrets.ADMIN_JWT_TOKEN`. Length-only confirmation in the
    verify step — never echo the value into a log."""
    body = _body()
    assert "secrets.ADMIN_JWT_TOKEN" in body
    # The value goes through SendEnv, not command-line interpolation
    assert "SendEnv=ADMIN_JWT_TOKEN" in body
    # The verify step exists with length-only logging
    assert "length=${#TOK}" in body


def test_drain_call_targets_correct_endpoint():
    """POST /admin/etl/drain (matches health.py route)."""
    body = _body()
    assert "/admin/etl/drain" in body
    assert "POST" in body


def test_cutover_mode_uploads_to_s3():
    """Per plan §9 Rishi pre-decision (yes for cutover, no for routine).
    The upload step must be gated on `inputs.mode == 'cutover'`."""
    body = _body()
    pos = body.find("Cutover-mode artifacts")
    assert pos != -1
    block = body[pos : pos + 2000]
    assert "inputs.mode == 'cutover'" in body
    # S3 destination matches the plan: s3://rishi-yral/cutover-runs/<ts>.json
    assert "cutover-runs" in body
    assert "rishi-yral" in body


def test_cutover_mode_posts_google_chat():
    """Per plan §9 Rishi pre-decision (yes for cutover, no for routine)."""
    body = _body()
    assert "GOOGLE_CHAT_WEBHOOK_URL" in body
    # And the post happens inside the cutover-only branch (same step)
    pos = body.find("Cutover-mode artifacts")
    block = body[pos : pos + 3000]
    assert "GOOGLE_CHAT_WEBHOOK_URL" in block


def test_exit_code_reflects_verdict():
    """GREEN → exit 0; anything else → exit 1 so the workflow turns red."""
    body = _body()
    pos = body.find("Exit code reflects verdict")
    assert pos != -1
    block = body[pos : pos + 1000]
    assert 'VERDICT" = "GREEN"' in block
    assert "exit 0" in block
    assert "exit 1" in block


def test_outer_timeout_set():
    """Hard cap so a stuck SSH or curl doesn't pin a runner. The
    drain endpoint has its own 180s deadline; this is the wall budget
    across all steps."""
    body = _body()
    assert "timeout-minutes:" in body


def test_workflow_runs_on_production_environment():
    """Same `environment: production` gate as other manual workflows
    (bootstrap-schema-migrations.yml, rotate-runpod-vllm-key.yml).
    Means repo Settings → Environments → production can require
    review-before-run if Rishi wants."""
    body = _body()
    assert "environment: production" in body
