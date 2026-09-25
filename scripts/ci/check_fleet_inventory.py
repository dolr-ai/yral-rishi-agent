#!/usr/bin/env python3
"""Keep servers.config honest.

Two jobs:

1. The inventory itself parses and is internally sane — four columns, a known
   role, no duplicate hostname or address, enough managers to hold quorum.

2. Every server address named anywhere else in the repo is registered in it.
   This is the half that matters. The old servers.config was wrong on every
   line for five months because the real host list lived as 27 copy-pasted IP
   literals across nine workflow files, and nothing compared the two. A list
   nobody checks is a list that quietly stops being true.

Run: python scripts/ci/check_fleet_inventory.py
"""

import ipaddress
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
INVENTORY = REPO / "servers.config"

# Where a hardcoded address would actually do damage: CI that deploys, and the
# scripts it calls. Docs may name a host in prose without breaking anything.
SEARCH_DIRS = (".github/workflows", "scripts", "bootstrap")
SEARCH_SUFFIXES = (".yml", ".yaml", ".sh", ".py")

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
ROLES = {"manager", "worker"}
MINIMUM_MANAGERS = 3


def parse_inventory(text):
    """The FLEET_NODES heredoc-style block, as a list of (host, role, user, ip)."""
    block = re.search(r'FLEET_NODES="\s*\n(.*?)"', text, re.DOTALL)
    if not block:
        sys.exit("FAIL: servers.config has no FLEET_NODES block")

    nodes = []
    for number, line in enumerate(block.group(1).splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 6:
            sys.exit(
                f"FAIL: servers.config line {number}: expected 6 columns "
                f"(hostname site role ssh_user public_ipv4 labels), "
                f"found {len(fields)}: {line!r}"
            )
        nodes.append(tuple(fields))
    return nodes


def check_inventory_is_sane(nodes):
    problems = []
    if not nodes:
        problems.append("FLEET_NODES is empty")

    for hostname, _site, role, _ssh_user, address, labels in nodes:
        if role not in ROLES:
            problems.append(f"{hostname}: role {role!r} is not one of {sorted(ROLES)}")
        if labels != "-":
            for pair in labels.split(","):
                if pair.count("=") != 1 or not all(pair.split("=")):
                    problems.append(
                        f"{hostname}: label {pair!r} is not key=value "
                        "(use '-' for no labels)"
                    )
        try:
            parsed = ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError:
            problems.append(f"{hostname}: {address!r} is not a valid IPv4 address")
            continue
        # Same exclusions the repo scan uses below. A multicast or reserved
        # address in this table is a typo that would otherwise be accepted and
        # then SSH'd to, so reject anything that cannot be a real server.
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_multicast
            or parsed.is_reserved
            or parsed.is_link_local
            or parsed.is_unspecified
        ):
            problems.append(
                f"{hostname}: {address} is not a routable public address"
            )

    for column, label in ((0, "hostname"), (4, "address")):
        seen = [node[column] for node in nodes]
        for value in sorted(set(seen)):
            if seen.count(value) > 1:
                problems.append(f"duplicate {label}: {value}")

    # Quorum is per SITE, not fleet-wide: sites are separate Swarms, so three
    # managers spread across two sites protects neither of them.
    managers = [node for node in nodes if node[2] == "manager"]
    for site in sorted({node[1] for node in nodes}):
        in_site = [n for n in nodes if n[1] == site]
        site_managers = [n for n in in_site if n[2] == "manager"]
        if len(in_site) >= MINIMUM_MANAGERS and len(site_managers) < MINIMUM_MANAGERS:
            problems.append(
                f"site {site} has {len(in_site)} nodes but only "
                f"{len(site_managers)} manager(s); it needs at least "
                f"{MINIMUM_MANAGERS} to survive losing one"
            )
    return problems, managers


def find_unregistered_addresses(known):
    """Addresses named in CI or scripts that the inventory doesn't list."""
    findings = []
    for directory in SEARCH_DIRS:
        root = REPO / directory
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in SEARCH_SUFFIXES or not path.is_file():
                continue
            for number, line in enumerate(
                path.read_text(errors="replace").splitlines(), start=1
            ):
                for candidate in IPV4.findall(line):
                    try:
                        parsed = ipaddress.IPv4Address(candidate)
                    except ipaddress.AddressValueError:
                        continue  # a version string, not an address
                    if parsed.is_private or parsed.is_loopback:
                        continue
                    if parsed.is_reserved or parsed.is_multicast:
                        continue
                    if candidate not in known:
                        findings.append(
                            (path.relative_to(REPO), number, candidate, line.strip())
                        )
    return findings


def main():
    if not INVENTORY.is_file():
        sys.exit("FAIL: servers.config not found")

    nodes = parse_inventory(INVENTORY.read_text())
    problems, managers = check_inventory_is_sane(nodes)
    known = {node[4] for node in nodes}
    unregistered = find_unregistered_addresses(known)

    for problem in problems:
        print(f"FAIL: {problem}")
    for path, number, address, line in unregistered:
        print(f"FAIL: {path}:{number} names {address}, which servers.config does not list")
        print(f"      {line}")

    if problems or unregistered:
        print()
        print("Add the server to servers.config, or correct the address above.")
        return 1

    sites = sorted({node[1] for node in nodes})
    print(f"servers.config: {len(nodes)} nodes across {len(sites)} site(s); "
          "every address referenced in CI is registered.")
    for site in sites:
        in_site = [n for n in nodes if n[1] == site]
        m = sum(1 for n in in_site if n[2] == "manager")
        print(f"  {site}: {len(in_site)} nodes ({m} managers, {len(in_site) - m} workers)")
    # Evenness matters per site, because each site is its own Raft group.
    for site in sites:
        count = sum(1 for n in nodes if n[1] == site and n[2] == "manager")
        if count > 1 and count % 2 == 0:
            print(
                f"NOTE: site {site} has {count} managers, an even number. Raft "
                f"needs a majority, so {count} tolerates the same number of "
                f"failures as {count - 1}. An odd count is the usual choice."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
