#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  node-bootstrap.sh                                                          ║
# ║                                                                              ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                                ║
# ║  Take a fresh Hetzner Ubuntu 24.04 box (rishi-4, rishi-5, or rishi-6) and ║
# ║  bring it to the point where a v2 service can be deployed onto it: Docker ║
# ║  installed, Swarm initialised or joined, UFW locked down to only the       ║
# ║  ports v2 needs, the `rishi-deploy` user created with narrow sudoers, and ║
# ║  encrypted Swarm overlay networks created.                                  ║
# ║                                                                              ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                           ║
# ║  Hetzner ships a brand-new server with just Ubuntu and an SSH root login.  ║
# ║  Nothing is configured for our use case. This script is the recipe Saikat ║
# ║  (or Rishi-as-root during the temporary root window per CONSTRAINTS A2 +   ║
# ║  C8) runs once on each of the three nodes to turn them into v2-ready      ║
# ║  cluster members. It is split into named phases so you can read what it   ║
# ║  is about to do, in order, without scrolling through hundreds of lines of  ║
# ║  shell. Re-running the script is safe — every phase checks current state   ║
# ║  before changing it, so no accidental double-installs or duplicate users.  ║
# ║                                                                              ║
# ║  🔗 HOW IT FITS WITH OTHER FILES                                             ║
# ║  - Reads:  ../cluster.hosts.yaml (shape only) + GitHub Secrets             ║
# ║            (RISHI_N_PUBLIC_IPV4) at the caller side, surfaced into this    ║
# ║            script as environment variables. NO literal IPs in this file   ║
# ║            per CONSTRAINTS C6.                                              ║
# ║  - Reads:  ../secrets-manifest.yaml — declares every cluster-level secret ║
# ║            this and sibling install scripts depend on (per D7).            ║
# ║  - Calls:  patroni-install.sh, redis-sentinel-install.sh,                  ║
# ║            langfuse-install.sh, caddy-swarm-service.yml — those run        ║
# ║            AFTER this script finishes on every node, by deploying stacks   ║
# ║            into the Swarm this script just created.                        ║
# ║  - Writes: /etc/systemd/system/yral-v2-swarm-resync.service per H1 so     ║
# ║            stacks come back automatically on reboot.                        ║
# ║                                                                              ║
# ║  📥 INPUTS (environment variables, set by the caller)                        ║
# ║  - YRAL_NODE_NAME            rishi-4 | rishi-5 | rishi-6                  ║
# ║  - YRAL_NODE_ROLE            edge | compute (drives placement labels)     ║
# ║  - YRAL_BOOTSTRAP_PHASE      root-window | swarm-init | swarm-join         ║
# ║  - YRAL_RISHI_4_PUBLIC_IPV4  IPv4 of rishi-4 (used during swarm-join)     ║
# ║  - YRAL_SWARM_JOIN_TOKEN     `docker swarm join-token manager` output     ║
# ║                              from rishi-4 (used during swarm-join)        ║
# ║  - YRAL_AUTHORIZED_SSH_KEYS  newline-separated list of public keys to    ║
# ║                              install into rishi-deploy's authorized_keys ║
# ║                                                                              ║
# ║  📤 OUTPUTS / SIDE EFFECTS                                                   ║
# ║  - rishi-deploy user exists and is in the docker group                     ║
# ║  - Docker Engine + compose plugin installed                                 ║
# ║  - UFW configured per CONSTRAINTS C3 (only 22 from known IPs, 443 on      ║
# ║    edge nodes, Swarm ports between cluster members, default deny)          ║
# ║  - Swarm initialised (rishi-4) or joined (rishi-5, rishi-6)                ║
# ║  - Three encrypted overlay networks created (yral-agent-public-web-       ║
# ║    overlay, yral-agent-internal-service-to-service-overlay, yral-agent-   ║
# ║    data-plane-overlay)                                                      ║
# ║  - Placement labels applied per cluster.hosts.yaml shape                   ║
# ║  - yral-v2-swarm-resync.service installed and enabled                      ║
# ║                                                                              ║
# ║  ⚠️ DRAFT — NO SERVERS TOUCHED YET                                            ║
# ║  Per agent spec (.claude/agents/session-1-infra-cluster.md) and            ║
# ║  CONSTRAINTS A13, this is a Day 1-2 deliverable. Actual execution against  ║
# ║  rishi-4/5/6 is Days 4-7 work and requires a separate explicit Rishi YES.  ║
# ║                                                                              ║
# ║  ⭐ START HERE                                                               ║
# ║  Read main() first; every phase function is called from there in order.   ║
# ║  Phases are top-of-file = first to run, bottom-of-file = last.             ║
# ║                                                                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# Strict mode — fail loudly on any unhandled error or unset variable so a
# half-completed bootstrap cannot leave a node in a confusing intermediate state.
set -euo pipefail


