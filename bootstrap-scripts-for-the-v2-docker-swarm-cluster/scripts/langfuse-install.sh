#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  langfuse-install.sh                                                        ║
# ║                                                                              ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                                ║
# ║  Deploy self-hosted Langfuse (web + worker + ClickHouse for traces) on    ║
# ║  rishi-6 per CONSTRAINTS D4 by reading secrets from GitHub Secrets,        ║
# ║  materialising them as SHA-rotating Swarm secrets, and `docker stack       ║
# ║  deploy`-ing langfuse-stack.yml.                                            ║
# ║                                                                              ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                           ║
# ║  Langfuse is the LLM-call tracing system every v2 service uses to record  ║
# ║  prompt + response + tokens + latency + cost per turn (CONSTRAINTS D4).   ║
# ║  Self-hosting it on rishi-6 (CONSTRAINTS § langfuse_tier=primary label)   ║
# ║  keeps every trace inside our cluster — no third-party data egress.       ║
# ║  Postgres lives on the shared Patroni cluster (schema `langfuse`);        ║
# ║  ClickHouse runs alongside Langfuse for high-cardinality trace events.   ║
# ║                                                                              ║
# ║  🔗 HOW IT FITS                                                              ║
# ║  - Runs on:   rishi-4 (manager) AFTER patroni-install.sh + redis-install.║
# ║  - Reads:     GitHub Secrets LANGFUSE_NEXTAUTH_SECRET +                   ║
# ║               LANGFUSE_ENCRYPTION_KEY + LANGFUSE_POSTGRES_PASSWORD +      ║
# ║               LANGFUSE_CLICKHOUSE_PASSWORD.                                ║
# ║  - Deploys:   langfuse-stack.yml on yral-v2-data-plane +      ║
# ║               yral-v2-internal (so v2      ║
# ║               services can POST traces).                                  ║
# ║  - Followed by: per-service apps post traces to                          ║
# ║               http://langfuse-web:3000 via the langfuse-python SDK.      ║
# ║                                                                              ║
# ║  📥 INPUTS (environment variables)                                           ║
# ║  - YRAL_LANGFUSE_NEXTAUTH_SECRET           (32+ char random)              ║
# ║  - YRAL_LANGFUSE_ENCRYPTION_KEY            (32-byte hex)                  ║
# ║  - YRAL_LANGFUSE_POSTGRES_PASSWORD         (the langfuse-schema role)     ║
# ║  - YRAL_LANGFUSE_CLICKHOUSE_PASSWORD       (random; auto-generate if not) ║
# ║  - YRAL_RISHI_4_PUBLIC_IPV4                (rishi-4's IPv4; SSH targeting)║
# ║  - YRAL_RISHI_5_PUBLIC_IPV4                (rishi-5's IPv4)               ║
# ║  - YRAL_RISHI_6_PUBLIC_IPV4                (rishi-6's IPv4; Langfuse host)║
# ║                                                                              ║
# ║  🛠️ ONE-TIME OPERATOR SETUP (run AS ROOT, while root SSH window is open)    ║
# ║  Narrow sudoers per CONSTRAINTS C8 doesn't grant `sudo install -d` /       ║
# ║  `sudo tee --append` to rishi-deploy, so this script CANNOT create the    ║
# ║  bind-mount directory or append to the resync registry itself. The        ║
# ║  canonical operator batch is documented in patroni-install.sh's header     ║
# ║  and covers patroni-data + redis-data; the Langfuse-specific addition     ║
# ║  is the ClickHouse bind dir (uid 101 NOT 999 — ClickHouse's official      ║
# ║  image runs as uid 101 = `clickhouse`, NOT the uid 999 the Patroni        ║
# ║  batch uses for `/data/langfuse-data`; that dir is now orphaned and       ║
# ║  unused — the stack file's bind source is `/data/clickhouse-data`):       ║
# ║                                                                              ║
# ║    ssh root@<rishi-6 ip> 'install -d --owner=101 --group=101 --mode=0750 /data/clickhouse-data' ║
# ║    ssh root@<rishi-4 ip> 'grep -q -x "yral-v2-langfuse" /etc/yral-v2/stacks-to-resync.list \\   ║
# ║      || echo "yral-v2-langfuse" >> /etc/yral-v2/stacks-to-resync.list'    ║
# ║    ssh root@<rishi-5 ip> '<same as rishi-4>'                              ║
# ║    ssh root@<rishi-6 ip> '<same as rishi-4>'                              ║
# ║                                                                              ║
# ║  Additionally, Langfuse expects a `langfuse` role on the shared Patroni  ║
# ║  cluster. This script does NOT yet pre-flight or create that role —      ║
# ║  it's the most likely deploy-time surprise on first invocation. If web   ║
# ║  / worker containers fail to start with auth errors against pgbouncer,   ║
# ║  bootstrap once via the Patroni leader:                                  ║
# ║                                                                              ║
# ║    docker exec <patroni-leader> psql -U postgres -d postgres -c \         ║
# ║      "CREATE ROLE langfuse LOGIN PASSWORD '<see YRAL_LANGFUSE_POSTGRES_  ║
# ║      PASSWORD>'; GRANT CREATE ON DATABASE postgres TO langfuse;"          ║
# ║                                                                              ║
# ║  langfuse-install.sh verifies the prereqs above and fails loud (with      ║
# ║  the exact remediation command) if any are missing.                       ║
# ║                                                                              ║
# ║  📤 OUTPUTS / SIDE EFFECTS                                                   ║
# ║  - Langfuse web container running on rishi-6 (placement label).           ║
# ║  - Langfuse worker container on rishi-6.                                  ║
# ║  - ClickHouse on rishi-6 with bind-mounted /data/clickhouse-data.         ║
# ║  - SHA-rotating Swarm secrets per CONSTRAINTS H2.                         ║
# ║                                                                              ║
# ║  ⚠️ DRAFT — NO STACKS DEPLOYED YET (per agent spec + A13).                   ║
# ║                                                                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝

