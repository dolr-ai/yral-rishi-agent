#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  redis-sentinel-install.sh                                                  ║
# ║                                                                              ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                                ║
# ║  Deploy the v2 cluster's Redis HA setup (primary on rishi-4, replica on    ║
# ║  rishi-5, three Sentinels for quorum) per CONSTRAINTS C11 by reading the   ║
# ║  shared password from GitHub Secrets, materialising it as a SHA-rotating   ║
# ║  Swarm secret, and `docker stack deploy`-ing redis-sentinel-stack.yml.     ║
# ║                                                                              ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                           ║
# ║  Redis Sentinel watches the Redis primary; if it dies, Sentinel promotes  ║
# ║  the replica. We pick Sentinel (NOT Cluster) per CONSTRAINTS C11 because   ║
# ║  v2's working set fits comfortably in 8-16 GB and we don't need sharding.║
# ║  All v2 services use `redis.sentinel.Sentinel` client to discover the     ║
# ║  current primary so failover is transparent to application code.          ║
# ║                                                                              ║
# ║  🔗 HOW IT FITS                                                              ║
# ║  - Runs on:   rishi-4 (manager) AFTER node-bootstrap.sh swarm-init/join.  ║
# ║  - Reads:     ../secrets-manifest.yaml + GitHub Secret REDIS_PRIMARY_     ║
# ║               PASSWORD (surfaced as YRAL_REDIS_PRIMARY_PASSWORD env var). ║
# ║  - Deploys:   redis-sentinel-stack.yml on yral-agent-data-plane-overlay.  ║
# ║  - Followed by: langfuse-install.sh; per-service apps connect via         ║
# ║               redis-sentinel:26379 (Sentinel discovery).                  ║
# ║                                                                              ║
# ║  📥 INPUTS (environment variables)                                           ║
# ║  - YRAL_REDIS_PRIMARY_PASSWORD   (from GitHub Secret REDIS_PRIMARY_PASSWORD)║
# ║                                                                              ║
# ║  📤 OUTPUTS / SIDE EFFECTS                                                   ║
# ║  - Redis primary running on rishi-4, replica on rishi-5.                  ║
# ║  - 3 Sentinels, one per node, quorum=2.                                   ║
# ║  - SHA-rotating Swarm secret per CONSTRAINTS H2.                          ║
# ║  - Bind-mounted /data/redis-data on rishi-4 + rishi-5.                    ║
# ║                                                                              ║
# ║  ⚠️ DRAFT — NO STACKS DEPLOYED YET (per agent spec + A13).                   ║
# ║                                                                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝

set -euo pipefail


REDIS_STACK_NAME="yral-v2-redis"
THIS_SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REDIS_STACK_COMPOSE_FILE_PATH="${THIS_SCRIPT_DIRECTORY}/redis-sentinel-stack.yml"
SWARM_STACK_RESYNC_REGISTRY_PATH="/etc/yral-v2/stacks-to-resync.list"
REDIS_BIND_MOUNT_HOST_PATH="/data/redis-data"


main() {
    confirm_running_in_swarm_manager_context
    confirm_redis_primary_password_environment_variable_present
    confirm_data_plane_overlay_exists

    create_redis_bind_mount_directories_on_persistence_nodes
    create_or_rotate_redis_password_swarm_secret
    render_redis_stack_compose_file_to_temporary_path
    deploy_redis_stack_into_swarm
    register_stack_with_swarm_resync_service
    print_post_install_summary
}


confirm_running_in_swarm_manager_context() {
    # WHAT:  refuse to continue if not on a Swarm manager.
    # WHEN:  first pre-flight.
    # WHY:   `docker stack deploy` only works on managers; explicit error
    #        beats a confusing failure later.
    local swarm_local_node_state
    swarm_local_node_state="$(docker info --format '{{.Swarm.LocalNodeState}}')"
    if [[ "${swarm_local_node_state}" != "active" ]]; then
        echo "ERROR redis-sentinel-install: this node is not in an active Swarm" >&2
        exit 1
    fi
    if ! docker info --format '{{.Swarm.ControlAvailable}}' | grep --quiet true; then
        echo "ERROR redis-sentinel-install: this node is not a Swarm manager" >&2
        exit 1
    fi
}


confirm_redis_primary_password_environment_variable_present() {
    # WHAT:  fail fast if YRAL_REDIS_PRIMARY_PASSWORD is unset.
    # WHEN:  second pre-flight.
    # WHY:   without it we cannot create the Swarm secret; deploy would loop.
    if [[ -z "${YRAL_REDIS_PRIMARY_PASSWORD:-}" ]]; then
        echo "ERROR redis-sentinel-install: YRAL_REDIS_PRIMARY_PASSWORD is unset" >&2
        echo "  Set via: gh secret set REDIS_PRIMARY_PASSWORD" >&2
        exit 1
    fi
}


confirm_data_plane_overlay_exists() {
    # WHAT:  check the encrypted data-plane overlay node-bootstrap.sh creates.
    # WHEN:  third pre-flight.
    # WHY:   stack file references it as external; missing = deploy fails.
    if ! docker network ls --format '{{.Name}}' | grep --quiet --line-regexp yral-agent-data-plane-overlay; then
        echo "ERROR redis-sentinel-install: yral-agent-data-plane-overlay missing — run node-bootstrap.sh first" >&2
        exit 1
    fi
}


