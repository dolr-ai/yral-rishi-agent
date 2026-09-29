#!/bin/bash
# site-bootstrap.sh — turn a fresh machine into a member of a SITE swarm.
#
# A site is one Docker Swarm on one provider's private network. Adding a
# provider or a geography means adding a site, never bolting a distant node
# onto an existing swarm: on 2026-09-28 we proved an overlay cannot span
# Hetzner<->Azure (130 ms; VXLAN + IPsec overflow the path MTU, nodes join but
# data silently drops, and it took amorae down). Within a site the overlay is
# perfect. This script is exactly the sequence that built `azure-in`, codified.
#
# Run ON the machine, as a user with passwordless sudo:
#
# Every run also needs FLEET_CI_PUBLIC_KEY="$(cat bootstrap/fleet-ci-key.pub)".
#
#   first node:   SITE=azure-in PRIVATE_IP=172.16.0.4 PRIVATE_SUBNET=172.16.0.0/24 \
#                 PUBLIC_PEERS="20.219.222.27 20.235.107.58" bash site-bootstrap.sh init
#   other nodes:  SITE=azure-in PRIVATE_IP=172.16.0.6 PRIVATE_SUBNET=172.16.0.0/24 \
#                 PUBLIC_PEERS="20.219.36.211 20.235.107.58" JOIN=172.16.0.4:2377 \
#                 TOKEN=<manager token from first node> bash site-bootstrap.sh join
#
# Then add the node's row to servers.config. Labels are applied there, not here.
set -euo pipefail

MODE="${1:?usage: site-bootstrap.sh init|join}"
: "${SITE:?SITE is required (e.g. azure-in)}"
: "${PRIVATE_IP:?PRIVATE_IP is required (this node's address on the site's private network)}"
: "${PRIVATE_SUBNET:?PRIVATE_SUBNET is required (e.g. 172.16.0.0/24)}"
PUBLIC_PEERS="${PUBLIC_PEERS:-}"

# ── Docker ────────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sudo sh >/tmp/docker-install.log 2>&1
    sudo usermod -aG docker "$(whoami)"
fi

# ── Fleet access ──────────────────────────────────────────────────────────
# CI (deploys, the nightly drift check) reaches every server with one fleet
# key, whatever the provider. Its public half lives in the repo next to this
# script; pass it in so a new node is reachable the moment it exists.
: "${FLEET_CI_PUBLIC_KEY:?FLEET_CI_PUBLIC_KEY is required (contents of bootstrap/fleet-ci-key.pub)}"
mkdir -p ~/.ssh && chmod 700 ~/.ssh
touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
# authorized_keys files often lack a final newline; appending onto the last
# key's line would silently break both keys.
[ -s ~/.ssh/authorized_keys ] && [ -n "$(tail -c1 ~/.ssh/authorized_keys)" ] && echo >> ~/.ssh/authorized_keys
grep -qF "$FLEET_CI_PUBLIC_KEY" ~/.ssh/authorized_keys || echo "$FLEET_CI_PUBLIC_KEY" >> ~/.ssh/authorized_keys

# ── Firewall ──────────────────────────────────────────────────────────────
# Public: only what the site publishes. Swarm ports ride the PRIVATE subnet, so
# they never face the internet. Public peers are allowed too only because a
# cloud NSG may route node-to-node traffic over public addresses; harmless
# otherwise. IPsec ESP (protocol 50) is what the encrypted overlay actually
# carries data on — forget it and nodes join but every packet vanishes.
# SSH is allowed first and unconditionally, so this cannot lock us out.
sudo ufw --force reset >/dev/null
sudo ufw default deny incoming >/dev/null
sudo ufw default allow outgoing >/dev/null
sudo ufw allow 22/tcp  comment 'ssh'   >/dev/null
sudo ufw allow 80/tcp  comment 'http'  >/dev/null
sudo ufw allow 443/tcp comment 'https' >/dev/null
allow_swarm_from() {
    sudo ufw allow from "$1" to any port 2377 proto tcp comment 'swarm mgmt'      >/dev/null
    sudo ufw allow from "$1" to any port 7946 proto tcp comment 'swarm discovery' >/dev/null
    sudo ufw allow from "$1" to any port 7946 proto udp comment 'swarm gossip'    >/dev/null
    sudo ufw allow from "$1" to any port 4789 proto udp comment 'swarm overlay'   >/dev/null
    sudo ufw allow from "$1" proto esp                   comment 'overlay ipsec'  >/dev/null
}
allow_swarm_from "$PRIVATE_SUBNET"
for peer in $PUBLIC_PEERS; do allow_swarm_from "$peer"; done
sudo ufw --force enable >/dev/null

# ── Swarm ─────────────────────────────────────────────────────────────────
# Advertise the PRIVATE address. Advertising the public one sends overlay
# traffic out to the internet and back — and past the MTU wall.
# `sudo docker`, because the usermod above only takes effect at next login:
# on a fresh machine this shell is not yet in the docker group.
case "$MODE" in
    init)
        sudo docker swarm init --advertise-addr "$PRIVATE_IP" --listen-addr 0.0.0.0:2377 >/dev/null
        echo "site $SITE initialised on $PRIVATE_IP"
        echo "manager join token (for the other nodes):"
        sudo docker swarm join-token -q manager
        ;;
    join)
        : "${JOIN:?JOIN is required for join (e.g. 172.16.0.4:2377)}"
        : "${TOKEN:?TOKEN is required for join}"
        sudo docker swarm join --token "$TOKEN" --advertise-addr "$PRIVATE_IP" \
            --listen-addr 0.0.0.0:2377 "$JOIN" >/dev/null
        echo "joined site $SITE via $JOIN as $PRIVATE_IP"
        ;;
    *) echo "unknown mode: $MODE (init|join)" >&2; exit 2 ;;
esac

echo "$(hostname): docker=$(sudo docker --version | cut -d, -f1) ufw=$(sudo ufw status | head -1) swarm=$(sudo docker info --format '{{.Swarm.LocalNodeState}}')"
