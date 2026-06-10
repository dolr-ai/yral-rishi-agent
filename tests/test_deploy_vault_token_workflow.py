"""Source-level pins for the Vault-token deploy workflow.

Same defense-in-depth shape as the bootstrap + patroni-roll workflows.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "deploy-vault-token.yml"


def _src() -> str:
    return WF.read_text()


def test_workflow_exists():
    assert WF.exists(), "deploy-vault-token.yml missing"


def test_workflow_is_manual_only():
    """Rolling the vault token on every push would defeat the audit
    purpose — must be workflow_dispatch only."""
    src = _src()
    on_block = src.split("on:")[1].split("env:")[0]
    assert "workflow_dispatch" in on_block
    assert "push:" not in on_block
    assert "pull_request" not in on_block


def test_workflow_requires_typed_confirmation():
    """Same DEPLOY VAULT TOKEN guard pattern as rollback.yml /
    bootstrap-schema-migrations.yml / roll-patroni-image.yml."""
    src = _src()
    assert "DEPLOY VAULT TOKEN" in src
    assert "i_understand" in src
    assert 'if [ "${{ inputs.i_understand }}" != "DEPLOY VAULT TOKEN" ]' in src


def test_workflow_takes_vault_token_as_input():
    """The token has to come from somewhere — workflow_dispatch input
    (masked at GitHub side). Not pulled from secrets store because the
    point of Vault is to NOT have the token in GH secrets."""
    src = _src()
    assert "vault_token:" in src
    assert "inputs.vault_token" in src


def test_workflow_uses_sha8_suffix_for_secret_name():
    """Swarm secrets are immutable — must use a content-derived name so
    rotations don't collide. Same pattern as langfuse-install.sh."""
    src = _src()
    assert "sha256sum" in src
    assert "cut -c1-8" in src or "characters=1-8" in src


def test_workflow_attaches_secret_at_vault_token_target():
    """The mount target must match what infra/vault.py reads from
    (/run/secrets/vault_token). Drift here = service can't find the
    token."""
    src = _src()
    assert "target=vault_token" in src or 'target=\"vault_token\"' in src or "target=$TARGET" in src
    assert "vault_token" in src  # the literal target name


def test_workflow_swaps_old_secret_for_new():
    """Service spec has at most one secret mounted at a given target.
    Workflow must --secret-rm the old before/with --secret-add of the
    new, otherwise the update fails."""
    src = _src()
    assert "--secret-add" in src
    assert "--secret-rm" in src


def test_workflow_uses_ssh_keyscan_not_static_known_hosts():
    """Lesson from PR #331 — modern OpenSSH requires the host key
    algorithm advertised by the server (ED25519) and a static
    KNOWN_HOSTS secret may only have RSA. ssh-keyscan picks them all."""
    src = _src()
    assert "ssh-keyscan" in src
    assert "secrets.KNOWN_HOSTS" not in src


def test_workflow_fails_over_across_managers():
    """If the first manager doesn't respond, try the next. Matches
    deploy.yml + bootstrap-schema-migrations.yml."""
    src = _src()
    assert "SWARM_MANAGER_1" in src
    assert "SWARM_MANAGER_2" in src
    assert "SWARM_MANAGER_3" in src
    assert "for host in" in src
