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
# ║  - Deploys:   redis-sentinel-stack.yml on yral-v2-data-plane.  ║
# ║  - Followed by: langfuse-install.sh; per-service apps connect via         ║
# ║               redis-sentinel:26379 (Sentinel discovery).                  ║
# ║                                                                              ║
# ║  📥 INPUTS (environment variables)                                           ║
# ║  - YRAL_REDIS_PRIMARY_PASSWORD   (from GitHub Secret REDIS_PRIMARY_PASSWORD)║
# ║  - YRAL_RISHI_4_PUBLIC_IPV4      (rishi-4's public IPv4; for SSH targeting)║
# ║  - YRAL_RISHI_5_PUBLIC_IPV4      (rishi-5's public IPv4; for SSH targeting)║
# ║  - YRAL_RISHI_6_PUBLIC_IPV4      (rishi-6's public IPv4; for SSH targeting)║
# ║                                                                              ║
# ║  🛠️ ONE-TIME OPERATOR SETUP (run AS ROOT, while root SSH window is open)    ║
# ║  Narrow sudoers per CONSTRAINTS C8 doesn't grant `sudo install -d` /       ║
# ║  `sudo tee --append` to rishi-deploy, so this script CANNOT create the    ║
# ║  bind-mount directory or append to the resync registry by itself. The     ║
# ║  canonical operator batch is documented in patroni-install.sh's header     ║
# ║  and covers all three stateful stacks (patroni + redis + langfuse) in     ║
# ║  one root-window pass. Excerpt — the redis-relevant lines:                ║
# ║                                                                              ║
# ║    for ip in <rishi-4 ip> <rishi-5 ip> <rishi-6 ip>; do                    ║
# ║      ssh root@$ip 'set -e                                                  ║
# ║        install -d --owner=999 --group=999 --mode=0700 /data/redis-data    ║
# ║        grep -q -x "yral-v2-redis" /etc/yral-v2/stacks-to-resync.list \\    ║
# ║          || echo "yral-v2-redis" >> /etc/yral-v2/stacks-to-resync.list     ║
# ║      '                                                                     ║
# ║    done                                                                    ║
# ║                                                                              ║
# ║  NOTE: uid 999 matches Redis's redis user in both the alpine and debian   ║
# ║  variants of the official `redis:7.2-*` image. /data/redis-data is        ║
# ║  created on all 3 nodes — rishi-4 + rishi-5 actually use it (primary +    ║
# ║  replica bind mounts), rishi-6 has it as a harmless extra so the batch    ║
# ║  is uniform across nodes.                                                  ║
# ║                                                                              ║
# ║  redis-sentinel-install.sh then verifies these prereqs and fails loud      ║
# ║  (with the exact remediation command) if missing.                          ║
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

# Every cluster node — quorum sentinel runs on all three. The bind-mount
# pre-flight only iterates the persistence subset (rishi-4 primary +
# rishi-5 replica); the resync-registry pre-flight iterates ALL nodes
# because the resync systemd unit runs cluster-wide.
CLUSTER_NODE_NAMES=(rishi-4 rishi-5 rishi-6)
REDIS_PERSISTENCE_NODE_NAMES=(rishi-4 rishi-5)

# Post-deploy verifier window. Same shape as patroni-install.sh's
# confirm_stack_actually_deployed (PR #51): `docker stack deploy` returns
# 0 as soon as the spec is in Docker Swarm's internal raft cluster store,
# NOT when tasks are actually running. If any task is Rejected (bad image,
# missing bind dir, missing constraint, etc.), Swarm keeps retrying it
# every few seconds while this script would have already printed
# "✅ redis-sentinel-install finished". Polling closes that gap.
REDIS_DEPLOY_VERIFY_TIMEOUT_SECONDS="${REDIS_DEPLOY_VERIFY_TIMEOUT_SECONDS:-30}"
REDIS_DEPLOY_VERIFY_POLL_SECONDS="${REDIS_DEPLOY_VERIFY_POLL_SECONDS:-5}"


main() {
    confirm_running_in_swarm_manager_context
    confirm_required_environment_variables_present
    confirm_data_plane_overlay_exists
    confirm_redis_bind_mount_directories_exist_on_persistence_nodes

    create_or_rotate_redis_password_swarm_secret
    render_redis_stack_compose_file_to_temporary_path
    deploy_redis_stack_into_swarm
    confirm_stack_actually_deployed
    confirm_stack_registered_with_swarm_resync_service
    print_post_install_summary
}