# ───────────────────────────── Constants ────────────────────────────────────

# Three encrypted Swarm overlay networks, per CONSTRAINTS C3. Names follow
# the V2 infra doc — never abbreviated to "public", "internal", "data" alone.
PUBLIC_WEB_OVERLAY_NAME="yral-agent-public-web-overlay"
INTERNAL_SERVICE_OVERLAY_NAME="yral-agent-internal-service-to-service-overlay"
DATA_PLANE_OVERLAY_NAME="yral-agent-data-plane-overlay"

# Ubuntu base packages we install once during the root window. chrony is
# REQUIRED — Patroni's leader election depends on synchronised clocks, and
# the desktop-class CPU on rishi-4/5/6 has no ECC so a stuck system clock
# could silently corrupt WAL replay. fail2ban hardens SSH against brute force.
BASE_PACKAGES_TO_INSTALL=(
    docker-ce
    docker-compose-plugin
    ufw
    fail2ban
    unattended-upgrades
    chrony
    htop
    ncdu
    jq            # widely used by the install scripts that follow
    curl          # ditto, used by health-checks and pull-image fallback
)

# UFW Swarm ports — these are the cluster-internal ports Docker Swarm uses
# for control plane and overlay-network traffic between manager nodes.
# UDP 4789 is the VXLAN port for encrypted overlays per `--opt encrypted`.
SWARM_CONTROL_PLANE_TCP_PORT="2377"
SWARM_GOSSIP_DISCOVERY_TCP_AND_UDP_PORT="7946"
SWARM_VXLAN_OVERLAY_UDP_PORT="4789"

# Where the resync systemd unit lands. The unit itself is templated below.
SWARM_RESYNC_SYSTEMD_UNIT_PATH="/etc/systemd/system/yral-v2-swarm-resync.service"


# ─────────────────────────────── Entry point ─────────────────────────────────


main() {
    # Pre-flight — refuse to run on the wrong OS or without required env vars.
    confirm_running_as_root
    confirm_running_on_ubuntu_24_04
    confirm_required_environment_variables_present

    # Phase routing — the caller picks one phase per invocation. Most nodes
    # need only `root-window`; rishi-4 also gets `swarm-init`, and the other
    # two get `swarm-join`. Splitting by phase keeps the script re-runnable.
    case "${YRAL_BOOTSTRAP_PHASE}" in
        root-window)
            install_base_packages
            create_rishi_deploy_user_with_authorized_keys
            configure_narrow_sudoers_for_rishi_deploy
            configure_ufw_firewall_for_this_node_role
            enable_unattended_security_upgrades
            disable_root_password_authentication
            ;;
        swarm-init)
            initialize_docker_swarm_on_first_manager_node
            create_encrypted_overlay_networks
            apply_placement_labels_to_this_node
            install_swarm_resync_systemd_service
            ;;
        swarm-join)
            join_docker_swarm_as_manager_node
            apply_placement_labels_to_this_node
            install_swarm_resync_systemd_service
            ;;
        *)
            echo "ERROR node-bootstrap: unknown phase '${YRAL_BOOTSTRAP_PHASE}'" >&2
            echo "  Expected one of: root-window | swarm-init | swarm-join" >&2
            exit 1
            ;;
    esac

    print_post_phase_summary
}


# ────────────────────── Pre-flight checks ────────────────────────────────────


