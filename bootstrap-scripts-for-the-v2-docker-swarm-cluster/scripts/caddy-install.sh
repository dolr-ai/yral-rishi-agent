#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  caddy-install.sh                                                            ║
# ║                                                                              ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                                ║
# ║  Deploy the cluster-side Caddy edge-ingress stack on rishi-4/5 per          ║
# ║  CONSTRAINTS C10 by hashing caddyfile.placeholder, creating it as a         ║
# ║  SHA-suffixed Swarm config, and `docker stack deploy`-ing                   ║
# ║  caddy-swarm-service.yml.                                                   ║
# ║                                                                              ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                           ║
# ║  Caddy is a small reverse-proxy server. We run 2 replicas (one on rishi-4, ║
# ║  one on rishi-5 — the nodes labelled `node_role=edge`) that hold the only ║
# ║  v2 host port: 443. In Phase 0 they have no upstream traffic source        ║
# ║  (rishi-1/2/3 edge Caddy is deferred per A2), so they serve a placeholder ║
# ║  response — the value of this deploy is validating the stack shape +      ║
# ║  having the listener ready when rishi-1/2/3 land. Sessions 3+4 add real    ║
# ║  per-service routes by editing caddyfile.placeholder (or eventually        ║
# ║  generate-caddy-swarm-config.sh).                                          ║
# ║                                                                              ║
# ║  🔗 HOW IT FITS                                                              ║
# ║  - Runs on:   rishi-4 (manager) AFTER node-bootstrap.sh swarm-init has    ║
# ║               created the yral-v2-public-web overlay.                      ║
# ║  - Reads:     caddyfile.placeholder (sibling file in this dir).            ║
# ║  - Deploys:   caddy-swarm-service.yml on yral-v2-public-web.               ║
# ║  - Followed by: Day-7 rishi-1/2/3 edge Caddy snippet PR (proxies into      ║
# ║               this stack's :443 over `https://rishi-4:443                  ║
# ║               https://rishi-5:443`).                                       ║
# ║                                                                              ║
# ║  📥 INPUTS (environment variables)                                           ║
# ║  - YRAL_RISHI_4_PUBLIC_IPV4                rishi-4's IPv4 (resync verifier)║
# ║  - YRAL_RISHI_5_PUBLIC_IPV4                rishi-5's IPv4                  ║
# ║  - YRAL_RISHI_6_PUBLIC_IPV4                rishi-6's IPv4                  ║
# ║                                                                              ║
# ║  🛠️ ONE-TIME OPERATOR SETUP (run AS ROOT, while root SSH window is open)    ║
# ║  Narrow sudoers per CONSTRAINTS C8 doesn't grant rishi-deploy             ║
# ║  `sudo tee --append`, so the resync-registry append is a one-time         ║
# ║  operator action documented in patroni-install.sh's header. Caddy adds   ║
# ║  one more stack name to that list, on EACH cluster node:                  ║
# ║                                                                              ║
# ║    ssh root@<rishi-4 ip> 'grep -q -x "yral-v2-edge-caddy" /etc/yral-v2/stacks-to-resync.list \\
# ║      || echo "yral-v2-edge-caddy" >> /etc/yral-v2/stacks-to-resync.list'  ║
# ║    ssh root@<rishi-5 ip> '<same as rishi-4>'                              ║
# ║    ssh root@<rishi-6 ip> '<same as rishi-4>'                              ║
# ║                                                                              ║
# ║  📤 OUTPUTS / SIDE EFFECTS                                                   ║
# ║  - Swarm config `yral_v2_edge_caddyfile_<sha8>` (created if missing).      ║
# ║  - Stack `yral-v2-edge-caddy` deployed: 2 replicas of caddy-edge-ingress   ║
# ║    on edge-labeled nodes, attached to yral-v2-public-web, publishing :443. ║
# ║  - Old `yral_v2_edge_caddyfile_<old-sha>` configs remain — Swarm refuses   ║
# ║    to delete configs still in use, and rotation cleanup is a future       ║
# ║    cleanup task (queued).                                                  ║
# ║                                                                              ║
# ║  ⭐ START HERE                                                               ║
# ║  Read main() first; each helper function carries a WHAT/WHEN/WHY role     ║
# ║  comment.                                                                  ║
# ║                                                                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝

