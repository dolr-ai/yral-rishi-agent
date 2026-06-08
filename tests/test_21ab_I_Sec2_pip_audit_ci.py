"""Phase 21αβ.I-Sec2 — source-pin that pip-audit is wired into CI.

Source-pin only; doesn't run pip-audit. The workflow itself runs it
on every PR/push; CI green proves the integration works end-to-end.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_pip_audit_job_exists_in_security_workflow():
    """`pip-audit` job must exist in security.yml on PR + push to main."""
    wf = (REPO / ".github" / "workflows" / "security.yml").read_text()
    assert "pip-audit:" in wf
    assert "pip install pip-audit" in wf
    assert "pull_request:" in wf
    assert "push:" in wf


def test_pip_audit_uses_strict_mode():
    """Strict mode fails CI on any finding not in the ignore-list.
    Without --strict, pip-audit exits 0 even with findings, defeating
    the purpose of the gate."""
    wf = (REPO / ".github" / "workflows" / "security.yml").read_text()
    pos = wf.find("pip-audit:")
    body = wf[pos:]
    assert "--strict" in body


def test_pip_audit_ignore_file_baseline_present():
    """The 14 known-accepted vulns from DEV-10 baseline must be in
    pip-audit-ignore.txt. Without these, every CI run fails."""
    cfg = REPO / "pip-audit-ignore.txt"
    assert cfg.exists(), "pip-audit-ignore.txt missing"
    body = cfg.read_text()
    # All 14 baseline CVE/PYSEC IDs from DEV-10:
    expected = [
        # pyjwt
        "PYSEC-2026-120",
        "PYSEC-2025-183",
        "PYSEC-2026-179",
        "PYSEC-2026-175",
        "PYSEC-2026-177",
        "PYSEC-2026-178",
        "PYSEC-2026-176",
        # python-multipart
        "CVE-2026-24486",
        "CVE-2026-40347",
        "CVE-2026-42561",
        # starlette (PYSEC-2026-161 appears once; pip-audit dedupes per
        # ID even when listed twice in the audit report)
        "PYSEC-2026-161",
        "CVE-2025-54121",
        "CVE-2025-62727",
    ]
    missing = [cve for cve in expected if cve not in body]
    assert not missing, f"missing baseline CVEs in ignore-list: {missing}"


def test_ignore_list_has_per_cve_justification_comments():
    """Each new ignore entry must have a justification — enforced
    via the section-header structure. Sanity-check that the file
    has the 3 expected section-headers (one per package)."""
    body = (REPO / "pip-audit-ignore.txt").read_text()
    assert "pyjwt" in body
    assert "python-multipart" in body
    assert "starlette" in body
    # Bump-path comments document the recovery plan.
    assert "Bump path" in body
