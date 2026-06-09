"""Source-level pins for the patroni image rolling-update workflow.

Built 2026-06-09 as Step 3 of the post-#314 recovery. Designed to
swap the patroni-pgvector image on each of the 3 swarm services safely
— one at a time, replicas first, leader last, with cluster-health
gating between every roll.

These tests defend the safety properties so future edits can't quietly
remove a guard.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "roll-patroni-image.yml"


def _src() -> str:
    return WF.read_text()


def test_workflow_exists():
    assert WF.exists(), "roll-patroni-image.yml missing"


def test_workflow_is_manual_only():
    """Rolling the live patroni cluster on every main push would be
    catastrophic — must be workflow_dispatch only."""
    src = _src()
    on_block = src.split("on:")[1].split("env:")[0]
    assert "workflow_dispatch" in on_block
    assert "push:" not in on_block
    assert "pull_request" not in on_block


def test_workflow_requires_typed_confirmation():
    """Same accidental-click guard as rollback.yml + the bootstrap
    workflow."""
    src = _src()
    assert "ROLL PATRONI" in src
    assert "i_understand" in src
    assert 'if [ "${{ inputs.i_understand }}" != "ROLL PATRONI" ]' in src


def test_workflow_rejects_floating_tags():
    """`:latest`/`:stable` make rollback ambiguous. SHA-tagged only."""
    src = _src()
    # The rejection step must exist before any docker service update.
    reject_pos = src.find("Reject floating tags")
    update_pos = src.find("docker service update --image")
    assert reject_pos > 0
    assert update_pos > 0
    assert reject_pos < update_pos
    assert '"latest"' in src and '"stable"' in src


def test_workflow_verifies_image_pulls_before_touching_services():
    """If the image isn't pullable, abort BEFORE rolling anything —
    otherwise the first service would fail to update + we'd be stuck."""
    src = _src()
    pull_pos = src.find("docker pull")
    update_pos = src.find("docker service update --image")
    assert pull_pos > 0
    assert pull_pos < update_pos


def test_workflow_rolls_leader_last():
    """Touching the leader is the highest-risk roll — defer until last.
    Order step must explicitly put non-leaders first, leader last."""
    src = _src()
    assert "Roll non-leaders first, leader last" in src
    assert "patronictl list" in src
    assert "Leader" in src


def test_workflow_waits_for_cluster_3_of_3_between_rolls():
    """Between each roll, wait until patronictl reports 3 members
    Running on the same timeline. Without this gate, we'd roll all 3
    even if the cluster was already broken after the first."""
    src = _src()
    assert "wait_cluster_healthy" in src
    # Must be called inside the roll loop, AND as a pre-flight.
    assert src.count("wait_cluster_healthy") >= 3  # function def + pre-flight call + in-loop call


def test_workflow_halts_on_first_failure():
    """If a single roll fails health checks, NO further rolls happen."""
    src = _src()
    assert "halting roll" in src
    # `exit 1` after each gate.
    assert src.count("exit 1") >= 5


def test_workflow_uses_start_first_update_order():
    """Without --update-order start-first, swarm tears down the old
    container BEFORE starting the new one — leaves the slot empty for
    seconds. start-first keeps the old container until the new one is
    Running."""
    src = _src()
    assert "--update-order start-first" in src
