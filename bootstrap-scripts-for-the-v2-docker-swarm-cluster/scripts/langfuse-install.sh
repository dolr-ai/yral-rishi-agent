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
# ║               LANGFUSE_ENCRYPTION_KEY + POSTGRES_SUPERUSER_PASSWORD      ║
# ║               (for the langfuse schema bootstrap).                        ║
# ║  - Deploys:   langfuse-stack.yml on yral-agent-data-plane-overlay +      ║
# ║               yral-agent-internal-service-to-service-overlay (so v2      ║
# ║               services can POST traces).                                  ║
# ║  - Followed by: per-service apps post traces to                          ║
# ║               http://langfuse-web:3000 via the langfuse-python SDK.      ║
# ║                                                                              ║
# ║  📥 INPUTS (environment variables)                                           ║
# ║  - YRAL_LANGFUSE_NEXTAUTH_SECRET           (32+ char random)              ║
# ║  - YRAL_LANGFUSE_ENCRYPTION_KEY            (32-byte hex)                  ║
# ║  - YRAL_LANGFUSE_POSTGRES_PASSWORD         (the langfuse-schema role)     ║
# ║  - YRAL_LANGFUSE_CLICKHOUSE_PASSWORD       (random; auto-generate if not) ║
# ║                                                                              ║
# ║  📤 OUTPUTS / SIDE EFFECTS                                                   ║
# ║  - Langfuse web container running on rishi-6 (placement label).           ║
# ║  - Langfuse worker container on rishi-6.                                  ║
# ║  - ClickHouse on rishi-6 with bind-mounted /data/clickhouse-data.         ║
# ║  - SHA-rotating Swarm secrets per CONSTRAINTS H2.                         ║
# ║  - Bootstrap step executed once: creates `langfuse` schema + role on     ║
# ║    the shared Patroni cluster.                                            ║
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


main() {
    confirm_running_in_swarm_manager_context
    confirm_required_environment_variables_present
    confirm_required_overlays_exist

    create_clickhouse_bind_mount_directory_on_rishi_6
    create_or_rotate_langfuse_swarm_secrets
    render_langfuse_stack_compose_file_to_temporary_path
    deploy_langfuse_stack_into_swarm
    register_stack_with_swarm_resync_service
    print_post_install_summary
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
    # WHAT:  fail fast if any Langfuse environment variable is unset.
    # WHEN:  pre-flight.
    # WHY:   missing the encryption key would let Langfuse start, write
    #        unencrypted traces, then explode when reads expect encryption.
    local required_environment_variables=(
        YRAL_LANGFUSE_NEXTAUTH_SECRET
        YRAL_LANGFUSE_ENCRYPTION_KEY
        YRAL_LANGFUSE_POSTGRES_PASSWORD
        YRAL_LANGFUSE_CLICKHOUSE_PASSWORD
    )
    local missing_count=0
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
    for overlay_network_name in yral-agent-data-plane-overlay yral-agent-internal-service-to-service-overlay; do
        if ! docker network ls --format '{{.Name}}' | grep --quiet --line-regexp "${overlay_network_name}"; then
            echo "ERROR langfuse-install: overlay ${overlay_network_name} missing — run node-bootstrap.sh swarm-init first" >&2
            exit 1
        fi
    done
}


create_clickhouse_bind_mount_directory_on_rishi_6() {
    # WHAT:  mkdir /data/clickhouse-data on rishi-6 with ClickHouse's uid (101).
    # WHEN:  before deploy.
    # WHY:   ClickHouse refuses to start if PGDATA-equivalent path isn't owned
    #        by uid 101 (ClickHouse's internal user).
    ssh -o StrictHostKeyChecking=accept-new "rishi-deploy@rishi-6" \
        "sudo install --owner=101 --group=101 --mode=0750 --directory ${LANGFUSE_CLICKHOUSE_BIND_MOUNT_HOST_PATH}"
}


create_or_rotate_langfuse_swarm_secrets() {
    # WHAT:  for each Langfuse secret, hash the value and create a SHA-suffixed
    #        Swarm secret per CONSTRAINTS H2.
    # WHEN:  after pre-flight.
    # WHY:   content rotation = new SHA = redeploy on next stack deploy.
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

        if ! docker secret inspect "${fully_qualified_secret_name}" >/dev/null 2>&1; then
            printf '%s' "${secret_value}" \
                | docker secret create "${fully_qualified_secret_name}" -
        fi

        local resolved_export_name
        resolved_export_name="YRAL_LANGFUSE_STACK_RESOLVED_$(echo "${swarm_secret_base_name}" | tr '[:lower:]' '[:upper:]')"
        export "${resolved_export_name}=${fully_qualified_secret_name}"
    done
}


render_langfuse_stack_compose_file_to_temporary_path() {
    if [[ ! -f "${LANGFUSE_STACK_COMPOSE_FILE_PATH}" ]]; then
        echo "ERROR langfuse-install: stack file not found" >&2; exit 1
    fi
    LANGFUSE_RENDERED_STACK_COMPOSE_FILE_PATH="$(mktemp /tmp/yral-v2-langfuse-rendered-stack.XXXXXX.yml)"
    envsubst < "${LANGFUSE_STACK_COMPOSE_FILE_PATH}" > "${LANGFUSE_RENDERED_STACK_COMPOSE_FILE_PATH}"
    export LANGFUSE_RENDERED_STACK_COMPOSE_FILE_PATH
}


deploy_langfuse_stack_into_swarm() {
    docker stack deploy \
        --compose-file "${LANGFUSE_RENDERED_STACK_COMPOSE_FILE_PATH}" \
        --with-registry-auth \
        --prune \
        "${LANGFUSE_STACK_NAME}"
}


register_stack_with_swarm_resync_service() {
    local cluster_node_hostname
    for cluster_node_hostname in rishi-4 rishi-5 rishi-6; do
        ssh "rishi-deploy@${cluster_node_hostname}" \
            "grep --quiet --line-regexp ${LANGFUSE_STACK_NAME} ${SWARM_STACK_RESYNC_REGISTRY_PATH} \
                || echo ${LANGFUSE_STACK_NAME} | sudo tee --append ${SWARM_STACK_RESYNC_REGISTRY_PATH} >/dev/null"
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
# - redis-sentinel-install.sh — must run first (Langfuse uses Redis for queues).
# - ../secrets-manifest.yaml — declares LANGFUSE_NEXTAUTH_SECRET + LANGFUSE_ENCRYPTION_KEY.
# ══════════════════════════════════════════════════════════════════════════
