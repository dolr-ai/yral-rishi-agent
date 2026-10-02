"""WireGuard mesh config, driven for real.

Calls the script's own functions on an inventory shaped like the live one and
asserts on the config a node would actually receive. Nothing here reads the
script's source.
"""

import importlib.util
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "mesh", REPO / "scripts" / "ci" / "sync_wireguard_mesh.py"
)
mesh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mesh)

INVENTORY = """
FLEET_NODES="
rishi-1  hetzner-de  worker   deploy        138.201.137.181  mesh_addr=100.96.1.1
rishi-4  hetzner-de  manager  rishi-deploy  138.201.128.108  node_role=edge,mesh_addr=100.96.1.4
old-box  hetzner-de  worker   deploy        138.201.1.1      -
india-1  azure-in    manager  rishi         20.219.36.211    node_role=edge,private_addr=172.16.0.4,mesh_addr=100.96.2.1
india-2  azure-in    manager  rishi         20.219.222.27    private_addr=172.16.0.6,mesh_addr=100.96.2.2
"
"""
KEYS = {"rishi-1": "KEY1", "rishi-4": "KEY4", "india-1": "KEYI1", "india-2": "KEYI2"}


def nodes_by_name():
    return {n["hostname"]: n for n in mesh.parse_inventory(INVENTORY)}


def test_a_node_without_a_mesh_address_is_not_part_of_the_mesh():
    assert "old-box" not in nodes_by_name()


def test_peers_in_the_same_site_talk_over_the_private_network():
    nodes = nodes_by_name()
    assert mesh.endpoint_for(nodes["india-2"], nodes["india-1"]) == "172.16.0.6"


def test_peers_in_another_site_are_reached_on_their_public_address():
    nodes = nodes_by_name()
    assert mesh.endpoint_for(nodes["india-1"], nodes["rishi-4"]) == "20.219.36.211"
    assert mesh.endpoint_for(nodes["rishi-4"], nodes["india-1"]) == "138.201.128.108"


def test_config_lists_every_other_peer_and_never_itself():
    nodes = nodes_by_name()
    config = mesh.render_config(nodes["india-1"], list(nodes.values()), KEYS)
    assert "Address = 100.96.2.1/32" in config
    assert config.count("[Peer]") == 3
    assert "PublicKey = KEYI1" not in config
    assert "AllowedIPs = 100.96.1.4/32" in config
    assert "Endpoint = 172.16.0.6:51820" in config


def test_a_peer_without_a_key_yet_is_left_out_rather_than_half_configured():
    nodes = nodes_by_name()
    keys = {k: v for k, v in KEYS.items() if k != "rishi-1"}
    config = mesh.render_config(nodes["india-1"], list(nodes.values()), keys)
    assert "100.96.1.1" not in config
    assert config.count("[Peer]") == 2


def test_the_private_key_is_never_written_into_the_config():
    nodes = nodes_by_name()
    config = mesh.render_config(nodes["rishi-4"], list(nodes.values()), KEYS)
    assert "PrivateKey" not in config


def test_apply_script_writes_a_whole_config_and_leaves_no_temp_files(tmp_path):
    # Runs the real node-side script against a temp root, with stand-ins for
    # ufw / ip / systemctl / flock, and checks the file it leaves behind.
    import os
    import subprocess

    nodes = nodes_by_name()
    root = tmp_path / "etc" / "wireguard"
    root.mkdir(parents=True)
    (root / "mesh.key").write_text("FAKEPRIVATE=\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for tool, body in {
        "ufw": "echo 'Status: active'",
        "ip": "exit 1",
        "systemctl": "exit 0",
        "flock": "exit 0",
    }.items():
        (fake_bin / tool).write_text(f"#!/bin/sh\n{body}\n")
        (fake_bin / tool).chmod(0o755)
    config = mesh.render_config(nodes["india-1"], list(nodes.values()), KEYS)
    script = mesh.render_apply_script(nodes["india-1"], config, list(nodes.values()))
    result = subprocess.run(
        ["bash", "-s"],
        input=script.replace("/etc/wireguard", str(root)),
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )
    assert result.returncode == 0, result.stderr
    written = (root / "wg-mesh.conf").read_text().splitlines()
    assert written[written.index("[Interface]") + 1] == "PrivateKey = FAKEPRIVATE="
    assert written.count("[Peer]") == 3
    assert [f for f in os.listdir(root) if f.startswith(".wg-mesh")] == []


def test_a_peer_added_to_a_live_interface_gets_a_route(tmp_path):
    # 2026-10-01: India's interface was already up, so syncconf added the
    # Hetzner peers but no routes, and mesh traffic leaked out of eth0.
    import os
    import subprocess

    nodes = nodes_by_name()
    root = tmp_path / "etc" / "wireguard"
    root.mkdir(parents=True)
    (root / "mesh.key").write_text("FAKEPRIVATE=\n")
    routes = tmp_path / "routes"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for tool, body in {
        "ufw": "echo 'Status: inactive'",
        "flock": "exit 0",
        "wg-quick": "exit 0",
        "wg": 'if [ "$3" = allowed-ips ]; then printf "K4\\t100.96.1.4/32\\nK1\\t100.96.1.1/32\\nKX\\t(none)\\n"; fi',
        "ip": f'if [ "$1" = route ]; then echo "$3" >> {routes}; fi',
    }.items():
        (fake_bin / tool).write_text(f"#!/bin/sh\n{body}\n")
        (fake_bin / tool).chmod(0o755)
    config = mesh.render_config(nodes["india-1"], list(nodes.values()), KEYS)
    script = mesh.render_apply_script(nodes["india-1"], config, list(nodes.values()))
    result = subprocess.run(
        ["bash", "-s"],
        input=script.replace("/etc/wireguard", str(root)),
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )
    assert result.returncode == 0, result.stderr
    assert routes.read_text().split() == ["100.96.1.4/32", "100.96.1.1/32"]


def test_a_transient_failure_stops_the_run_instead_of_dropping_the_node(monkeypatch):
    import subprocess

    def unreachable(node, command, stdin=None):
        return subprocess.CompletedProcess(
            [], 255, "", "ssh: connect to host: Connection timed out"
        )

    monkeypatch.setattr(mesh, "run_on", unreachable)
    node = nodes_by_name()["india-1"]
    try:
        mesh.ensure_key_and_read_public_half(node)
    except mesh.TransientFailure:
        return
    raise AssertionError("a timeout must stop the run, not remove the node")


def test_a_node_without_our_key_is_left_out_not_fatal(monkeypatch):
    import subprocess

    def denied(node, command, stdin=None):
        return subprocess.CompletedProcess(
            [], 255, "", "deploy@x: Permission denied (publickey)."
        )

    monkeypatch.setattr(mesh, "run_on", denied)
    key, problem = mesh.ensure_key_and_read_public_half(nodes_by_name()["rishi-1"])
    assert key is None and "SSH key" in problem