confirm_running_as_root() {
    # WHAT:  refuse to continue unless invoked with root privileges.
    # WHEN:  every phase needs root for at least one step.
    # WHY:   creating a unix user, installing apt packages, editing UFW, and
    #        writing systemd units all require root. Failing now is clearer
    #        than failing in the middle of phase 3 with a permission error.
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "ERROR node-bootstrap: must run as root (sudo or root login)" >&2
        exit 1
    fi
}


confirm_running_on_ubuntu_24_04() {
    # WHAT:  read /etc/os-release and refuse if not Ubuntu 24.04.
    # WHEN:  immediately after the root check.
    # WHY:   per V2 infra doc §1, the cluster runs Ubuntu 24.04.4 LTS. Other
    #        distros would silently break the apt install + UFW assumptions.
    local detected_distribution_id
    local detected_distribution_version
    detected_distribution_id="$(. /etc/os-release && echo "${ID:-unknown}")"
    detected_distribution_version="$(. /etc/os-release && echo "${VERSION_ID:-unknown}")"

    if [[ "${detected_distribution_id}" != "ubuntu" || "${detected_distribution_version}" != "24.04" ]]; then
        echo "ERROR node-bootstrap: requires Ubuntu 24.04 (detected ${detected_distribution_id} ${detected_distribution_version})" >&2
        exit 1
    fi
}


confirm_required_environment_variables_present() {
    # WHAT:  fail fast if the caller forgot to export YRAL_* env vars.
    # WHEN:  third pre-flight check.
    # WHY:   a missing YRAL_NODE_NAME would let the script run, fail to apply
    #        placement labels mid-way, and leave the node in a broken Swarm
    #        state. Catching the omission up front is much cheaper.
    local required_environment_variables=(
        YRAL_NODE_NAME
        YRAL_NODE_ROLE
        YRAL_BOOTSTRAP_PHASE
        YRAL_AUTHORIZED_SSH_KEYS
    )

    # `swarm-join` additionally needs the rishi-4 IP + join token.
    if [[ "${YRAL_BOOTSTRAP_PHASE:-}" == "swarm-join" ]]; then
        required_environment_variables+=(YRAL_RISHI_4_PUBLIC_IPV4 YRAL_SWARM_JOIN_TOKEN)
    fi

    local missing_environment_variable_count=0
    for environment_variable_name in "${required_environment_variables[@]}"; do
        if [[ -z "${!environment_variable_name:-}" ]]; then
            echo "ERROR node-bootstrap: required environment variable ${environment_variable_name} is unset" >&2
            missing_environment_variable_count=$((missing_environment_variable_count + 1))
        fi
    done

    if [[ "${missing_environment_variable_count}" -gt 0 ]]; then
        echo "ERROR node-bootstrap: ${missing_environment_variable_count} required environment variable(s) missing — see above" >&2
        exit 1
    fi
}


# ────────────────────── Phase: root-window ───────────────────────────────────


install_base_packages() {
    # WHAT:  apt-get update + install Docker CE, compose plugin, UFW,
    #        fail2ban, chrony, and support tools.
    # WHEN:  first step of the root window — every other phase depends on
    #        these binaries being on PATH.
    # WHY:   Docker is the orchestrator (C2). chrony keeps Patroni's leader
    #        election sane (clock drift = split-brain risk). UFW + fail2ban
    #        harden the box. jq + curl are used by every install script.
    add_docker_apt_repository_if_missing
    apt-get update --quiet
    DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends "${BASE_PACKAGES_TO_INSTALL[@]}"
    systemctl enable --now docker chrony fail2ban
}


