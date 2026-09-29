#!/usr/bin/env python3
"""Make every server in servers.config a peer on one private WireGuard network.

The fleet spans providers and continents, and will span more. Docker Swarm
wants one flat network where every node reaches every other node on its
advertised address. Public addresses do not give us that: an Azure VM does not
own its public IP (Azure rewrites it to a private one on arrival), and Docker's
built-in overlay encryption drops packets whose addresses don't match. So the
swarm runs on top of this mesh instead. Each node gets a `mesh_addr` in
servers.config, WireGuard encrypts everything between nodes, and to the swarm
every provider looks like one LAN.

Adding a server = adding its row with a mesh_addr, then running this. Every
node's peer list is rewritten from the inventory, so the new server becomes
reachable from all the others in one pass, and a removed row disappears.

Private keys are generated ON each node and never leave it; only public keys
travel. A node we cannot manage yet (no passwordless sudo) is reported and
left out of everyone's peer list until it can be.

Prints the plan by default and changes nothing. --apply writes the config and
brings the interface up; `wg syncconf` updates peers without dropping live
tunnels.

Run:  python scripts/ci/sync_wireguard_mesh.py [--site azure-in] [--apply]
"""

import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
INVENTORY = REPO / "servers.config"

MESH_INTERFACE = "wg-mesh"
MESH_PORT = 51820
# 1420 is WireGuard's own safe default for a 1500-byte underlay: its header
# costs 80 bytes, and anything bigger would be split on the wire.
MESH_MTU = 1420
# Azure and other clouds put nodes behind NAT; a keepalive holds the NAT
# mapping open so the far side can always reach back in.
KEEPALIVE_SECONDS = 25
PRIVATE_KEY_PATH = "/etc/wireguard/mesh.key"


def parse_labels(field):
    if field == "-":
        return {}
    return dict(pair.split("=", 1) for pair in field.split(","))


def parse_inventory(text):
    block = re.search(r'FLEET_NODES="\s*\n(.*?)"', text, re.DOTALL)
    if not block:
        sys.exit("FAIL: servers.config has no FLEET_NODES block")
    nodes = []
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        hostname, site, _role, ssh_user, public_ipv4, labels = line.split()
        labels = parse_labels(labels)
        if "mesh_addr" not in labels:
            continue
        nodes.append(
            {
                "hostname": hostname,
                "site": site,
                "ssh": f"{ssh_user}@{public_ipv4}",
                "public_ipv4": public_ipv4,
                "mesh_addr": labels["mesh_addr"],
                "private_addr": labels.get("private_addr"),
            }
        )
    return nodes


def endpoint_for(peer, local):
    # Same provider network: talk privately. Anything else: the internet.
    if peer["site"] == local["site"] and peer["private_addr"]:
        return peer["private_addr"]
    return peer["public_ipv4"]


def render_config(local, peers, public_keys):
    lines = [
        "# Written by scripts/ci/sync_wireguard_mesh.py from servers.config.",
        "# Edit servers.config and re-run it; hand edits here are overwritten.",
        "[Interface]",
        f"Address = {local['mesh_addr']}/32",
        f"ListenPort = {MESH_PORT}",
        f"MTU = {MESH_MTU}",
    ]
    for peer in peers:
        if peer["hostname"] == local["hostname"] or peer["hostname"] not in public_keys:
            continue
        lines += [
            "",
            f"# {peer['hostname']} ({peer['site']})",
            "[Peer]",
            f"PublicKey = {public_keys[peer['hostname']]}",
            f"Endpoint = {endpoint_for(peer, local)}:{MESH_PORT}",
            f"AllowedIPs = {peer['mesh_addr']}/32",
            f"PersistentKeepalive = {KEEPALIVE_SECONDS}",
        ]
    return "\n".join(lines) + "\n"


def run_on(node, command, stdin=None):
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", node["ssh"], command],
        input=stdin,
        capture_output=True,
        text=True,
    )