set -euo pipefail


LANGFUSE_STACK_NAME="yral-v2-langfuse"
THIS_SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LANGFUSE_STACK_COMPOSE_FILE_PATH="${THIS_SCRIPT_DIRECTORY}/langfuse-stack.yml"
SWARM_STACK_RESYNC_REGISTRY_PATH="/etc/yral-v2/stacks-to-resync.list"
LANGFUSE_CLICKHOUSE_BIND_MOUNT_HOST_PATH="/data/clickhouse-data"
LANGFUSE_SWARM_SECRET_BASE_NAMES=(
    yral_v2_langfuse_nextauth_secret
    yral_v2_langfuse_encryption_key
    yral_v2_langfuse_postgres_password
    yral_v2_langfuse_clickhouse_password
)

# Every cluster node — resync-registry pre-flight iterates all three because
# the resync systemd unit runs cluster-wide. The bind-mount + the placement
# of every Langfuse service is rishi-6 (per langfuse-stack.yml's
# `node.hostname == rishi-6` constraint).
CLUSTER_NODE_NAMES=(rishi-4 rishi-5 rishi-6)
LANGFUSE_PLACEMENT_NODE_NAME="rishi-6"

# Post-deploy verifier window. Same shape as patroni-install.sh and
# redis-sentinel-install.sh — `docker stack deploy` returns 0 as soon as
# the spec is in Docker Swarm's internal raft cluster store, NOT when
# tasks are actually running. Polling closes that gap; any task in
# Rejected / Failed state inside the window fails loud.
LANGFUSE_DEPLOY_VERIFY_TIMEOUT_SECONDS="${LANGFUSE_DEPLOY_VERIFY_TIMEOUT_SECONDS:-30}"
LANGFUSE_DEPLOY_VERIFY_POLL_SECONDS="${LANGFUSE_DEPLOY_VERIFY_POLL_SECONDS:-5}"


main() {
    confirm_running_in_swarm_manager_context
    confirm_required_environment_variables_present
    confirm_required_overlays_exist
    confirm_clickhouse_bind_mount_directory_exists_on_langfuse_node

    create_or_rotate_langfuse_swarm_secrets
    render_langfuse_stack_compose_file_to_temporary_path
    deploy_langfuse_stack_into_swarm
    confirm_stack_actually_deployed
    confirm_stack_registered_with_swarm_resync_service
    print_post_install_summary
}


# WHAT: return the public IPv4 for ${1} (a node name like 'rishi-6').
# WHEN: called everywhere this script SSHes to a cluster node.
# WHY:  the script can run from the operator laptop, which has no SSH
#       config alias for the short hostnames. Same trap PR #41 fixed for
#       patroni-install.sh and PR #55 for redis-sentinel-install.sh.
get_public_ipv4_for_node() {
    local node_name="${1}"
    local env_var_name
    env_var_name="YRAL_$(echo "${node_name}" | tr '[:lower:]-' '[:upper:]_')_PUBLIC_IPV4"
    if [[ -z "${!env_var_name:-}" ]]; then
        echo "ERROR langfuse-install: ${env_var_name} is unset (needed to SSH to ${node_name})" >&2
        exit 1
    fi
    echo "${!env_var_name}"
}