set -euo pipefail


# ────────────────────── Module-level constants ───────────────────────────────


readonly CADDY_STACK_NAME="yral-v2-edge-caddy"
readonly CADDY_SWARM_CONFIG_BASE_NAME="yral_v2_edge_caddyfile"
readonly CLUSTER_NODE_NAMES=(rishi-4 rishi-5 rishi-6)
readonly SWARM_STACK_RESYNC_REGISTRY_PATH="/etc/yral-v2/stacks-to-resync.list"

THIS_SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly CADDY_STACK_COMPOSE_FILE_PATH="${THIS_SCRIPT_DIRECTORY}/caddy-swarm-service.yml"
readonly CADDYFILE_CONTENT_FILE_PATH="${THIS_SCRIPT_DIRECTORY}/caddyfile.placeholder"

# Post-deploy verifier window — matches the pattern PR #51 + #55 + #60
# established for the other install scripts.
readonly CADDY_DEPLOY_VERIFY_TIMEOUT_SECONDS=30
readonly CADDY_DEPLOY_VERIFY_POLL_SECONDS=5


main() {
    confirm_running_in_swarm_manager_context
    confirm_required_environment_variables_present
    confirm_required_overlays_exist

    create_or_rotate_caddyfile_swarm_config
    render_caddy_stack_compose_file_to_temporary_path
    deploy_caddy_stack_into_swarm
    confirm_stack_actually_deployed
    confirm_stack_registered_with_swarm_resync_service
    print_post_install_summary
}


# WHAT: return the public IPv4 for ${1} (a node name like 'rishi-4').
# WHEN: called by the resync-registry verifier when ssh-ing to cluster nodes.
# WHY:  the script can run from a manager node without SSH config aliases
#       for the short hostnames. Same pattern as the other install scripts.
get_public_ipv4_for_node() {
    local node_name="${1}"
    local env_var_name
    env_var_name="YRAL_$(echo "${node_name}" | tr '[:lower:]-' '[:upper:]_')_PUBLIC_IPV4"
    if [[ -z "${!env_var_name:-}" ]]; then
        echo "ERROR caddy-install: ${env_var_name} is unset (needed to SSH to ${node_name})" >&2
        exit 1
    fi
    echo "${!env_var_name}"
}


confirm_running_in_swarm_manager_context() {
    # WHAT:  refuse to run unless this node is an active Swarm manager.
    # WHEN:  first pre-flight.
    # WHY:   `docker stack deploy` against a non-manager errors mid-way with
    #        a confusing "This node is not a swarm manager" message. Failing
    #        up front is clearer.
    local swarm_local_node_state
    swarm_local_node_state="$(docker info --format '{{.Swarm.LocalNodeState}}')"
    if [[ "${swarm_local_node_state}" != "active" ]]; then
        echo "ERROR caddy-install: not in active Swarm" >&2; exit 1
    fi
    if ! docker info --format '{{.Swarm.ControlAvailable}}' | grep --quiet true; then
        echo "ERROR caddy-install: not on a Swarm manager" >&2; exit 1
    fi
}


confirm_required_environment_variables_present() {
    # WHAT:  fail fast if any required env var is unset.
    # WHEN:  second pre-flight.
    # WHY:   the resync-registry verifier ssh-hops to each cluster node by IP;
    #        missing an IP means a mid-way failure with a confusing error.
    local required_environment_variables=(
        YRAL_RISHI_4_PUBLIC_IPV4
        YRAL_RISHI_5_PUBLIC_IPV4
        YRAL_RISHI_6_PUBLIC_IPV4
    )
    local missing_count=0
    local environment_variable_name
    for environment_variable_name in "${required_environment_variables[@]}"; do
        if [[ -z "${!environment_variable_name:-}" ]]; then
            echo "ERROR caddy-install: required environment variable ${environment_variable_name} is unset" >&2
            missing_count=$((missing_count + 1))
        fi
    done
    if [[ "${missing_count}" -gt 0 ]]; then
        exit 1
    fi
}


