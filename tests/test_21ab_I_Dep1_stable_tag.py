"""Phase 21αβ.I-Dep1 — source-pin that :stable tag step is wired.

Source-pin only. The live behavior is verified by the next deploy:
after merge + auto-deploy + /health passes, `crane tag … stable`
fires and ghcr.io/dolr-ai/yral-rishi-agent:stable points at the new SHA.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_deploy_workflow_has_stable_tag_step():
    """The step must exist with the right gate (health_check success)."""
    wf = (REPO / ".github" / "workflows" / "deploy.yml").read_text()
    assert "Tag image as :stable after successful deploy" in wf
    # Must be gated on /health passing — never tag a failed deploy.
    pos = wf.find("Tag image as :stable")
    body = wf[pos : pos + 1500]
    assert "if: steps.health_check.outcome == 'success'" in body
    # crane is the chosen mechanism (avoids docker pull+tag+push)
    assert "crane tag" in body
    assert "stable" in body


def test_stable_tag_uses_pinned_crane_version():
    """Pin a specific crane version. `latest` would silently roll us
    forward and is forbidden everywhere else in deploy."""
    wf = (REPO / ".github" / "workflows" / "deploy.yml").read_text()
    pos = wf.find("Tag image as :stable")
    body = wf[pos : pos + 1500]
    # v0.20.2 of go-containerregistry (the project that ships crane)
    assert "v0.20.2" in body, "crane version must be pinned"


def test_deploy_md_documents_stable_tag():
    """DEPLOY.md must mention :stable so an operator under pressure
    can find the fallback handle without digging into deploy.yml."""
    doc = (REPO / "docs" / "DEPLOY.md").read_text()
    assert ":stable" in doc
    # The known-good handle is documented as the manual-pin command:
    assert "docker service update --image ghcr.io/dolr-ai/yral-rishi-agent:stable" in doc
