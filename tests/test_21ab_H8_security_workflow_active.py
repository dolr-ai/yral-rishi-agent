"""Phase 21αβ.H8 — pin all three security CI jobs stay wired.

Without this test, a future PR that rewrites `.github/workflows/security.yml`
could silently drop gitleaks, pip-audit, or Trivy and we wouldn't notice
until the next leak / vuln / CVE incident. This source-pins the job
names + their findings policies so a removal forces this test to update.

24.1 — gitleaks secret scan (DEV-7 baseline)
24.3 — pip-audit dep vuln scan (DEV-10 baseline) + Trivy container scan
       (this PR adds Trivy; pip-audit was already active)
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "security.yml"


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


def test_security_workflow_exists():
    assert WORKFLOW.exists(), ".github/workflows/security.yml missing"


# ─── 24.1 — gitleaks ────────────────────────────────────────────────────


def test_gitleaks_job_defined():
    """gitleaks must run on every push to main AND every PR. Pin both
    the job name and the triggers so a future PR can't silently scope
    it to push-only."""
    src = WORKFLOW.read_text()
    assert "gitleaks:" in src
    # Job has gitleaks-detect step
    assert "gitleaks detect" in src
    # And references the allowlist baseline (don't drop the FP suppress)
    assert ".gitleaks.toml" in src


def test_gitleaks_runs_on_both_push_and_pr():
    """The triggers section MUST include both push to main AND pull_request.
    Without push, gitleaks doesn't catch direct-to-main commits. Without
    pull_request, gitleaks doesn't catch leaks in PRs before merge."""
    src = WORKFLOW.read_text()
    # Find the `on:` block; check both triggers present
    pos = src.find("on:")
    end = src.find("permissions:")
    on_block = src[pos:end]
    assert "push:" in on_block
    assert "pull_request:" in on_block
    assert "branches: [main]" in on_block


def test_gitleaks_scans_all_history():
    """`--log-opts=\"--all\"` makes gitleaks sweep every commit (not just
    the PR diff). Critical for catching legacy leaks that survived a
    rebase. DEV-7 baseline relied on this."""
    src = WORKFLOW.read_text()
    assert '--log-opts="--all"' in src


# ─── 24.3 — pip-audit ─────────────────────────────────────────────────


def test_pip_audit_job_defined():
    """pip-audit MUST run in strict mode against requirements.txt."""
    src = WORKFLOW.read_text()
    assert "pip-audit:" in src
    assert "pip-audit" in src.lower()


def test_pip_audit_strict_mode():
    """`--strict` makes pip-audit fail CI on any finding not in the
    ignore-list. Without it, pip-audit only warns + we'd miss new CVEs."""
    src = WORKFLOW.read_text()
    assert "--strict" in src


def test_pip_audit_reads_ignore_list():
    """The DEV-10 (2026-06-05) baseline of 14 known vulns is in
    `pip-audit-ignore.txt`. Pin the reference."""
    src = WORKFLOW.read_text()
    assert "pip-audit-ignore.txt" in src


# ─── 24.3 — Trivy container scan (new in this PR) ──────────────────────


def test_trivy_job_defined():
    """Trivy MUST scan the published GHCR image — that's where OS-level
    CVEs (libpython, openssl, libc) land that pip-audit can't see."""
    src = WORKFLOW.read_text()
    assert "trivy:" in src
    assert "aquasecurity/trivy-action" in src


def test_trivy_scans_stable_ghcr_tag():
    """`:stable` is the tag that points at the latest deployed image
    (the one production is running). Scanning anything else gives us
    a number that doesn't match what's actually serving traffic."""
    src = WORKFLOW.read_text()
    assert "ghcr.io/dolr-ai/yral-rishi-agent:stable" in src


def test_trivy_fails_on_high_and_critical():
    """HIGH + CRITICAL severities fail CI; MEDIUM + LOW land in the
    artifact for review without blocking. Pin both halves so a future
    'too noisy' refactor can't silently drop CRITICAL gates."""
    src = WORKFLOW.read_text()
    trivy_pos = src.find("trivy:")
    trivy_block = src[trivy_pos : trivy_pos + 4000]
    # First scan is the fail-CI one with HIGH+CRITICAL
    assert "severity: 'HIGH,CRITICAL'" in trivy_block
    assert "exit-code: '1'" in trivy_block
    # Second scan is the report-only full-severity one
    assert "severity: 'UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL'" in trivy_block
    assert "exit-code: '0'" in trivy_block


def test_trivy_ignores_unfixed_cves():
    """`ignore-unfixed: true` — there's no actionable signal in a CVE
    with no upstream fix; we can't act on it. Filter those out so the
    report focuses on actionable findings."""
    src = WORKFLOW.read_text()
    trivy_pos = src.find("trivy:")
    trivy_block = src[trivy_pos : trivy_pos + 4000]
    assert "ignore-unfixed: true" in trivy_block


def test_trivy_reads_allowlist_file():
    """Trivy reads `.trivyignore` by convention — same shape as
    `pip-audit-ignore.txt`. Mirror the convention so all 3 security
    jobs use the same allowlist pattern (CVE-per-line + comments).
    Empty file at baseline is fine."""
    src = WORKFLOW.read_text()
    trivy_pos = src.find("trivy:")
    trivy_block = src[trivy_pos : trivy_pos + 4000]
    assert "trivyignores: '.trivyignore'" in trivy_block
    # The allowlist file MUST exist (even if empty)
    assert (REPO / ".trivyignore").exists()


def test_trivy_uploads_full_report_for_review():
    """The MEDIUM + LOW table report is the operator-facing surface;
    quarterly cleanup happens against this. Pin the upload step."""
    src = WORKFLOW.read_text()
    trivy_pos = src.find("trivy:")
    trivy_block = src[trivy_pos : trivy_pos + 4000]
    assert "trivy-report-full.txt" in trivy_block
    assert "trivy-report.json" in trivy_block


def test_trivy_runs_only_on_push_not_pr():
    """Trivy on every PR push = ~3 min extra for no signal (the image
    being scanned is the same one). Constrain to push events only."""
    src = WORKFLOW.read_text()
    trivy_pos = src.find("trivy:")
    trivy_block = src[trivy_pos : trivy_pos + 4000]
    assert "if: github.event_name == 'push'" in trivy_block


# ─── all 3 jobs present (regression guard) ─────────────────────────────


def test_all_three_security_jobs_present():
    """Belt-and-braces against a refactor that drops a job. If this
    test fails, the security baseline has regressed — investigate
    before merging."""
    src = WORKFLOW.read_text()
    assert "gitleaks:" in src
    assert "pip-audit:" in src
    assert "trivy:" in src