confirm_required_overlays_exist() {
    # WHAT:  caddy-swarm-service.yml attaches to yral-v2-public-web only.
    # WHEN:  third pre-flight.
    # WHY:   missing overlay = deploy fails with `network yral-v2-public-web
    #        is declared as external, but could not be found` mid-way.
    if ! docker network ls --format '{{.Name}}' | grep --quiet --line-regexp 'yral-v2-public-web'; then
        echo "ERROR caddy-install: overlay yral-v2-public-web missing — run node-bootstrap.sh swarm-init first" >&2
        exit 1
    fi
}


create_or_rotate_caddyfile_swarm_config() {
    # WHAT:  hash caddyfile.placeholder content + create a SHA-suffixed
    #        Swarm config object holding that content per CONSTRAINTS H2.
    # WHEN:  after pre-flight, before deploy.
    # WHY:   content rotation = new SHA = new config name = next stack deploy
    #        rolls the service. Skipping when the SHA-named config already
    #        exists makes the script idempotent on re-runs. Same export-in-
    #        both-branches invariant as langfuse-install.sh's secret rotation
    #        (PR #45 root cause), kept here so envsubst always sees a value.
    if [[ ! -f "${CADDYFILE_CONTENT_FILE_PATH}" ]]; then
        echo "ERROR caddy-install: Caddyfile content file not found at ${CADDYFILE_CONTENT_FILE_PATH}" >&2
        exit 1
    fi

    local content_sha8
    content_sha8="$(sha256sum "${CADDYFILE_CONTENT_FILE_PATH}" | cut --characters=1-8)"
    local fully_qualified_config_name="${CADDY_SWARM_CONFIG_BASE_NAME}_${content_sha8}"

    # Exposed via env so render_caddy_stack_compose_file_to_temporary_path
    # (and envsubst inside it) sees the resolved name. The export must run
    # regardless of whether docker config create runs or skips — otherwise
    # envsubst writes an empty config name into the rendered stack file
    # and `docker stack deploy` fails with a confusing error.
    export YRAL_EDGE_CADDY_STACK_RESOLVED_YRAL_V2_EDGE_CADDYFILE="${fully_qualified_config_name}"

    if docker config inspect "${fully_qualified_config_name}" >/dev/null 2>&1; then
        echo "caddy-install: config ${fully_qualified_config_name} already exists — skipping"
        return 0
    fi

    docker config create "${fully_qualified_config_name}" "${CADDYFILE_CONTENT_FILE_PATH}"
}


render_caddy_stack_compose_file_to_temporary_path() {
    # WHAT:  envsubst the stack file with the resolved Swarm-config name +
    #        write the rendered version under /tmp.
    # WHEN:  after config creation, before deploy.
    # WHY:   the stack file uses ${YRAL_EDGE_CADDY_STACK_RESOLVED_...} as a
    #        placeholder so the SHA-rotating name lands cleanly. Explicit
    #        envsubst WHITELIST so only this one placeholder substitutes —
    #        every other `$VAR` token in the stack passes through untouched.
    #        Same pattern PR #57 introduced for redis-sentinel-install.sh
    #        after the bare-envsubst trap.
    if [[ ! -f "${CADDY_STACK_COMPOSE_FILE_PATH}" ]]; then
        echo "ERROR caddy-install: stack file not found at ${CADDY_STACK_COMPOSE_FILE_PATH}" >&2
        exit 1
    fi
    CADDY_RENDERED_STACK_COMPOSE_FILE_PATH="$(mktemp /tmp/yral-v2-edge-caddy-rendered-stack.XXXXXX.yml)"
    envsubst '${YRAL_EDGE_CADDY_STACK_RESOLVED_YRAL_V2_EDGE_CADDYFILE}' \
        < "${CADDY_STACK_COMPOSE_FILE_PATH}" \
        > "${CADDY_RENDERED_STACK_COMPOSE_FILE_PATH}"
    export CADDY_RENDERED_STACK_COMPOSE_FILE_PATH
}