# WHAT: return the public IPv4 for ${1} (a node name like 'rishi-4').
# WHEN: called everywhere this script SSHes to a cluster node.
# WHY:  the script can run from the operator laptop, which has no SSH
#       config alias for the short hostnames. Yesterday's Day-5 Patroni
#       deploy surfaced this trap (PR #41 fixed it for patroni-install.sh);
#       same shape ported here.
get_public_ipv4_for_node() {
    local node_name="${1}"
    local env_var_name
    env_var_name="YRAL_$(echo "${node_name}" | tr '[:lower:]-' '[:upper:]_')_PUBLIC_IPV4"
    if [[ -z "${!env_var_name:-}" ]]; then
        echo "ERROR redis-sentinel-install: ${env_var_name} is unset (needed to SSH to ${node_name})" >&2
        exit 1
    fi
    echo "${!env_var_name}"
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


confirm_required_environment_variables_present() {
    # WHAT:  fail fast if any required env var is unset.
    # WHEN:  second pre-flight.
    # WHY:   without the password we cannot create the Swarm secret;
    #        without the public IPv4s we cannot SSH to verify bind dirs
    #        or resync-registry membership.
    local required_environment_variables=(
        YRAL_REDIS_PRIMARY_PASSWORD
        YRAL_RISHI_4_PUBLIC_IPV4
        YRAL_RISHI_5_PUBLIC_IPV4
        YRAL_RISHI_6_PUBLIC_IPV4
    )
    local required_environment_variable
    for required_environment_variable in "${required_environment_variables[@]}"; do
        if [[ -z "${!required_environment_variable:-}" ]]; then
            echo "ERROR redis-sentinel-install: ${required_environment_variable} is unset" >&2
            if [[ "${required_environment_variable}" == "YRAL_REDIS_PRIMARY_PASSWORD" ]]; then
                echo "  Set via: gh secret set REDIS_PRIMARY_PASSWORD" >&2
            fi
            exit 1
        fi
    done
}


confirm_data_plane_overlay_exists() {
    # WHAT:  check the encrypted data-plane overlay node-bootstrap.sh creates.
    # WHEN:  third pre-flight.
    # WHY:   stack file references it as external; missing = deploy fails.
    if ! docker network ls --format '{{.Name}}' | grep --quiet --line-regexp yral-v2-data-plane; then
        echo "ERROR redis-sentinel-install: yral-v2-data-plane missing — run node-bootstrap.sh first" >&2
        exit 1
    fi
}


confirm_redis_bind_mount_directories_exist_on_persistence_nodes() {
    # WHAT:  ssh to rishi-4 (primary) + rishi-5 (replica) as rishi-deploy
    #        and verify /data/redis-data exists with ownership 999:999.
    # WHEN:  fourth pre-flight.
    # WHY:   redis-sentinel-stack.yml bind-mounts /data/redis-data into the
    #        primary + replica containers per V2 infra doc §7.2 (bind mounts
    #        survive `docker system prune` where Docker volumes did not).
    #        Narrow sudoers per CONSTRAINTS C8 doesn't grant rishi-deploy
    #        permission to `sudo install -d` — so creating the directory
    #        is a one-time operator setup that runs AS ROOT (the canonical
    #        batch lives in patroni-install.sh's header and covers all
    #        three stateful stacks; see this file's header for the
    #        redis-relevant excerpt). This function only VERIFIES the
    #        prereq and fails loud with the exact remediation command if
    #        missing. Uid 999 matches Redis's user in the official
    #        redis:7.2-* image (both alpine and debian variants).
    local persistence_node_name
    for persistence_node_name in "${REDIS_PERSISTENCE_NODE_NAMES[@]}"; do
        local persistence_node_ipv4
        persistence_node_ipv4="$(get_public_ipv4_for_node "${persistence_node_name}")"
        local actual_ownership
        actual_ownership="$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
            "rishi-deploy@${persistence_node_ipv4}" \
            "test -d ${REDIS_BIND_MOUNT_HOST_PATH} && stat -c '%u:%g' ${REDIS_BIND_MOUNT_HOST_PATH}" 2>/dev/null || echo "missing")"
        if [[ "${actual_ownership}" != "999:999" ]]; then
            echo "ERROR redis-sentinel-install: ${REDIS_BIND_MOUNT_HOST_PATH} on ${persistence_node_name} is '${actual_ownership}', expected '999:999'" >&2
            echo "  Operator one-time setup (run AS ROOT, while root SSH window is open):" >&2
            echo "    ssh root@${persistence_node_ipv4} 'install -d --owner=999 --group=999 --mode=0700 ${REDIS_BIND_MOUNT_HOST_PATH}'" >&2
            echo "  If the directory exists with the wrong ownership, chown it instead:" >&2
            echo "    ssh root@${persistence_node_ipv4} 'chown -R 999:999 ${REDIS_BIND_MOUNT_HOST_PATH}'" >&2
            exit 1
        fi
    done
}