create_redis_bind_mount_directories_on_persistence_nodes() {
    # WHAT:  mkdir /data/redis-data on rishi-4 (primary) and rishi-5 (replica).
    # WHEN:  before deploy.
    # WHY:   bind mount survives `docker system prune` (V2 infra doc §7.2);
    #        UID 999 matches Redis's internal uid in the official image.
    local persistence_node_hostname
    for persistence_node_hostname in rishi-4 rishi-5; do
        ssh -o StrictHostKeyChecking=accept-new "rishi-deploy@${persistence_node_hostname}" \
            "sudo install --owner=999 --group=999 --mode=0700 --directory ${REDIS_BIND_MOUNT_HOST_PATH}"
    done
}


create_or_rotate_redis_password_swarm_secret() {
    # WHAT:  hash the password, create Swarm secret with SHA8 suffix per H2.
    # WHEN:  after pre-flight.
    # WHY:   content rotation (different password) creates a different secret
    #        name, so `docker stack deploy` triggers redeploy. Same password
    #        on re-run = same secret name = no churn.
    local content_sha8
    content_sha8="$(printf '%s' "${YRAL_REDIS_PRIMARY_PASSWORD}" | sha256sum | cut --characters=1-8)"
    local fully_qualified_secret_name="yral_v2_redis_primary_password_${content_sha8}"

    if ! docker secret inspect "${fully_qualified_secret_name}" >/dev/null 2>&1; then
        printf '%s' "${YRAL_REDIS_PRIMARY_PASSWORD}" \
            | docker secret create "${fully_qualified_secret_name}" -
    fi

    export YRAL_REDIS_STACK_RESOLVED_REDIS_PRIMARY_PASSWORD="${fully_qualified_secret_name}"
}


render_redis_stack_compose_file_to_temporary_path() {
    # WHAT:  envsubst the stack file with the resolved secret name.
    # WHEN:  after secret creation.
    # WHY:   keeps the committed YAML free of SHA-suffixed names.
    if [[ ! -f "${REDIS_STACK_COMPOSE_FILE_PATH}" ]]; then
        echo "ERROR redis-sentinel-install: stack file not found" >&2
        exit 1
    fi
    REDIS_RENDERED_STACK_COMPOSE_FILE_PATH="$(mktemp /tmp/yral-v2-redis-rendered-stack.XXXXXX.yml)"
    envsubst < "${REDIS_STACK_COMPOSE_FILE_PATH}" > "${REDIS_RENDERED_STACK_COMPOSE_FILE_PATH}"
    export REDIS_RENDERED_STACK_COMPOSE_FILE_PATH
}


deploy_redis_stack_into_swarm() {
    # WHAT:  `docker stack deploy` against the rendered file.
    # WHEN:  after rendering.
    # WHY:   --with-registry-auth so worker nodes can pull the Redis image.
    docker stack deploy \
        --compose-file "${REDIS_RENDERED_STACK_COMPOSE_FILE_PATH}" \
        --with-registry-auth \
        --prune \
        "${REDIS_STACK_NAME}"
}


register_stack_with_swarm_resync_service() {
    # WHAT:  append stack name to /etc/yral-v2/stacks-to-resync.list.
    # WHEN:  after deploy.
    # WHY:   per H1, the boot-time resync iterates this list. Without
    #        registration, this stack does not survive a reboot.
    local cluster_node_hostname
    for cluster_node_hostname in rishi-4 rishi-5 rishi-6; do
        ssh "rishi-deploy@${cluster_node_hostname}" \
            "grep --quiet --line-regexp ${REDIS_STACK_NAME} ${SWARM_STACK_RESYNC_REGISTRY_PATH} \
                || echo ${REDIS_STACK_NAME} | sudo tee --append ${SWARM_STACK_RESYNC_REGISTRY_PATH} >/dev/null"
    done
}


print_post_install_summary() {
    cat <<SUMMARY

✅ redis-sentinel-install finished — Redis HA stack deployed as ${REDIS_STACK_NAME}.

Verify:
  docker stack ps ${REDIS_STACK_NAME}                 # all replicas Running?
  docker exec -it \$(docker ps -q -f name=${REDIS_STACK_NAME}_redis-sentinel-rishi-4) \\
      redis-cli -p 26379 SENTINEL primary yral-v2-redis-primary

App connection: discover current primary via Sentinel:
  redis-py:  Sentinel([("redis-sentinel:26379")], socket_timeout=0.5).master_for("yral-v2-redis-primary")

Next:
  ./langfuse-install.sh
SUMMARY
}


main "$@"


# ══════════════════════════════════════════════════════════════════════════
# RELATED FILES
# ─────────────
# - redis-sentinel-stack.yml — the Compose stack this script deploys.
# - node-bootstrap.sh        — must run first (creates data-plane overlay).
# - patroni-install.sh, langfuse-install.sh — siblings; same install pattern.
# - ../secrets-manifest.yaml — declares REDIS_PRIMARY_PASSWORD.
# ══════════════════════════════════════════════════════════════════════════