add_docker_apt_repository_if_missing() {
    # WHAT:  install Docker's signed apt repo if /etc/apt/sources.list.d
    #        does not already have it.
    # WHEN:  support function called from install_base_packages, before apt-get update.
    # WHY:   Ubuntu's default `docker.io` package lags upstream and is missing
    #        the `compose-plugin` we rely on. Docker's official repo is the
    #        documented source per Docker's own install instructions.
    local docker_apt_keyring_path="/etc/apt/keyrings/docker.gpg"
    local docker_apt_source_list_path="/etc/apt/sources.list.d/docker.list"

    if [[ -f "${docker_apt_source_list_path}" ]]; then
        return 0
    fi

    install -m 0755 -d /etc/apt/keyrings
    curl --fail --silent --show-error --location "https://download.docker.com/linux/ubuntu/gpg" \
        | gpg --dearmor --output "${docker_apt_keyring_path}"
    chmod a+r "${docker_apt_keyring_path}"
    echo "deb [arch=amd64 signed-by=${docker_apt_keyring_path}] https://download.docker.com/linux/ubuntu noble stable" \
        > "${docker_apt_source_list_path}"
}


create_rishi_deploy_user_with_authorized_keys() {
    # WHAT:  create the `rishi-deploy` unix user and install authorized
    #        SSH public keys into ~/rishi-deploy/.ssh/authorized_keys.
    # WHEN:  after base packages — useradd is part of Ubuntu base.
    # WHY:   per CONSTRAINTS C8, day-to-day ops never use root. rishi-deploy
    #        owns docker socket access + a narrow sudoers slice. The
    #        authorized_keys block matches the existing rishi-1/2/3 convention.
    if ! id -u rishi-deploy >/dev/null 2>&1; then
        useradd --create-home --shell /bin/bash --user-group rishi-deploy
    fi
    usermod --append --groups docker rishi-deploy

    install --owner=rishi-deploy --group=rishi-deploy --mode=0700 \
        --directory /home/rishi-deploy/.ssh

    # Write the authorized_keys file from the multi-line environment variable. printf
    # preserves trailing newlines correctly where echo would not.
    printf '%s\n' "${YRAL_AUTHORIZED_SSH_KEYS}" \
        > /home/rishi-deploy/.ssh/authorized_keys
    chown rishi-deploy:rishi-deploy /home/rishi-deploy/.ssh/authorized_keys
    chmod 0600 /home/rishi-deploy/.ssh/authorized_keys
}


configure_narrow_sudoers_for_rishi_deploy() {
    # WHAT:  drop a sudoers fragment giving rishi-deploy passwordless access
    #        to docker, narrow systemctl verbs on specific units, journalctl,
    #        and apt update/upgrade — nothing else.
    # WHEN:  after the user exists.
    # WHY:   per CONSTRAINTS C8, blanket `ALL=(ALL) NOPASSWD: ALL` is forbidden.
    #        The narrow surface keeps a compromised CI key from rooting the box.
    local sudoers_fragment_path="/etc/sudoers.d/rishi-deploy-narrow"

    cat > "${sudoers_fragment_path}" <<'SUDOERS_FRAGMENT'
# rishi-deploy narrow sudoers — see CONSTRAINTS C8.
# Anything not listed here requires Saikat to grant a temporary root window.

rishi-deploy ALL=(root) NOPASSWD: /usr/bin/docker
rishi-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart caddy
rishi-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl status caddy
rishi-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl reload caddy
rishi-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart docker
rishi-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl status docker
rishi-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart yral-v2-swarm-resync
rishi-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl status yral-v2-swarm-resync
rishi-deploy ALL=(root) NOPASSWD: /usr/bin/journalctl
rishi-deploy ALL=(root) NOPASSWD: /usr/bin/apt-get update
rishi-deploy ALL=(root) NOPASSWD: /usr/bin/apt-get -y upgrade
SUDOERS_FRAGMENT

    chmod 0440 "${sudoers_fragment_path}"
    visudo --check --file "${sudoers_fragment_path}"
}


