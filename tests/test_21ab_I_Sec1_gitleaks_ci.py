"""Phase 21αβ.I-Sec1 — source-pin that gitleaks is wired into CI.

Source-pin only; doesn't run gitleaks. The workflow itself runs gitleaks
on every PR/push; CI green proves the integration works end-to-end.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_security_workflow_exists():
    """A `gitleaks` job must exist in a workflow that runs on PR.
    Uses the CLI directly (free) instead of gitleaks/gitleaks-action@v2
    which requires a paid license for organizations."""
    wf = REPO / ".github" / "workflows" / "security.yml"
    assert wf.exists(), "security.yml workflow missing"
    body = wf.read_text()
    assert "gitleaks detect" in body
    assert "pull_request:" in body


def test_gitleaks_uses_full_history():
    """`fetch-depth: 0` so the secret scan covers full git history,
    matching the DEV-7 baseline (`--log-opts="--all"`). Default
    actions/checkout fetch-depth=1 would miss past commits."""
    wf = (REPO / ".github" / "workflows" / "security.yml").read_text()
    assert "fetch-depth: 0" in wf


def test_gitleaks_allowlist_baseline_present():
    """`.gitleaks.toml` must contain the 4 known false-positive
    matches from DEV-7. Without these, every CI run would fail with
    findings we've already triaged."""
    cfg = REPO / ".gitleaks.toml"
    assert cfg.exists(), ".gitleaks.toml missing"
    body = cfg.read_text()
    # Each of the 4 baseline commit SHAs must be in the allowlist.
    for sha in (
        "20ebe2577f13939160fdaf3cff98f7de57b03204",
        "9801855322667584ea502b8b46f341490728aa32",
        "7ea7e503dde283088052606b1c6b10c8c909bf12",
        "372600dbb19c43c239677c8b5066f53ecae60145",
    ):
        assert sha in body, f"missing baseline commit SHA in allowlist: {sha}"
    # Archived orchestrator path-prefix allow.
    assert "yral-rishi-agent-conversation-turn-orchestrator/tests" in body
    # Default ruleset extension.
    assert "useDefault = true" in body


def test_security_workflow_runs_on_pr_AND_push_to_main():
    """Both triggers required: PR fires the gate, push catches direct
    main commits (defense-in-depth if branch protection ever slips)."""
    wf = (REPO / ".github" / "workflows" / "security.yml").read_text()
    assert "pull_request:" in wf
    assert "push:" in wf
    # Both targeted at main only — no per-branch noise.
    assert "branches: [main]" in wf
