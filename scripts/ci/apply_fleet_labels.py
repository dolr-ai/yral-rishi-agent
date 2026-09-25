#!/usr/bin/env python3
"""Reconcile a Swarm's node labels to what servers.config declares.

Labels are how a service says what KIND of machine it needs — `node_role=edge`
rather than `node.hostname == rishi-4`. That indirection is the whole reason a
box can be replaced, renumbered, or moved to another provider without editing a
single service definition. It only works if the labels on the live cluster
actually match the ones written down, which is what this reconciles.

Reads the live labels on stdin (the caller does the SSH, so the comparison is
testable without a cluster), one node per line:

    <hostname> <key=value,key=value|->
    rishi-4 node_role=edge,state_tier=primary

Prints a plan by default and changes NOTHING. With --apply it prints the
`docker node update` commands to run; it still executes nothing itself, because
a label change silently relocates running services and that deserves a human
looking at it.

Run:  ... | python scripts/ci/apply_fleet_labels.py --site hetzner-de [--apply]
"""

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
INVENTORY = REPO / "servers.config"


def parse_labels(field):
    if field == "-":
        return {}
    out = {}
    for pair in field.split(","):
        if pair.count("=") == 1 and all(pair.split("=")):
            key, value = pair.split("=")
            out[key] = value
    return out


def parse_inventory(text, site):
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
        hostname, node_site, _role, _user, _address, labels = fields
        if site and node_site != site:
            continue
        nodes[hostname] = parse_labels(labels)
    return nodes


def parse_live(text):
    nodes = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split()
        hostname = fields[0]
        nodes[hostname] = parse_labels(fields[1] if len(fields) > 1 else "-")
    return nodes


def plan_for(hostname, want, have):
    """The --label-add / --label-rm needed to turn `have` into `want`."""
    adds = {k: v for k, v in want.items() if have.get(k) != v}
    removes = [k for k in have if k not in want]
    commands = []
    if adds or removes:
        parts = [f"--label-add {k}={v}" for k, v in sorted(adds.items())]
        parts += [f"--label-rm {k}" for k in sorted(removes)]
        commands.append(f"docker node update {' '.join(parts)} {hostname}")
    return adds, removes, commands


def main():
    args = sys.argv[1:]
    site = args[args.index("--site") + 1] if "--site" in args else None
    apply_mode = "--apply" in args

    live_text = sys.stdin.read()
    if not live_text.strip():
        sys.exit("FAIL: no live node labels on stdin — could not reach a manager?")

    want_all = parse_inventory(INVENTORY.read_text(), site)
    have_all = parse_live(live_text)

    unknown = sorted(set(have_all) - set(want_all))
    missing = sorted(set(want_all) - set(have_all))
    commands, changed = [], 0

    for hostname in sorted(set(want_all) & set(have_all)):
        adds, removes, cmds = plan_for(hostname, want_all[hostname], have_all[hostname])
        if cmds:
            changed += 1
            for key, value in sorted(adds.items()):
                print(f"  {hostname}: set {key}={value}")
            for key in sorted(removes):
                print(f"  {hostname}: remove {key} (not in servers.config)")
            commands += cmds

    for hostname in unknown:
        print(f"  WARNING {hostname} is in the Swarm but not in servers.config "
              f"for site {site!r} — not touching it")
    for hostname in missing:
        print(f"  WARNING {hostname} is in servers.config but not in this Swarm")

    if not changed:
        print(f"Labels already match servers.config ({len(want_all)} nodes"
              + (f", site {site}" if site else "") + ").")
        return 0

    print(f"\n{changed} node(s) differ from servers.config.")
    if not apply_mode:
        print("Plan only. Re-run with --apply to print the commands.")
        return 0

    print("\nRun these on a manager of this site:\n")
    for command in commands:
        print(f"  {command}")
    print("\nNOT executed automatically: a label change relocates running "
          "services, so a person reads this first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