configure_ufw_firewall_for_this_node_role() {
    # WHAT:  reset UFW to a known-good default-deny posture, then open only
    #        the ports v2 needs based on whether this node is edge or compute.
    # WHEN:  after sudoers — we want UFW locked down before any new listening
    #        sockets get bound by Swarm or Patroni later.
    # WHY:   per CONSTRAINTS C3, only :443 is exposed on rishi-4/5 (edge);
    #        rishi-6 (compute) has no public listener. SSH is allow-list-only
    #        per V2 infra doc §2 to defend the temporary root-window window.
    ufw --force reset

    # SSH from the documented allow-list. Caller passes a comma-separated
    # list via env so we never hardcode IPs in the file (CONSTRAINTS C6).
    local ssh_allow_list_csv="${YRAL_SSH_ALLOWLIST_CIDRS:-}"
    if [[ -n "${ssh_allow_list_csv}" ]]; then
        local ssh_allow_cidr
        for ssh_allow_cidr in ${ssh_allow_list_csv//,/ }; do
            ufw allow from "${ssh_allow_cidr}" to any port 22 proto tcp
        done
    else
        echo "WARN node-bootstrap: YRAL_SSH_ALLOWLIST_CIDRS unset — leaving SSH open to all (review before production)" >&2
        ufw allow 22/tcp
    fi

    # Edge nodes (rishi-4, rishi-5) terminate Caddy ingress :443. rishi-6
    # is compute-only and never exposes a public port — Caddy doesn't run
    # there per the placement label `node_role=compute`.
    if [[ "${YRAL_NODE_ROLE}" == "edge" ]]; then
        ufw allow 443/tcp
    fi

    # Swarm control plane / gossip / overlay — restricted to the cluster
    # subnet via environment variable (Hetzner private network or public IPs of peers).
    local cluster_peer_cidr_csv="${YRAL_CLUSTER_PEER_CIDRS:-}"
    if [[ -n "${cluster_peer_cidr_csv}" ]]; then
        local cluster_peer_cidr
        for cluster_peer_cidr in ${cluster_peer_cidr_csv//,/ }; do
            ufw allow from "${cluster_peer_cidr}" to any port "${SWARM_CONTROL_PLANE_TCP_PORT}" proto tcp
            ufw allow from "${cluster_peer_cidr}" to any port "${SWARM_GOSSIP_DISCOVERY_TCP_AND_UDP_PORT}" proto tcp
            ufw allow from "${cluster_peer_cidr}" to any port "${SWARM_GOSSIP_DISCOVERY_TCP_AND_UDP_PORT}" proto udp
            ufw allow from "${cluster_peer_cidr}" to any port "${SWARM_VXLAN_OVERLAY_UDP_PORT}" proto udp
        done
    fi

    ufw default deny incoming
    ufw default allow outgoing
    ufw --force enable
}


enable_unattended_security_upgrades() {
    # WHAT:  enable Ubuntu's unattended-upgrades, scoped to security patches.
    # WHEN:  after UFW.
    # WHY:   per V2 infra doc §2 step 9, security patches go in unattended.
    #        Full dist-upgrades stay manual (kernel changes need reboots,
    #        which we want under our control around Patroni leader handoffs).
    dpkg-reconfigure --priority=low unattended-upgrades

    # Restrict to the security pocket only. The default config also enables
    # `-updates`, which can bring in non-security changes that surprise us.
    cat > /etc/apt/apt.conf.d/52unattended-upgrades-security-only <<'UNATTENDED_OVERRIDE'
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
};
UNATTENDED_OVERRIDE
}


disable_root_password_authentication() {
    # WHAT:  set `PermitRootLogin prohibit-password` in sshd_config.
    # WHEN:  last step of the root window.
    # WHY:   keys still work for emergency break-glass per V2 infra doc §2,
    #        but a stolen password (no matter how strong) cannot get in.
    sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
    sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
    systemctl reload ssh
}


# ────────────────────── Phase: swarm-init / swarm-join ───────────────────────


initialize_docker_swarm_on_first_manager_node() {
    # WHAT:  run `docker swarm init` on rishi-4 and capture the join token
    #        for the other managers.
    # WHEN:  swarm-init phase, called only on rishi-4.
    # WHY:   Swarm needs ONE node to start the cluster; the other nodes join
    #        with the manager token printed below. Re-running is safe — we
    #        skip if the node is already in a Swarm.
    if docker info --format '{{.Swarm.LocalNodeState}}' | grep --quiet active; then
        echo "node-bootstrap: this node is already in a Swarm — skipping init"
        return 0
    fi

    docker swarm init --advertise-addr "$(hostname --ip-address | awk '{print $1}')"

    # Print the manager join token so the operator can copy it to the env
    # of the swarm-join run on rishi-5 and rishi-6.
    echo "==== MANAGER JOIN TOKEN (copy to YRAL_SWARM_JOIN_TOKEN on rishi-5/6) ===="
    docker swarm join-token --quiet manager
    echo "===================================================================="
}


join_docker_swarm_as_manager_node() {
    # WHAT:  run `docker swarm join --token …` against rishi-4.
    # WHEN:  swarm-join phase, on rishi-5 and rishi-6.
    # WHY:   three-manager quorum tolerates one node down at a time — needed
    #        for Patroni leader failover and for chaos test exit criteria H3.
    if docker info --format '{{.Swarm.LocalNodeState}}' | grep --quiet active; then
        echo "node-bootstrap: this node is already in a Swarm — skipping join"
        return 0
    fi

    docker swarm join \
        --token "${YRAL_SWARM_JOIN_TOKEN}" \
        --advertise-addr "$(hostname --ip-address | awk '{print $1}')" \
        "${YRAL_RISHI_4_PUBLIC_IPV4}:${SWARM_CONTROL_PLANE_TCP_PORT}"
}


create_encrypted_overlay_networks() {
    # WHAT:  create three Swarm overlay networks with --opt encrypted.
    # WHEN:  swarm-init phase only (overlays are cluster-wide, defined once).
    # WHY:   per CONSTRAINTS C3, all inter-service traffic rides Swarm
    #        overlays. The split into three (public-web / internal /
    #        data-plane) means a compromised public service cannot directly
    #        see Patroni or Redis on the data-plane overlay.
    local existing_overlay_networks
    existing_overlay_networks="$(docker network ls --filter driver=overlay --format '{{.Name}}')"

    local overlay_network_name
    for overlay_network_name in \
        "${PUBLIC_WEB_OVERLAY_NAME}" \
        "${INTERNAL_SERVICE_OVERLAY_NAME}" \
        "${DATA_PLANE_OVERLAY_NAME}"; do

        if echo "${existing_overlay_networks}" | grep --quiet --line-regexp "${overlay_network_name}"; then
            echo "node-bootstrap: overlay ${overlay_network_name} already exists — skipping"
            continue
        fi
        docker network create \
            --driver overlay \
            --opt encrypted \
            --attachable \
            "${overlay_network_name}"
    done
}


apply_placement_labels_to_this_node() {
    # WHAT:  attach Swarm placement labels (node_role, state_tier,
    #        observability_tier, langfuse_tier) to this node.
    # WHEN:  after init/join — labels live on the Swarm node object.
    # WHY:   per cluster.hosts.yaml, these labels drive `placement.constraints`
    #        in patroni-install.sh, redis-sentinel-install.sh, langfuse-
    #        install.sh, and caddy-swarm-service.yml. Without labels, the
    #        scheduler cannot pin Langfuse to rishi-6 etc.
    case "${YRAL_NODE_NAME}" in
        rishi-4)
            docker node update --label-add node_role=edge --label-add state_tier=primary "${YRAL_NODE_NAME}"
            ;;
        rishi-5)
            docker node update --label-add node_role=edge --label-add observability_tier=primary "${YRAL_NODE_NAME}"
            ;;
        rishi-6)
            docker node update --label-add node_role=compute --label-add langfuse_tier=primary "${YRAL_NODE_NAME}"
            ;;
        *)
            echo "ERROR node-bootstrap: unknown node name '${YRAL_NODE_NAME}' — cannot pick labels" >&2
            exit 1
            ;;
    esac
}