deploy_caddy_stack_into_swarm() {
    # WHAT:  `docker stack deploy` then remove the rendered temp file.
    # WHEN:  after render.
    # WHY:   --with-registry-auth so worker nodes can pull caddy:2.8.4-alpine
    #        from Docker Hub. The rendered file contains no secret values
    #        (just a config-name reference), but cleaning up keeps /tmp tidy.
    docker stack deploy \
        --compose-file "${CADDY_RENDERED_STACK_COMPOSE_FILE_PATH}" \
        --with-registry-auth \
        --prune \
        "${CADDY_STACK_NAME}"
    rm -f "${CADDY_RENDERED_STACK_COMPOSE_FILE_PATH}"
}


confirm_stack_actually_deployed() {
    # WHAT:  two-layer post-deploy verifier ported verbatim from langfuse-
    #        install.sh's PR #51/#55/#60 implementation.
    #        Layer 1 (loop, ${CADDY_DEPLOY_VERIFY_TIMEOUT_SECONDS}s total at
    #          ${CADDY_DEPLOY_VERIFY_POLL_SECONDS}s ticks):
    #          fail loud if ANY task whose desired-state is `running` is
    #          currently in `Rejected` or `Failed` state.
    #        Layer 2 (single check at end of the window): fail loud if
    #          ANY service has 0 running replicas AND zero tasks in
    #          `docker stack ps` (the unsatisfiable-placement case).
    # WHEN:  immediately after deploy.
    # WHY:   `docker stack deploy` exits 0 as soon as the spec is in Swarm's
    #        raft store — it does NOT wait for tasks to schedule or images
    #        to pull. Caddy starts fast (cold-pull ~5-10s, boot <1s) but the
    #        verifier handles slower windows too.
    local deadline_epoch=$(($(date +%s) + CADDY_DEPLOY_VERIFY_TIMEOUT_SECONDS))
    local rejected_or_failed_tasks=""
    while [[ "$(date +%s)" -lt "${deadline_epoch}" ]]; do
        rejected_or_failed_tasks="$(
            docker stack ps "${CADDY_STACK_NAME}" \
                --filter desired-state=running \
                --format '{{.Name}}	{{.CurrentState}}	{{.Error}}' \
                2>/dev/null \
                | awk -F'	' '$2 ~ /^(Rejected|Failed)/ {print}'
        )"
        if [[ -n "${rejected_or_failed_tasks}" ]]; then
            echo "ERROR caddy-install: ${CADDY_STACK_NAME} has Rejected/Failed tasks (docker stack deploy returned 0 anyway):" >&2
            echo "${rejected_or_failed_tasks}" >&2
            echo "" >&2
            echo "Full stack ps for debugging:" >&2
            docker stack ps "${CADDY_STACK_NAME}" --no-trunc >&2 || true
            exit 1
        fi
        sleep "${CADDY_DEPLOY_VERIFY_POLL_SECONDS}"
    done

    local zero_replica_services
    zero_replica_services="$(
        docker stack services "${CADDY_STACK_NAME}" \
            --format '{{.Name}}	{{.Replicas}}' \
            2>/dev/null \
            | awk -F'	' '$2 ~ /^0\// {print}'
    )"
    if [[ -n "${zero_replica_services}" ]]; then
        local service_name first_field placed_count=0
        while IFS=$'\t' read -r service_name first_field; do
            placed_count="$(
                docker stack ps "${CADDY_STACK_NAME}" \
                    --filter "name=${service_name}" \
                    --format '{{.ID}}' 2>/dev/null \
                    | wc -l | tr -d ' '
            )"
            if [[ "${placed_count}" == "0" ]]; then
                echo "ERROR caddy-install: service ${service_name} has 0 replicas and Swarm never created any task — likely an unsatisfiable placement constraint (e.g., no edge-labelled nodes)." >&2
                echo "" >&2
                echo "Full stack services for debugging:" >&2
                docker stack services "${CADDY_STACK_NAME}" >&2 || true
                exit 1
            fi
        done <<< "${zero_replica_services}"
    fi

    echo "caddy-install: post-deploy verifier — no Rejected/Failed tasks and every service has at least one task placed, after ${CADDY_DEPLOY_VERIFY_TIMEOUT_SECONDS}s"
}