confirm_running_in_swarm_manager_context() {
    local swarm_local_node_state
    swarm_local_node_state="$(docker info --format '{{.Swarm.LocalNodeState}}')"
    if [[ "${swarm_local_node_state}" != "active" ]]; then
        echo "ERROR langfuse-install: not in active Swarm" >&2; exit 1
    fi
    if ! docker info --format '{{.Swarm.ControlAvailable}}' | grep --quiet true; then
        echo "ERROR langfuse-install: not on a Swarm manager" >&2; exit 1
    fi
}


confirm_required_environment_variables_present() {
    # WHAT:  fail fast if any required env var is unset.
    # WHEN:  pre-flight.
    # WHY:   without secrets we cannot create Swarm secrets; without IPv4s
    #        we cannot SSH to verify bind dirs or resync registry.
    #        Missing the encryption key would let Langfuse start, write
    #        unencrypted traces, then explode when reads expect encryption.
    local required_environment_variables=(
        YRAL_LANGFUSE_NEXTAUTH_SECRET
        YRAL_LANGFUSE_ENCRYPTION_KEY
        YRAL_LANGFUSE_POSTGRES_PASSWORD
        YRAL_LANGFUSE_CLICKHOUSE_PASSWORD
        YRAL_RISHI_4_PUBLIC_IPV4
        YRAL_RISHI_5_PUBLIC_IPV4
        YRAL_RISHI_6_PUBLIC_IPV4
    )
    local missing_count=0
    local environment_variable_name
    for environment_variable_name in "${required_environment_variables[@]}"; do
        if [[ -z "${!environment_variable_name:-}" ]]; then
            echo "ERROR langfuse-install: required environment variable ${environment_variable_name} is unset" >&2
            missing_count=$((missing_count + 1))
        fi
    done
    if [[ "${missing_count}" -gt 0 ]]; then
        exit 1
    fi
}


confirm_required_overlays_exist() {
    # WHAT:  Langfuse needs both data-plane (to talk to Patroni) and
    #        internal-service overlay (so v2 services can POST traces).
    # WHEN:  pre-flight.
    # WHY:   missing overlays = deploy fails with a confusing error mid-way.
    local overlay_network_name
    for overlay_network_name in yral-v2-data-plane yral-v2-internal; do
        if ! docker network ls --format '{{.Name}}' | grep --quiet --line-regexp "${overlay_network_name}"; then
            echo "ERROR langfuse-install: overlay ${overlay_network_name} missing — run node-bootstrap.sh swarm-init first" >&2
            exit 1
        fi
    done
}


confirm_clickhouse_bind_mount_directory_exists_on_langfuse_node() {
    # WHAT:  ssh to rishi-6 as rishi-deploy and verify /data/clickhouse-data
    #        exists with ownership 101:101 mode 0750.
    # WHEN:  fourth pre-flight.
    # WHY:   langfuse-stack.yml bind-mounts /data/clickhouse-data into the
    #        ClickHouse container per V2 infra doc §7.2 (bind mounts survive
    #        `docker system prune`). Uid 101 matches ClickHouse's user in
    #        the official clickhouse/clickhouse-server image. Narrow
    #        sudoers per CONSTRAINTS C8 doesn't grant rishi-deploy
    #        `sudo install -d`; canonical operator batch lives in this
    #        file's header. This function only VERIFIES the prereq and
    #        fails loud with the exact remediation command if missing.
    #
    #        Note: yesterday's Patroni operator-setup batch (patroni-
    #        install.sh's header) created `/data/langfuse-data` on all 3
    #        nodes with uid 999. That directory is now orphaned — the
    #        Langfuse stack file actually bind-mounts
    #        `/data/clickhouse-data` (uid 101) on rishi-6 only. The
    #        orphaned dir is harmless; cleanup is a separate Day-6+ task.
    local langfuse_node_ipv4
    langfuse_node_ipv4="$(get_public_ipv4_for_node "${LANGFUSE_PLACEMENT_NODE_NAME}")"
    local actual_ownership
    actual_ownership="$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
        "rishi-deploy@${langfuse_node_ipv4}" \
        "test -d ${LANGFUSE_CLICKHOUSE_BIND_MOUNT_HOST_PATH} && stat -c '%u:%g' ${LANGFUSE_CLICKHOUSE_BIND_MOUNT_HOST_PATH}" 2>/dev/null || echo "missing")"
    if [[ "${actual_ownership}" != "101:101" ]]; then
        echo "ERROR langfuse-install: ${LANGFUSE_CLICKHOUSE_BIND_MOUNT_HOST_PATH} on ${LANGFUSE_PLACEMENT_NODE_NAME} is '${actual_ownership}', expected '101:101'" >&2
        echo "  Operator one-time setup (run AS ROOT, while root SSH window is open):" >&2
        echo "    ssh root@${langfuse_node_ipv4} 'install -d --owner=101 --group=101 --mode=0750 ${LANGFUSE_CLICKHOUSE_BIND_MOUNT_HOST_PATH}'" >&2
        echo "  If the directory exists with the wrong ownership, chown it instead:" >&2
        echo "    ssh root@${langfuse_node_ipv4} 'chown -R 101:101 ${LANGFUSE_CLICKHOUSE_BIND_MOUNT_HOST_PATH}'" >&2
        exit 1
    fi
}