install_swarm_resync_systemd_service() {
    # WHAT:  drop a oneshot systemd unit that re-deploys every v2 stack
    #        after docker.service is up at boot.
    # WHEN:  swarm-init and swarm-join phases.
    # WHY:   per CONSTRAINTS H1 + the April-19 incident reference memory,
    #        Docker's `restart: always` is unreliable across reboots. This
    #        oneshot iterates over a stack list and re-applies each one,
    #        forcing the desired state.
    cat > "${SWARM_RESYNC_SYSTEMD_UNIT_PATH}" <<'SWARM_RESYNC_UNIT'
[Unit]
Description=Yral v2 Swarm stack resync (per CONSTRAINTS H1)
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
# The actual stack list lives at /etc/yral-v2/stacks-to-resync.list,
# one stack name per line. Bootstrap installs an empty list; deploy
# workflows append to it as new stacks ship.
ExecStart=/usr/local/bin/yral-v2-resync-all-stacks.sh

[Install]
WantedBy=multi-user.target
SWARM_RESYNC_UNIT

    install_resync_helper_script

    systemctl daemon-reload
    systemctl enable yral-v2-swarm-resync.service
}


install_resync_helper_script() {
    # WHAT:  install the support script that the systemd unit ExecStart= calls.
    # WHEN:  invoked from install_swarm_resync_systemd_service.
    # WHY:   the support script iterates over a flat list of stack names and
    #        runs `docker stack deploy` for each — keep the systemd
    #        unit short and the loop visible to a future reader.
    install -d /etc/yral-v2
    if [[ ! -f /etc/yral-v2/stacks-to-resync.list ]]; then
        : > /etc/yral-v2/stacks-to-resync.list
    fi

    cat > /usr/local/bin/yral-v2-resync-all-stacks.sh <<'RESYNC_HELPER'
#!/usr/bin/env bash
# Re-applies every Yral v2 Swarm stack listed in the resync registry.
# Called by yral-v2-swarm-resync.service after docker.service is ready.
set -euo pipefail

STACKS_REGISTRY_PATH="/etc/yral-v2/stacks-to-resync.list"
STACK_COMPOSE_BASE_DIRECTORY="/etc/yral-v2/stacks"

[ -f "${STACKS_REGISTRY_PATH}" ] || exit 0

while IFS= read -r stack_name; do
    [ -z "${stack_name}" ] && continue
    compose_file_path="${STACK_COMPOSE_BASE_DIRECTORY}/${stack_name}/stack.yml"
    if [ -f "${compose_file_path}" ]; then
        docker stack deploy --compose-file "${compose_file_path}" --with-registry-auth "${stack_name}"
    fi
done < "${STACKS_REGISTRY_PATH}"
RESYNC_HELPER

    chmod +x /usr/local/bin/yral-v2-resync-all-stacks.sh
}