create_or_rotate_redis_password_swarm_secret() {
    # WHAT:  hash the password, create Swarm secret with SHA8 suffix per H2.
    # WHEN:  after pre-flight.
    # WHY:   content rotation (different password) creates a different secret
    #        name, so `docker stack deploy` triggers redeploy. Same password
    #        on re-run = same secret name = no churn. The resolved name
    #        export below MUST run in BOTH the create-new and skip-existing
    #        branches — otherwise envsubst writes an empty key into the
    #        rendered stack YAML on every re-run (this was patroni-install.sh
    #        bug fixed in PR #45; same trap applies here).
    local content_sha8
    content_sha8="$(printf '%s' "${YRAL_REDIS_PRIMARY_PASSWORD}" | sha256sum | cut --characters=1-8)"
    local fully_qualified_secret_name="yral_v2_redis_primary_password_${content_sha8}"

    export YRAL_REDIS_STACK_RESOLVED_REDIS_PRIMARY_PASSWORD="${fully_qualified_secret_name}"

    if docker secret inspect "${fully_qualified_secret_name}" >/dev/null 2>&1; then
        echo "redis-sentinel-install: secret ${fully_qualified_secret_name} already exists — skipping"
        return 0
    fi

    printf '%s' "${YRAL_REDIS_PRIMARY_PASSWORD}" \
        | docker secret create "${fully_qualified_secret_name}" -
}


render_redis_stack_compose_file_to_temporary_path() {
    # WHAT:  envsubst the stack file with the resolved secret name. We pass
    #        envsubst an EXPLICIT WHITELIST of placeholder variable names
    #        so it ONLY substitutes the ones we intend — every other `$VAR`
    #        token in the stack (e.g. `$REDIS_PASSWORD` inside container
    #        `command:` blocks) passes through untouched.
    # WHEN:  after secret creation.
    # WHY:   keeps the committed YAML free of SHA-suffixed names AND lets
    #        the stack contain `$VAR` tokens that the container shell
    #        should expand at runtime (which envsubst would otherwise
    #        eat). Day-5-Step-2 deploy attempt #2 hit this trap — the
    #        stack file's `$$REDIS_PASSWORD` got `$REDIS_PASSWORD`
    #        consumed by un-scoped envsubst, leaving stray `$` for
    #        Compose to choke on. The whitelist makes envsubst do exactly
    #        what `${YRAL_REDIS_STACK_RESOLVED_REDIS_PRIMARY_PASSWORD}`
    #        rendering needs and nothing else.
    if [[ ! -f "${REDIS_STACK_COMPOSE_FILE_PATH}" ]]; then
        echo "ERROR redis-sentinel-install: stack file not found" >&2
        exit 1
    fi
    REDIS_RENDERED_STACK_COMPOSE_FILE_PATH="$(mktemp /tmp/yral-v2-redis-rendered-stack.XXXXXX.yml)"
    envsubst '${YRAL_REDIS_STACK_RESOLVED_REDIS_PRIMARY_PASSWORD}' \
        < "${REDIS_STACK_COMPOSE_FILE_PATH}" \
        > "${REDIS_RENDERED_STACK_COMPOSE_FILE_PATH}"
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


confirm_stack_actually_deployed() {
    # WHAT:  two-layer post-deploy verifier (ported verbatim from
    #        patroni-install.sh's PR #51 implementation, renamed for redis).
    #        Layer 1 (loop, ${REDIS_DEPLOY_VERIFY_TIMEOUT_SECONDS}s total at
    #          ${REDIS_DEPLOY_VERIFY_POLL_SECONDS}s ticks): fail loud if ANY
    #          task whose desired-state is `running` is currently in
    #          `Rejected` or `Failed` state. Catches the Day-5 deploys-2+3
    #          bug class — tasks Swarm tries to start but can't because of
    #          bad image / bad mount / unsatisfiable constraint. Such tasks
    #          loop every ~5s so they always surface inside the 30s window.
    #        Layer 2 (single check at end of the window): fail loud if ANY
    #          service in the stack has 0 running replicas AND zero tasks
    #          in `docker stack ps` (the "placement matches no node" case).
    # WHEN:  immediately after deploy_redis_stack_into_swarm.
    # WHY:   `docker stack deploy` exits 0 as soon as the spec is in Docker
    #        Swarm's internal raft cluster store — it does NOT wait for
    #        tasks to actually schedule, pull images, or transition to
    #        Running. Day-5 Patroni deploys 2 + 3 surfaced this silent-
    #        success mode (bind-dir-missing + wrong pgbouncer image tag);
    #        we close it here for redis too.
    # WHAT WE DELIBERATELY DON'T DO:
    #        We do NOT wait for all replicas to reach Running. The redis-
    #        replica service's first cycle includes an RDB sync from the
    #        primary (typically fast but can stretch if the primary has
    #        meaningful state); sentinel containers may briefly stay in
    #        `Preparing` while image pulls finish. Those are legitimate
    #        in-flight states, not failures.
    local deadline_epoch=$(($(date +%s) + REDIS_DEPLOY_VERIFY_TIMEOUT_SECONDS))
    local rejected_or_failed_tasks=""
    while [[ "$(date +%s)" -lt "${deadline_epoch}" ]]; do
        rejected_or_failed_tasks="$(
            docker stack ps "${REDIS_STACK_NAME}" \
                --filter desired-state=running \
                --format '{{.Name}}	{{.CurrentState}}	{{.Error}}' \
                2>/dev/null \
                | awk -F'	' '$2 ~ /^(Rejected|Failed)/ {print}'
        )"
        if [[ -n "${rejected_or_failed_tasks}" ]]; then
            echo "ERROR redis-sentinel-install: ${REDIS_STACK_NAME} has Rejected/Failed tasks (docker stack deploy returned 0 anyway):" >&2
            echo "${rejected_or_failed_tasks}" >&2
            echo "" >&2
            echo "Full stack ps for debugging:" >&2
            docker stack ps "${REDIS_STACK_NAME}" --no-trunc >&2 || true
            exit 1
        fi
        sleep "${REDIS_DEPLOY_VERIFY_POLL_SECONDS}"
    done

    # Layer 2: confirm Swarm at least scheduled SOMETHING for every service.
    local zero_replica_services
    zero_replica_services="$(
        docker stack services "${REDIS_STACK_NAME}" \
            --format '{{.Name}}	{{.Replicas}}' \
            2>/dev/null \
            | awk -F'	' '$2 ~ /^0\// {print}'
    )"
    if [[ -n "${zero_replica_services}" ]]; then
        local service_name first_field placed_count=0
        while IFS=$'\t' read -r service_name first_field; do
            placed_count="$(
                docker stack ps "${REDIS_STACK_NAME}" \
                    --filter "name=${service_name}" \
                    --format '{{.ID}}' 2>/dev/null \
                    | wc -l | tr -d ' '
            )"
            if [[ "${placed_count}" == "0" ]]; then
                echo "ERROR redis-sentinel-install: service ${service_name} has 0 replicas and Swarm never created any task — likely an unsatisfiable placement constraint." >&2
                echo "" >&2
                echo "Full stack services for debugging:" >&2
                docker stack services "${REDIS_STACK_NAME}" >&2 || true
                exit 1
            fi
        done <<< "${zero_replica_services}"
    fi

    echo "redis-sentinel-install: post-deploy verifier — no Rejected/Failed tasks and every service has at least one task placed, after ${REDIS_DEPLOY_VERIFY_TIMEOUT_SECONDS}s"
}


