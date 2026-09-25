#!/usr/bin/env python3
"""Does the live Swarm still match servers.config?

servers.config says what the fleet should be. This says what it IS, and fails
when they disagree — a node joined but never written down, a node written down
that never joined, a role that drifted, an address that changed underneath us,
or a node that is Down or Drained and nobody noticed.

The swarm state arrives on **stdin** rather than being fetched here, so the
comparison is testable without SSH and without a cluster. The workflow pipes it
in; see .github/workflows/fleet-drift.yml for the producing command.

Expected stdin, one line per node, five whitespace-separated fields:

    <hostname> <role> <state> <availability> <address>
    rishi-4 manager ready active 138.201.128.108

Run:  docker node inspect ... | python scripts/ci/check_fleet_drift.py
"""

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
INVENTORY = REPO / "servers.config"

HEALTHY_STATE = "ready"
HEALTHY_AVAILABILITY = "active"


def parse_inventory(text):
    block = re.search(r'FLEET_NODES="\s*\n(.*?)"', text, re.DOTALL)
    if not block:
        sys.exit("FAIL: servers.config has no FLEET_NODES block")
    nodes = {}
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 6:
            sys.exit(f"FAIL: servers.config row is not 6 columns: {line!r}")
        hostname, site, role, ssh_user, address, labels = fields
        nodes[hostname] = {"site": site, "role": role, "ssh_user": ssh_user,
                           "address": address, "labels": labels}
    return nodes


def parse_swarm(text):
    nodes = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) != 5:
            sys.exit(
                "FAIL: swarm state line is not 5 fields "
                f"(hostname role state availability address): {line!r}"
            )
        hostname, role, state, availability, address = fields
        nodes[hostname] = {
            "role": role.lower(),
            "state": state.lower(),
            "availability": availability.lower(),
            "address": address,
        }
    return nodes


def compare(expected, actual):
    drift = []

    for hostname in sorted(set(actual) - set(expected)):
        drift.append(
            f"{hostname} is in the Swarm but not in servers.config "
            f"(address {actual[hostname]['address']}). Add it, or remove the node."
        )

    for hostname in sorted(set(expected) - set(actual)):
        drift.append(
            f"{hostname} is in servers.config but not in the Swarm. "
            "It never joined, or it was removed without updating the file."
        )

    for hostname in sorted(set(expected) & set(actual)):
        want, have = expected[hostname], actual[hostname]
        if want["role"] != have["role"]:
            drift.append(
                f"{hostname} is a {have['role']} in the Swarm but "
                f"servers.config says {want['role']}."
            )
        if want["address"] != have["address"]:
            drift.append(
                f"{hostname} answers on {have['address']} but servers.config "
                f"says {want['address']}. An address changed underneath us."
            )
        if have["state"] != HEALTHY_STATE:
            drift.append(f"{hostname} is {have['state']}, not {HEALTHY_STATE}.")
        if have["availability"] != HEALTHY_AVAILABILITY:
            drift.append(
                f"{hostname} availability is {have['availability']}, not "
                f"{HEALTHY_AVAILABILITY}. Work will not schedule onto it."
            )

    return drift


def main():
    # Two read-only queries the workflow uses to find the cluster. They exist
    # so the job can READ the inventory instead of `source`-ing it: sourcing
    # runs whatever the file contains, and that job is holding an SSH key to
    # every production node.
    if len(sys.argv) > 1 and sys.argv[1] in ("--managers", "--ssh-user"):
        nodes = parse_inventory(INVENTORY.read_text())
        wanted_site = sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--site" else None
        if wanted_site:
            nodes = {h: n for h, n in nodes.items() if n["site"] == wanted_site}
        if sys.argv[1] == "--managers":
            managers = [n for n in nodes.values() if n["role"] == "manager"]
            if not managers:
                sys.exit("FAIL: servers.config lists no managers")
            print(" ".join(node["address"] for node in managers))
        else:
            users = {node["ssh_user"] for node in nodes.values() if node["role"] == "manager"}
            if len(users) != 1:
                sys.exit(f"FAIL: managers disagree on ssh_user: {sorted(users)}")
            print(users.pop())
        return 0

    # Sites are separate Swarms. Comparing one site's live nodes against the
    # whole inventory would report every node of every OTHER site as missing,
    # so the caller says which site this swarm state came from.
    site = None
    if "--site" in sys.argv:
        index = sys.argv.index("--site")
        if index + 1 < len(sys.argv):
            site = sys.argv[index + 1]

    swarm_text = sys.stdin.read()
    if not swarm_text.strip():
        sys.exit("FAIL: no swarm state on stdin — could not reach a manager?")

    expected = parse_inventory(INVENTORY.read_text())
    if site:
        expected = {h: n for h, n in expected.items() if n["site"] == site}
        if not expected:
            sys.exit(f"FAIL: servers.config lists no nodes for site {site!r}")
    actual = parse_swarm(swarm_text)
    drift = compare(expected, actual)

    if drift:
        print(f"Fleet has drifted from servers.config — {len(drift)} difference(s):")
        print()
        for item in drift:
            print(f"  FAIL: {item}")
        print()
        print("servers.config is the source of truth. Either the fleet is wrong")
        print("or the file is. Decide which, then fix that one.")
        return 1

    managers = sum(1 for node in actual.values() if node["role"] == "manager")
    print(
        f"Fleet matches servers.config: {len(actual)} nodes "
        f"({managers} managers, {len(actual) - managers} workers), "
        "all ready and active."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