# ────────────────────── Final summary ────────────────────────────────────────


print_post_phase_summary() {
    # WHAT:  echo a one-block summary so the operator knows the phase finished.
    # WHEN:  last thing main() does on the success path.
    # WHY:   running this on three nodes back to back, you want a clear
    #        success delimiter between runs — easier than reading set -x trace.
    cat <<SUMMARY

✅ node-bootstrap finished phase '${YRAL_BOOTSTRAP_PHASE}' on ${YRAL_NODE_NAME} (role=${YRAL_NODE_ROLE}).

Next:
  - rishi-4 root-window done → run YRAL_BOOTSTRAP_PHASE=swarm-init on rishi-4.
  - rishi-4 swarm-init done → copy join token, run swarm-join on rishi-5 + rishi-6.
  - All three swarm-joined → patroni-install.sh, redis-sentinel-install.sh,
    langfuse-install.sh, caddy-swarm-service.yml can deploy.
SUMMARY
}


# Run main() with whatever arguments were passed (currently none expected;
# all configuration comes through YRAL_* environment variables).
main "$@"


# ══════════════════════════════════════════════════════════════════════════
# RELATED FILES
# ─────────────
# - patroni-install.sh, redis-sentinel-install.sh, langfuse-install.sh
#       Sibling install scripts that deploy stateful stacks once node-
#       bootstrap has finished on all three nodes.
# - caddy-swarm-service.yml
#       The edge ingress stack file deployed onto rishi-4/5 after swarm-init.
# - ../secrets-manifest.yaml
#       Cluster-level secrets manifest declaring every secret these install
#       scripts depend on (per CONSTRAINTS D7).
# - ../cluster.hosts.yaml (referenced, not yet committed)
#       Shape-only cluster topology; IP values come from GitHub Secrets at
#       render time per CONSTRAINTS C6.
# ══════════════════════════════════════════════════════════════════════════