confirm_stack_registered_with_swarm_resync_service() {
    # WHAT:  verify ${CADDY_STACK_NAME} appears in
    #        ${SWARM_STACK_RESYNC_REGISTRY_PATH} on every cluster node.
    # WHEN:  after deploy, last pre-completion check.
    # WHY:   per CONSTRAINTS H1, the boot-time resync service iterates this
    #        list and re-deploys each stack on reboot. The intra-cluster
    #        SSH provisioned by PR #75 lets this verifier ssh-hop natively;
    #        the YRAL_LANGFUSE_SKIP_PREFLIGHT_BIND_MOUNT_VERIFY-style bypass
    #        is NOT needed here. Append is a one-time root operator action
    #        documented in this file's header.
    local cluster_node_name
    for cluster_node_name in "${CLUSTER_NODE_NAMES[@]}"; do
        local cluster_node_ipv4
        cluster_node_ipv4="$(get_public_ipv4_for_node "${cluster_node_name}")"
        if ! ssh -o BatchMode=yes "rishi-deploy@${cluster_node_ipv4}" \
            "grep --quiet --line-regexp ${CADDY_STACK_NAME} ${SWARM_STACK_RESYNC_REGISTRY_PATH}" 2>/dev/null; then
            echo "ERROR caddy-install: ${CADDY_STACK_NAME} not registered in ${SWARM_STACK_RESYNC_REGISTRY_PATH} on ${cluster_node_name}" >&2
            echo "  Operator one-time setup (run AS ROOT, while root SSH window is open):" >&2
            echo "    ssh root@${cluster_node_ipv4} 'grep -q -x \"${CADDY_STACK_NAME}\" ${SWARM_STACK_RESYNC_REGISTRY_PATH} || echo \"${CADDY_STACK_NAME}\" >> ${SWARM_STACK_RESYNC_REGISTRY_PATH}'" >&2
            exit 1
        fi
    done
}


print_post_install_summary() {
    cat <<SUMMARY

✅ caddy-install finished — Caddy edge-ingress stack deployed as ${CADDY_STACK_NAME}.

Verify:
  docker stack ps ${CADDY_STACK_NAME}             # 2 replicas on rishi-4 + rishi-5 Running?
  curl --insecure https://<rishi-4-ip>/           # placeholder response (Phase 0)
  curl --insecure https://<rishi-5-ip>/           # placeholder response (Phase 0)

Next steps (NOT in this script):
  - Day 7: dolr-ai/yral-rishi-hetzner-infra-template adds rishi-1/2/3 Caddy
    snippet upstream-proxying to https://rishi-{4,5}:443 with Cloudflare in
    Full mode.
  - Sessions 3+4: register per-service routes by editing caddyfile.placeholder
    (or moving to generate-caddy-swarm-config.sh once that follow-up lands).
SUMMARY
}


# ────────────────────── Entry point ──────────────────────────────────────────


main "${@}"


# ══════════════════════════════════════════════════════════════════════════
# RELATED FILES
# ─────────────
# - caddy-swarm-service.yml
#       Compose stack this script deploys. Edited so `configs.<name>` and
#       `services.caddy-edge-ingress.configs[].source` both reference
#       ${YRAL_EDGE_CADDY_STACK_RESOLVED_YRAL_V2_EDGE_CADDYFILE} which
#       envsubst resolves to the SHA-suffixed config name.
# - caddyfile.placeholder
#       The Caddyfile body. Edit this + re-run the script → new sha8 →
#       Swarm rolls the service onto the new config.
# - node-bootstrap.sh
#       Creates yral-v2-public-web overlay this stack attaches to + labels
#       rishi-4 and rishi-5 with node_role=edge for the placement constraint.
# - patroni-install.sh / redis-sentinel-install.sh / langfuse-install.sh
#       Cousin install scripts; this one ports the post-deploy verifier +
#       resync-registry verifier + envsubst-whitelist patterns established
#       across PRs #51, #55, #57, #60.
# ══════════════════════════════════════════════════════════════════════════
