"""Fleet drift detection, driven for real.

Each test runs the actual script as a subprocess and feeds it a swarm state on
stdin — the same way the nightly workflow does — then asserts on the exit code
and the message. Nothing here reads the script's source.

The IN_SYNC fixture below is the real output of `docker node inspect` across
the live fleet on 2026-09-25, so these test the format we actually receive.
"""

import subprocess
import sys
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
CHECKER = REPO / "scripts" / "ci" / "check_fleet_drift.py"

IN_SYNC = """\
rishi-1 worker ready active 138.201.137.181
rishi-2 worker ready active 136.243.150.84
rishi-3 worker ready active 136.243.147.225
rishi-4 manager ready active 138.201.128.108
rishi-5 manager ready active 88.99.160.251
rishi-6 manager ready active 162.55.88.112
"""


def run(swarm_state):
    return subprocess.run(
        [sys.executable, str(CHECKER), "--site", "hetzner-de"],
        input=swarm_state,
        capture_output=True,
        text=True,
        cwd=REPO,
    )


def test_passes_when_the_swarm_matches_the_inventory():
    result = run(IN_SYNC)
    assert result.returncode == 0, result.stdout
    assert "matches servers.config" in result.stdout


def test_catches_a_node_that_joined_without_being_written_down():
    joined = IN_SYNC + "rishi-7 worker ready active 5.9.44.77\n"
    result = run(joined)
    assert result.returncode == 1
    assert "rishi-7 is in the Swarm but not in servers.config" in result.stdout


def test_catches_a_node_in_the_file_that_never_joined():
    result = run(IN_SYNC.replace("rishi-3 worker ready active 136.243.147.225\n", ""))
    assert result.returncode == 1
    assert "rishi-3 is in servers.config but not in the Swarm" in result.stdout


def test_catches_a_role_that_drifted():
    """A manager quietly demoted is a quorum change nobody agreed to."""
    demoted = IN_SYNC.replace(
        "rishi-6 manager ready active", "rishi-6 worker ready active"
    )
    result = run(demoted)
    assert result.returncode == 1
    assert "rishi-6 is a worker in the Swarm but servers.config says manager" in (
        result.stdout
    )


def test_catches_an_address_that_changed_underneath_us():
    moved = IN_SYNC.replace("88.99.160.251", "88.99.160.99")
    result = run(moved)
    assert result.returncode == 1
    assert "An address changed underneath us" in result.stdout


def test_catches_a_node_that_is_down():
    down = IN_SYNC.replace("rishi-2 worker ready active", "rishi-2 worker down active")
    result = run(down)
    assert result.returncode == 1
    assert "rishi-2 is down" in result.stdout


def test_catches_a_node_left_drained():
    """Drain is how a node is taken out of service — and how one is forgotten."""
    drained = IN_SYNC.replace(
        "rishi-1 worker ready active", "rishi-1 worker ready drain"
    )
    result = run(drained)
    assert result.returncode == 1
    assert "Work will not schedule onto it" in result.stdout


def test_refuses_to_pass_when_it_got_no_swarm_state():
    """An unreachable manager must fail loudly, never look like agreement."""
    result = run("")
    assert result.returncode != 0
    assert "no swarm state on stdin" in (result.stdout + result.stderr)


def test_reports_every_difference_not_just_the_first():
    broken = IN_SYNC.replace(
        "rishi-6 manager ready active", "rishi-6 worker ready active"
    ).replace("rishi-2 worker ready active", "rishi-2 worker down active")
    result = run(broken)
    assert result.returncode == 1
    assert "2 difference(s)" in result.stdout


def run_flag(flag):
    return subprocess.run(
        [sys.executable, str(CHECKER), flag, "--site", "hetzner-de"],
        capture_output=True, text=True, cwd=REPO,
    )


def test_managers_flag_prints_the_manager_addresses():
    """The drift workflow reads the inventory with this instead of sourcing it,
    so that a job holding the production SSH key never executes the file."""
    result = run_flag("--managers")
    assert result.returncode == 0
    printed = result.stdout.split()
    assert printed == ["138.201.128.108", "88.99.160.251", "162.55.88.112"]


def test_ssh_user_flag_prints_the_manager_account():
    result = run_flag("--ssh-user")
    assert result.returncode == 0
    assert result.stdout.strip() == "rishi-deploy"


def test_a_second_site_is_not_reported_as_missing():
    """Each site is its own Swarm. Querying one must not flag the other's nodes."""
    result = run(IN_SYNC)
    assert result.returncode == 0, result.stdout
    assert "rishi-in-1" not in result.stdout