create_or_rotate_langfuse_swarm_secrets() {
    # WHAT:  for each Langfuse secret, hash the value and create a SHA-suffixed
    #        Swarm secret per CONSTRAINTS H2.
    # WHEN:  after pre-flight.
    # WHY:   content rotation = new SHA = redeploy on next stack deploy.
    #        The resolved name export below MUST run in BOTH the create-new
    #        and skip-existing branches — otherwise envsubst writes an
    #        empty key into the rendered stack YAML on every re-run (this
    #        was patroni-install.sh bug fixed in PR #45). Original draft
    #        of this function already had the export outside the if; kept
    #        that way + the role-comment captures the invariant.
    declare -A swarm_secret_to_environment_variable=(
        ["yral_v2_langfuse_nextauth_secret"]="YRAL_LANGFUSE_NEXTAUTH_SECRET"
        ["yral_v2_langfuse_encryption_key"]="YRAL_LANGFUSE_ENCRYPTION_KEY"
        ["yral_v2_langfuse_postgres_password"]="YRAL_LANGFUSE_POSTGRES_PASSWORD"
        ["yral_v2_langfuse_clickhouse_password"]="YRAL_LANGFUSE_CLICKHOUSE_PASSWORD"
    )

    local swarm_secret_base_name
    for swarm_secret_base_name in "${LANGFUSE_SWARM_SECRET_BASE_NAMES[@]}"; do
        local environment_variable_name="${swarm_secret_to_environment_variable[${swarm_secret_base_name}]}"
        local secret_value="${!environment_variable_name}"
        local content_sha8
        content_sha8="$(printf '%s' "${secret_value}" | sha256sum | cut --characters=1-8)"
        local fully_qualified_secret_name="${swarm_secret_base_name}_${content_sha8}"

        local resolved_export_name
        resolved_export_name="YRAL_LANGFUSE_STACK_RESOLVED_$(echo "${swarm_secret_base_name}" | tr '[:lower:]' '[:upper:]')"
        export "${resolved_export_name}=${fully_qualified_secret_name}"

        if docker secret inspect "${fully_qualified_secret_name}" >/dev/null 2>&1; then
            echo "langfuse-install: secret ${fully_qualified_secret_name} already exists — skipping"
            continue
        fi

        printf '%s' "${secret_value}" \
            | docker secret create "${fully_qualified_secret_name}" -
    done
}


