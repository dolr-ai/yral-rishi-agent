"""Source-level pins for the runpod_vllm key rotation workflow.

Same defense-in-depth shape as the other manual-trigger workflows
shipped 2026-06-09 (bootstrap-schema-migrations, roll-patroni-image).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "rotate-runpod-vllm-key.yml"


def _src() -> str:
    return WF.read_text()


def test_workflow_exists():
    assert WF.exists(), "rotate-runpod-vllm-key.yml missing"


def test_workflow_is_manual_only():
    """Rotating the bearer on every push would tear the cluster apart.
    Must be workflow_dispatch only."""
    src = _src()
    on_block = src.split("on:")[1].split("env:")[0]
    assert "workflow_dispatch" in on_block
    assert "push:" not in on_block
    assert "pull_request" not in on_block


def test_workflow_requires_typed_confirmation():
    """Same ROTATE KEY accidental-click guard pattern as rollback.yml /
    bootstrap-schema-migrations.yml / roll-patroni-image.yml."""
    src = _src()
    assert "ROTATE KEY" in src
    assert "i_understand" in src
    assert 'if [ "${{ inputs.i_understand }}" != "ROTATE KEY" ]' in src


def test_workflow_consumes_secret_from_github_secrets():
    """The bearer value comes from GitHub Secret RUNPOD_VLLM_API_KEY,
    NOT from workflow input. This makes rotation a 2-step user flow:
    update the GitHub Secret (UI), then trigger this workflow."""
    src = _src()
    assert "secrets.RUNPOD_VLLM_API_KEY" in src


def test_workflow_aborts_when_github_secret_empty():
    """If the secret is empty, fail before touching anything."""
    src = _src()
    assert "GitHub Secret RUNPOD_VLLM_API_KEY is empty" in src
    assert "exit 1" in src


def test_workflow_uses_sha8_suffix_for_new_secret_name():
    """Swarm secrets are immutable — must use content-derived name so
    rotations don't collide. Same SHA8 pattern as langfuse-install.sh."""
    src = _src()
    assert "sha256sum" in src
    assert "cut -c1-8" in src or "characters=1-8" in src


def test_workflow_mounts_at_canonical_target_path():
    """Target name MUST be RUNPOD_VLLM_API_KEY so it matches
    PROVIDERS['runpod_vllm']['secret_path'] in llm_registry. Drift here =
    runner reads stale secret = 4 background processes break."""
    src = _src()
    assert "RUNPOD_VLLM_API_KEY" in src
    assert "target=$TARGET" in src or "target=RUNPOD_VLLM_API_KEY" in src


def test_workflow_swaps_old_secret_for_new():
    src = _src()
    assert "--secret-add" in src
    assert "--secret-rm" in src


def test_workflow_uses_ssh_keyscan_not_static_known_hosts():
    """Lesson from PR #331 — modern OpenSSH expects ED25519 host keys
    and a static KNOWN_HOSTS may only have RSA. ssh-keyscan picks all."""
    src = _src()
    assert "ssh-keyscan" in src
    assert "secrets.KNOWN_HOSTS" not in src


def test_workflow_fails_over_across_managers():
    src = _src()
    assert "SWARM_MANAGER_1" in src
    assert "SWARM_MANAGER_2" in src
    assert "SWARM_MANAGER_3" in src
    assert "for host in" in src