confirm_stack_registered_with_swarm_resync_service() {
    # WHAT:  verify ${REDIS_STACK_NAME} appears in
    #        ${SWARM_STACK_RESYNC_REGISTRY_PATH} on every cluster node.
    # WHEN:  after deploy, last pre-completion check.
    # WHY:   per CONSTRAINTS H1, the boot-time resync service iterates this
    #        list and re-deploys each stack on reboot. Narrow sudoers per
    #        CONSTRAINTS C8 doesn't grant rishi-deploy `sudo tee --append`,
    #        so adding the line is a one-time operator setup that runs
    #        AS ROOT (canonical batch in patroni-install.sh's header).
    #        This function only VERIFIES the prereq and fails loud with
    #        the exact remediation command if missing.
    local cluster_node_name
    for cluster_node_name in "${CLUSTER_NODE_NAMES[@]}"; do
        local cluster_node_ipv4
        cluster_node_ipv4="$(get_public_ipv4_for_node "${cluster_node_name}")"
        if ! ssh -o BatchMode=yes "rishi-deploy@${cluster_node_ipv4}" \
            "grep --quiet --line-regexp ${REDIS_STACK_NAME} ${SWARM_STACK_RESYNC_REGISTRY_PATH}" 2>/dev/null; then
            echo "ERROR redis-sentinel-install: ${REDIS_STACK_NAME} not registered in ${SWARM_STACK_RESYNC_REGISTRY_PATH} on ${cluster_node_name}" >&2
            echo "  Operator one-time setup (run AS ROOT, while root SSH window is open):" >&2
            echo "    ssh root@${cluster_node_ipv4} 'grep -q -x \"${REDIS_STACK_NAME}\" ${SWARM_STACK_RESYNC_REGISTRY_PATH} || echo \"${REDIS_STACK_NAME}\" >> ${SWARM_STACK_RESYNC_REGISTRY_PATH}'" >&2
            exit 1
        fi
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
#                              patroni-install.sh is the canonical reference
#                              for the operator-setup batch + the verify-only
#                              pre-flight + post-deploy verifier patterns
#                              ported into this file.
# - ../secrets-manifest.yaml — declares REDIS_PRIMARY_PASSWORD.
# ══════════════════════════════════════════════════════════════════════════