render_langfuse_stack_compose_file_to_temporary_path() {
    # WHAT:  envsubst the stack file with the 4 resolved Langfuse secret
    #        names. We pass envsubst an EXPLICIT WHITELIST of the
    #        placeholder variable names so it ONLY substitutes those —
    #        every other `$VAR` or `$(...)` token in the stack passes
    #        through untouched. Same pattern PR #57 introduced for
    #        redis-sentinel-install.sh after the bare-envsubst trap.
    # WHEN:  after secret creation.
    # WHY:   keeps the committed YAML free of SHA-suffixed names AND
    #        future-proofs against any container-shell `$VAR` tokens
    #        added later. The current Langfuse stack has no such tokens
    #        but the whitelist is cheap defense-in-depth.
    if [[ ! -f "${LANGFUSE_STACK_COMPOSE_FILE_PATH}" ]]; then
        echo "ERROR langfuse-install: stack file not found" >&2; exit 1
    fi
    LANGFUSE_RENDERED_STACK_COMPOSE_FILE_PATH="$(mktemp /tmp/yral-v2-langfuse-rendered-stack.XXXXXX.yml)"
    envsubst '${YRAL_LANGFUSE_STACK_RESOLVED_YRAL_V2_LANGFUSE_NEXTAUTH_SECRET} ${YRAL_LANGFUSE_STACK_RESOLVED_YRAL_V2_LANGFUSE_ENCRYPTION_KEY} ${YRAL_LANGFUSE_STACK_RESOLVED_YRAL_V2_LANGFUSE_POSTGRES_PASSWORD} ${YRAL_LANGFUSE_STACK_RESOLVED_YRAL_V2_LANGFUSE_CLICKHOUSE_PASSWORD}' \
        < "${LANGFUSE_STACK_COMPOSE_FILE_PATH}" \
        > "${LANGFUSE_RENDERED_STACK_COMPOSE_FILE_PATH}"
    export LANGFUSE_RENDERED_STACK_COMPOSE_FILE_PATH
}


deploy_langfuse_stack_into_swarm() {
    docker stack deploy \
        --compose-file "${LANGFUSE_RENDERED_STACK_COMPOSE_FILE_PATH}" \
        --with-registry-auth \
        --prune \
        "${LANGFUSE_STACK_NAME}"
}


confirm_stack_actually_deployed() {
    # WHAT:  two-layer post-deploy verifier (ported verbatim from
    #        patroni-install.sh's PR #51 + redis-sentinel-install.sh's
    #        PR #55 implementation, renamed for langfuse).
    #        Layer 1 (loop, ${LANGFUSE_DEPLOY_VERIFY_TIMEOUT_SECONDS}s
    #          total at ${LANGFUSE_DEPLOY_VERIFY_POLL_SECONDS}s ticks):
    #          fail loud if ANY task whose desired-state is `running` is
    #          currently in `Rejected` or `Failed` state.
    #        Layer 2 (single check at end of the window): fail loud if
    #          ANY service in the stack has 0 running replicas AND zero
    #          tasks in `docker stack ps` (the "placement matches no
    #          node" case).
    # WHEN:  immediately after deploy_langfuse_stack_into_swarm.
    # WHY:   `docker stack deploy` exits 0 as soon as the spec is in
    #        Docker Swarm's internal raft cluster store — it does NOT
    #        wait for tasks to actually schedule, pull images, or
    #        transition to Running. Day-5 Patroni deploys 2+3 surfaced
    #        the silent-success mode; this verifier closes it for
    #        Langfuse too.
    # WHAT WE DELIBERATELY DON'T DO:
    #        We do NOT wait for all replicas to reach Running. ClickHouse
    #        legitimately spends several minutes in `Preparing`/`Starting`
    #        on first boot while it initialises its schema; langfuse-web
    #        runs Prisma migrations against the `langfuse` schema at
    #        first start. Those are legitimate in-flight states, not
    #        failures.
    local deadline_epoch=$(($(date +%s) + LANGFUSE_DEPLOY_VERIFY_TIMEOUT_SECONDS))
    local rejected_or_failed_tasks=""
    while [[ "$(date +%s)" -lt "${deadline_epoch}" ]]; do
        rejected_or_failed_tasks="$(
            docker stack ps "${LANGFUSE_STACK_NAME}" \
                --filter desired-state=running \
                --format '{{.Name}}	{{.CurrentState}}	{{.Error}}' \
                2>/dev/null \
                | awk -F'	' '$2 ~ /^(Rejected|Failed)/ {print}'
        )"
        if [[ -n "${rejected_or_failed_tasks}" ]]; then
            echo "ERROR langfuse-install: ${LANGFUSE_STACK_NAME} has Rejected/Failed tasks (docker stack deploy returned 0 anyway):" >&2
            echo "${rejected_or_failed_tasks}" >&2
            echo "" >&2
            echo "Full stack ps for debugging:" >&2
            docker stack ps "${LANGFUSE_STACK_NAME}" --no-trunc >&2 || true
            exit 1
        fi
        sleep "${LANGFUSE_DEPLOY_VERIFY_POLL_SECONDS}"
    done

    # Layer 2: confirm Swarm at least scheduled SOMETHING for every service.
    local zero_replica_services
    zero_replica_services="$(
        docker stack services "${LANGFUSE_STACK_NAME}" \
            --format '{{.Name}}	{{.Replicas}}' \
            2>/dev/null \
            | awk -F'	' '$2 ~ /^0\// {print}'
    )"
    if [[ -n "${zero_replica_services}" ]]; then
        local service_name first_field placed_count=0
        while IFS=$'\t' read -r service_name first_field; do
            placed_count="$(
                docker stack ps "${LANGFUSE_STACK_NAME}" \
                    --filter "name=${service_name}" \
                    --format '{{.ID}}' 2>/dev/null \
                    | wc -l | tr -d ' '
            )"
            if [[ "${placed_count}" == "0" ]]; then
                echo "ERROR langfuse-install: service ${service_name} has 0 replicas and Swarm never created any task — likely an unsatisfiable placement constraint." >&2
                echo "" >&2
                echo "Full stack services for debugging:" >&2
                docker stack services "${LANGFUSE_STACK_NAME}" >&2 || true
                exit 1
            fi
        done <<< "${zero_replica_services}"
    fi

    echo "langfuse-install: post-deploy verifier — no Rejected/Failed tasks and every service has at least one task placed, after ${LANGFUSE_DEPLOY_VERIFY_TIMEOUT_SECONDS}s"
}


