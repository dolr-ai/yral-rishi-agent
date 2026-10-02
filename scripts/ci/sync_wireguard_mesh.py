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
tunnels, then each peer's route is (re)added.

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


# A node we can NEVER manage (no passwordless sudo, or our key isn't on it)
# is left out on purpose and reported. Anything else — a timeout, a refused
# connection — may be a blip, and dropping that node from everyone's peer
# list would cut it off the mesh. So those stop the whole run instead.
NOT_MANAGEABLE_EXIT = 42
NOT_MANAGEABLE_SSH = "Permission denied (publickey"


class TransientFailure(Exception):
    pass


def ensure_key_and_read_public_half(node):
    result = run_on(
        node,
        (
            f"sudo -n true || exit {NOT_MANAGEABLE_EXIT}; "
            "command -v wg >/dev/null || sudo apt-get install -y -qq wireguard-tools >/dev/null; "
            f"sudo sh -c 'umask 077; [ -s {PRIVATE_KEY_PATH} ] || wg genkey > {PRIVATE_KEY_PATH}; wg pubkey < {PRIVATE_KEY_PATH}'"
        ),
    )
    if result.returncode == NOT_MANAGEABLE_EXIT:
        return None, "needs passwordless sudo (ask the server's owner)"
    if NOT_MANAGEABLE_SSH in result.stderr:
        return None, "our SSH key is not on it"
    if result.returncode != 0:
        raise TransientFailure(f"{node['hostname']}: {result.stderr.strip()[-300:]}")
    return result.stdout.strip(), None


def render_apply_script(node, config, peers):
    """The shell script that runs on the node, as root, under a lock.

    The lock stops two runs racing. The config is built in a unique temp
    file and swapped in with one rename, so the live file is never half
    written. The private key is spliced in here, on the node, straight from
    its key file — it never crosses the wire, and printf is a shell builtin
    so it never shows up in the process list. It must be in the config
    itself: `wg syncconf` reads a config with no PrivateKey as "remove the
    key", which would drop every tunnel.

    The firewall opens the WireGuard port only to known peers, and lets
    everything arriving through the tunnel in: it is already authenticated by
    the peer's key, and the swarm's own ports ride inside it.
    """
    conf = f"/etc/wireguard/{MESH_INTERFACE}.conf"
    allow_peers = "\n".join(
        f"  ufw allow from {endpoint_for(p, node)} to any port {MESH_PORT} proto udp comment 'wg mesh' >/dev/null"
        for p in peers
        if p["hostname"] != node["hostname"]
    )
    return f"""set -e
umask 077
exec 9>/etc/wireguard/.mesh.lock
flock 9
tmp=$(mktemp /etc/wireguard/.{MESH_INTERFACE}.XXXXXX)
trap 'rm -f "$tmp" "$tmp.body"' EXIT
cat > "$tmp.body" <<'WG_MESH_CONFIG'
{config}WG_MESH_CONFIG
{{ sed -n '1,/^\\[Interface\\]/p' "$tmp.body"
  printf 'PrivateKey = %s\\n' "$(cat {PRIVATE_KEY_PATH})"
  sed '1,/^\\[Interface\\]/d' "$tmp.body"; }} > "$tmp"
mv -f "$tmp" {conf}
if ufw status | grep -q 'Status: active'; then
{allow_peers}
  ufw allow in on {MESH_INTERFACE} comment 'wg mesh' >/dev/null
fi
if ip link show {MESH_INTERFACE} >/dev/null 2>&1; then
  wg syncconf {MESH_INTERFACE} <(wg-quick strip {MESH_INTERFACE})
  # syncconf adds peers but not their routes (only wg-quick up does), so a
  # peer added to a live interface would be unreachable without this.
  for addr in $(wg show {MESH_INTERFACE} allowed-ips | cut -f2 | grep -v none); do
    ip route replace "$addr" dev {MESH_INTERFACE}
  done
else
  systemctl enable --now wg-quick@{MESH_INTERFACE} >/dev/null
fi
"""


def apply_on(node, config, peers):
    result = run_on(
        node, "sudo bash -s", stdin=render_apply_script(node, config, peers)
    )
    return result.returncode == 0, result.stderr.strip()[-300:]


def main():
    apply = "--apply" in sys.argv
    site = sys.argv[sys.argv.index("--site") + 1] if "--site" in sys.argv else None
    nodes = parse_inventory(INVENTORY.read_text())

    public_keys = {}
    if apply:
        try:
            for node in nodes:
                key, problem = ensure_key_and_read_public_half(node)
                if key:
                    public_keys[node["hostname"]] = key
                else:
                    print(f"✗ {node['hostname']}: left out of the mesh — {problem}")
        except TransientFailure as failure:
            sys.exit(
                f"STOPPED, nothing changed: could not reach {failure}. Re-run when it answers."
            )

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