def ensure_key_and_read_public_half(node):
    result = run_on(
        node,
        (
            "sudo -n true || exit 42; "
            "command -v wg >/dev/null || sudo apt-get install -y -qq wireguard-tools >/dev/null; "
            f"sudo sh -c 'umask 077; [ -s {PRIVATE_KEY_PATH} ] || wg genkey > {PRIVATE_KEY_PATH}; wg pubkey < {PRIVATE_KEY_PATH}'"
        ),
    )
    if result.returncode == 42:
        return None, "needs passwordless sudo (ask the server's owner)"
    if result.returncode != 0:
        return None, result.stderr.strip()[-300:]
    return result.stdout.strip(), None


def apply_on(node, config, peers):
    # The firewall opens the WireGuard port only to known peers, and lets
    # everything arriving through the tunnel in: it is already authenticated
    # by the peer's key, and the swarm's own ports ride inside it.
    allow_peers = " ".join(
        f"sudo ufw allow from {endpoint_for(p, node)} to any port {MESH_PORT} proto udp comment 'wg mesh' >/dev/null;"
        for p in peers
        if p["hostname"] != node["hostname"]
    )
    # The private key is spliced in ON the node, straight from its key file,
    # so it never crosses the wire. It must be in the config itself: `wg
    # syncconf` treats a config with no PrivateKey as "remove the key", which
    # would silently drop every tunnel on the next peer update. printf is a
    # shell builtin, so the key never shows up in the process list either.
    conf = f"/etc/wireguard/{MESH_INTERFACE}.conf"
    command = (
        f"sudo tee {conf}.new >/dev/null && "
        f'sudo sh -c \'umask 077; {{ sed -n "1,/^\\[Interface\\]/p" {conf}.new; '
        f'printf "PrivateKey = %s\\n" "$(cat {PRIVATE_KEY_PATH})"; '
        f'sed "1,/^\\[Interface\\]/d" {conf}.new; }} > {conf} && rm {conf}.new\' && '
        f"if sudo ufw status | grep -q 'Status: active'; then {allow_peers} "
        f"sudo ufw allow in on {MESH_INTERFACE} comment 'wg mesh' >/dev/null; fi && "
        f"if ip link show {MESH_INTERFACE} >/dev/null 2>&1; then "
        f"sudo bash -c 'wg syncconf {MESH_INTERFACE} <(wg-quick strip {MESH_INTERFACE})'; "
        f"else sudo systemctl enable --now wg-quick@{MESH_INTERFACE} >/dev/null; fi"
    )
    result = run_on(node, command, stdin=config)
    return result.returncode == 0, result.stderr.strip()[-300:]


def main():
    apply = "--apply" in sys.argv
    site = sys.argv[sys.argv.index("--site") + 1] if "--site" in sys.argv else None
    nodes = parse_inventory(INVENTORY.read_text())

    public_keys = {}
    for node in nodes:
        key, problem = ensure_key_and_read_public_half(node) if apply else (None, None)
        if key:
            public_keys[node["hostname"]] = key
        elif problem:
            print(f"✗ {node['hostname']}: left out of the mesh — {problem}")

    targets = [n for n in nodes if (not site or n["site"] == site)]
    members = [n for n in nodes if n["hostname"] in public_keys] if apply else nodes
    for node in targets:
        if apply and node["hostname"] not in public_keys:
            continue
        peers = [p["hostname"] for p in members if p["hostname"] != node["hostname"]]
        print(
            f"{node['hostname']} {node['mesh_addr']} ← peers: {', '.join(peers) or 'none'}"
        )
        if apply:
            ok, error = apply_on(
                node, render_config(node, members, public_keys), members
            )
            print(f"  {'✓ applied' if ok else '✗ failed: ' + error}")
    if not apply:
        print(
            "\nPlan only. Re-run with --apply to write configs and bring the mesh up."
        )


if __name__ == "__main__":
    main()
