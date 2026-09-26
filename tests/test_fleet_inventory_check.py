"""The fleet-inventory gate, driven for real.

A CI gate with no test is the failure mode this repo keeps hitting: a check
that is green because it isn't looking. So these run the actual script as a
subprocess against a throwaway repo and assert on its exit code, rather than
reading its source.
"""

import shutil
import subprocess
import sys
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
CHECKER = REPO / "scripts" / "ci" / "check_fleet_inventory.py"

INVENTORY = """\
FLEET_NODES="
rishi-1  hetzner-de  worker   deploy        138.201.137.181  -
rishi-4  hetzner-de  manager  rishi-deploy  138.201.128.108  node_role=edge
rishi-5  hetzner-de  manager  rishi-deploy  88.99.160.251    node_role=edge
rishi-6  hetzner-de  manager  rishi-deploy  162.55.88.112    node_role=compute
"
"""


def build_repo(tmp_path, inventory=INVENTORY, workflow=""):
    (tmp_path / "scripts" / "ci").mkdir(parents=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    shutil.copy(CHECKER, tmp_path / "scripts" / "ci" / CHECKER.name)
    (tmp_path / "servers.config").write_text(inventory)
    if workflow:
        (tmp_path / ".github" / "workflows" / "deploy.yml").write_text(workflow)
    return tmp_path


def run(repo):
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "ci" / CHECKER.name)],
        capture_output=True,
        text=True,
    )


def test_passes_when_every_referenced_address_is_registered(tmp_path):
    repo = build_repo(tmp_path, workflow="env:\n  MANAGER: 138.201.128.108\n")
    assert run(repo).returncode == 0


def test_fails_on_an_address_the_inventory_does_not_list(tmp_path):
    """The case that matters: a new server reaches CI without being registered."""
    repo = build_repo(tmp_path, workflow="env:\n  NEW: 5.9.44.77\n")
    result = run(repo)
    assert result.returncode == 1
    assert "5.9.44.77" in result.stdout


def test_fails_on_a_row_with_the_wrong_column_count(tmp_path):
    broken = INVENTORY.replace(
        "rishi-1  hetzner-de  worker   deploy        138.201.137.181  -", "rishi-1  hetzner-de  worker"
    )
    assert run(build_repo(tmp_path, inventory=broken)).returncode == 1


def test_fails_on_a_duplicate_address(tmp_path):
    duplicated = INVENTORY.replace(
        "rishi-1  hetzner-de  worker   deploy        138.201.137.181  -",
        "rishi-1  hetzner-de  worker   deploy        88.99.160.251    -",
    )
    assert run(build_repo(tmp_path, inventory=duplicated)).returncode == 1


def test_fails_when_too_few_managers_to_hold_quorum(tmp_path):
    thin = INVENTORY.replace("rishi-6  hetzner-de  manager", "rishi-6  hetzner-de  worker ")
    result = run(build_repo(tmp_path, inventory=thin))
    assert result.returncode == 1
    assert "manager" in result.stdout


def test_private_addresses_are_ignored(tmp_path):
    """Overlay and health-check addresses are not fleet servers."""
    repo = build_repo(tmp_path, workflow="env:\n  LOCAL: 127.0.0.1\n  NET: 10.0.1.5\n")
    assert run(repo).returncode == 0


def test_the_real_inventory_in_this_repo_passes():
    """Guards against the committed servers.config drifting out of sync."""
    result = subprocess.run(
        [sys.executable, str(CHECKER)], capture_output=True, text=True, cwd=REPO
    )
    assert result.returncode == 0, result.stdout


def test_rejects_an_address_that_cannot_be_a_real_server(tmp_path):
    """Codex review on #529: 224.0.0.1 is multicast, not a host we can SSH to."""
    multicast = INVENTORY.replace("138.201.137.181", "224.0.0.1")
    result = run(build_repo(tmp_path, inventory=multicast))
    assert result.returncode == 1
    assert "not a routable public address" in result.stdout


def test_rejects_a_malformed_label(tmp_path):
    """Labels drive placement, so a broken one must not reach the cluster."""
    broken = INVENTORY.replace("node_role=edge", "node_role")
    result = run(build_repo(tmp_path, inventory=broken))
    assert result.returncode == 1
    assert "is not key=value" in result.stdout


def test_a_site_too_thin_for_quorum_fails(tmp_path):
    """Quorum is per site: 3 nodes in a site with 1 manager cannot survive it dying."""
    thin = INVENTORY.replace("rishi-5  hetzner-de  manager", "rishi-5  hetzner-de  worker ")
    thin = thin.replace("rishi-6  hetzner-de  manager", "rishi-6  hetzner-de  worker ")
    result = run(build_repo(tmp_path, inventory=thin))
    assert result.returncode == 1
    assert "hetzner-de" in result.stdout


def test_a_site_with_no_manager_at_all_fails(tmp_path):
    """Codex review on #530: a small site could previously skip manager checks
    entirely, so a worker-only Swarm — which can orchestrate nothing — passed."""
    workers_only = INVENTORY.replace("  manager  ", "  worker   ")
    result = run(build_repo(tmp_path, inventory=workers_only))
    assert result.returncode == 1
    assert "NO manager" in result.stdout
