"""Phase 21αβ.H8 / 24.2 — weekly security drill workflow.

Source-pin tests pinning the workflow's shape so a future refactor
that drops a scanner or changes the cadence forces this test to
update. The workflow itself runs in GitHub Actions; this just pins
its declared shape.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "weekly-security-drill.yml"


def test_workflow_exists():
    assert WORKFLOW.exists(), "weekly-security-drill.yml missing"


# ─── cadence ─────────────────────────────────────────────────────────────


def test_workflow_runs_weekly_on_sunday():
    """Sundays 04:00 UTC — off-peak for V2 alpha-soak + far from the
    daily 08:00 IST email digest. Pin both halves: cron string + the
    explanatory comment so a future refactor that shifts the cadence
    sees the rationale."""
    src = WORKFLOW.read_text()
    assert "cron: '0 4 * * 0'" in src
    # The rationale lives in a comment so it survives moves
    assert "Sundays" in src or "Sunday" in src


def test_workflow_supports_ad_hoc_trigger():
    """`workflow_dispatch` lets an operator run the drill on-demand
    (e.g. when a public CVE drops + we don't want to wait for the next
    Sunday). Pin the input shape so a refactor can't drop it."""
    src = WORKFLOW.read_text()
    assert "workflow_dispatch:" in src
    assert "reason:" in src


# ─── 3 scanners covered ─────────────────────────────────────────────────


def test_runs_gitleaks_all_history():
    """Same all-history scan as per-PR security.yml — that's the whole
    point of a periodic drill."""
    src = WORKFLOW.read_text()
    assert "gitleaks detect" in src
    assert '--log-opts="--all"' in src


def test_runs_pip_audit_without_ignore_list():
    """The drill INTENTIONALLY does NOT honor pip-audit-ignore.txt —
    surfaces every finding including the DEV-10 baseline so we
    quarterly re-check whether 'accepted for now' is still accurate.
    Pin the absence of --ignore-vuln in this workflow."""
    src = WORKFLOW.read_text()
    # pip-audit is invoked
    pos = src.find("pip-audit \\")
    assert pos != -1
    # Find the next blank line / next step to bound the invocation
    end = src.find("\n      - ", pos)
    block = src[pos:end] if end != -1 else src[pos : pos + 1000]
    # Crucially: NO --ignore-vuln in this drill (different from
    # security.yml which DOES honor the allowlist)
    assert "--ignore-vuln" not in block


def test_runs_trivy_on_stable_image():
    """Trivy scans the same `:stable` image as per-PR. The drill
    surfaces ALL severities (not just HIGH+CRITICAL) so MEDIUM+LOW
    trend is visible for quarterly cleanup."""
    src = WORKFLOW.read_text()
    assert "aquasecurity/trivy-action" in src
    assert "ghcr.io/dolr-ai/yral-rishi-agent:stable" in src
    # All severities, not just HIGH+CRITICAL
    assert "UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL" in src


def test_trivy_drill_does_not_fail_workflow():
    """Drill is REPORT-ONLY — never fails the workflow. The per-PR
    workflow is the gate; this is the audit cadence."""
    src = WORKFLOW.read_text()
    trivy_pos = src.find("aquasecurity/trivy-action")
    block = src[trivy_pos : trivy_pos + 1500]
    # exit-code: '0' = always report, never fail
    assert "exit-code: '0'" in block


# ─── outputs + tracking issue ───────────────────────────────────────────


def test_uploads_all_three_reports_as_artifact():
    """JSON reports retained 90 days so quarterly triage can compare
    week-over-week trend."""
    src = WORKFLOW.read_text()
    for path in (
        "gitleaks-report.json",
        "pip-audit-report.json",
        "trivy-report.json",
    ):
        assert path in src, f"report path missing from artifact: {path}"
    assert "retention-days: 90" in src


def test_opens_tracking_issue_with_label():
    """Each weekly drill gets its OWN issue (vs reopening one) so the
    audit trail is discoverable + ops can close as triage completes.
    Pin both halves: gh issue create + the security label."""
    src = WORKFLOW.read_text()
    assert "gh issue create" in src
    assert "--label security,weekly-drill" in src
    # Title pattern includes the date so they're chronologically sortable
    assert 'TITLE="Weekly security drill' in src


def test_issue_body_summarizes_three_scanners():
    """The body has a table with all 3 scanners + their findings counts
    + their severity gates. Pin the structure."""
    src = WORKFLOW.read_text()
    # The body-builder block
    pos = src.find("Build issue body")
    body_block = src[pos : pos + 3000]
    assert "gitleaks" in body_block.lower()
    assert "pip-audit" in body_block.lower()
    assert "trivy" in body_block.lower()
    # Triage actions surfaced
    assert "Triage actions" in body_block


def test_issue_body_references_companion_runbook():
    """Cross-link to the rotation runbook (24.4) + per-PR security
    gates (24.1+24.3) so the operator opening the issue has the
    surrounding context one click away."""
    src = WORKFLOW.read_text()
    assert "docs/runbooks/secret-rotation.md" in src
    assert ".github/workflows/security.yml" in src
    # The 3 allowlist files
    assert ".gitleaks.toml" in src
    assert "pip-audit-ignore.txt" in src
    assert ".trivyignore" in src


# ─── permissions hygiene ────────────────────────────────────────────────


def test_workflow_requests_minimal_permissions():
    """Pin the explicit permissions block — without it GitHub grants
    the default token broad write permissions. Defense in depth."""
    src = WORKFLOW.read_text()
    pos = src.find("permissions:")
    block = src[pos : pos + 200]
    # contents: read for checkout, issues: write for the tracking
    # issue, packages: read for GHCR pull (Trivy image scan)
    assert "contents: read" in block
    assert "issues: write" in block
    assert "packages: read" in block