confirm_stack_registered_with_swarm_resync_service() {
    # WHAT:  verify ${LANGFUSE_STACK_NAME} appears in
    #        ${SWARM_STACK_RESYNC_REGISTRY_PATH} on every cluster node.
    # WHEN:  after deploy, last pre-completion check.
    # WHY:   per CONSTRAINTS H1, the boot-time resync service iterates
    #        this list and re-deploys each stack on reboot. Narrow
    #        sudoers per CONSTRAINTS C8 doesn't grant rishi-deploy
    #        `sudo tee --append`, so the append is a one-time operator
    #        setup that runs AS ROOT (canonical batch in this file's
    #        header). This function only VERIFIES the prereq and fails
    #        loud with the exact remediation command if missing.
    local cluster_node_name
    for cluster_node_name in "${CLUSTER_NODE_NAMES[@]}"; do
        local cluster_node_ipv4
        cluster_node_ipv4="$(get_public_ipv4_for_node "${cluster_node_name}")"
        if ! ssh -o BatchMode=yes "rishi-deploy@${cluster_node_ipv4}" \
            "grep --quiet --line-regexp ${LANGFUSE_STACK_NAME} ${SWARM_STACK_RESYNC_REGISTRY_PATH}" 2>/dev/null; then
            echo "ERROR langfuse-install: ${LANGFUSE_STACK_NAME} not registered in ${SWARM_STACK_RESYNC_REGISTRY_PATH} on ${cluster_node_name}" >&2
            echo "  Operator one-time setup (run AS ROOT, while root SSH window is open):" >&2
            echo "    ssh root@${cluster_node_ipv4} 'grep -q -x \"${LANGFUSE_STACK_NAME}\" ${SWARM_STACK_RESYNC_REGISTRY_PATH} || echo \"${LANGFUSE_STACK_NAME}\" >> ${SWARM_STACK_RESYNC_REGISTRY_PATH}'" >&2
            exit 1
        fi
    done
}


print_post_install_summary() {
    cat <<SUMMARY

✅ langfuse-install finished — Langfuse stack deployed as ${LANGFUSE_STACK_NAME}.

Verify:
  docker stack ps ${LANGFUSE_STACK_NAME}              # all replicas Running?
  curl --silent --fail http://langfuse-web:3000/api/public/health   # from another container on the internal overlay

Next steps (NOT in this script):
  - Add Caddy snippet on rishi-1/2 for langfuse.rishi.yral.com -> rishi-6 web container
  - Per-service Langfuse keys (LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY)
    are minted in the Langfuse UI and stored in each service's GitHub Secrets.
SUMMARY
}


main "$@"


# ══════════════════════════════════════════════════════════════════════════
# RELATED FILES
# ─────────────
# - langfuse-stack.yml       — the Compose stack this script deploys.
# - patroni-install.sh       — must run first (Langfuse uses the langfuse schema).
#                              Canonical reference for operator-setup batch +
#                              verify-only pre-flight + post-deploy verifier
#                              patterns ported here.
# - redis-sentinel-install.sh — must run first (Langfuse uses Redis for queues).
# - ../secrets-manifest.yaml — declares LANGFUSE_NEXTAUTH_SECRET + LANGFUSE_ENCRYPTION_KEY.
# ══════════════════════════════════════════════════════════════════════════
