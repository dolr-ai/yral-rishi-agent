#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  patroni-install.sh                                                         ║
# ║                                                                              ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                                ║
# ║  Deploy the v2 cluster's HA Postgres (Patroni leader + sync replica +       ║
# ║  async replica + 3-node etcd quorum + 2-replica pgBouncer) onto rishi-4/5/6║
# ║  by reading secrets from GitHub Secrets, materialising them as SHA-rotating║
# ║  Swarm secrets, and `docker stack deploy`-ing patroni-stack.yml.            ║
# ║                                                                              ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                           ║
# ║  Postgres is the single shared database all 13 v2 services use (one schema ║
# ║  per service per CONSTRAINTS F3). Patroni is the layer on top that elects  ║
# ║  a leader, replicates writes to a synchronous replica (so no write is      ║
# ║  acknowledged until at least one replica has it — RPO 0 within the         ║
# ║  cluster), and auto-fails-over within ~60s if the leader dies (RTO < 60s). ║
# ║  etcd is the consensus store Patroni uses for leader election; we run     ║
# ║  three etcd members so any one can fail without losing quorum. PgBouncer   ║
# ║  sits in front of Patroni (CONSTRAINTS G3) so 13 services × 20 connections║
# ║  doesn't overwhelm Postgres's per-connection cost. This script does NOT    ║
# ║  install anything inside Postgres itself — that's done by per-service     ║
# ║  alembic migrations against the schema each service owns.                  ║
# ║                                                                              ║
# ║  🔗 HOW IT FITS                                                              ║
# ║  - Runs on:    rishi-4 (manager) AFTER node-bootstrap.sh has finished     ║
# ║                swarm-init/swarm-join on all three nodes.                   ║
# ║  - Reads:      ../secrets-manifest.yaml (declares the secrets used here). ║
# ║                Secret VALUES come from GitHub Secrets at deploy time, NOT  ║
# ║                from the manifest (per CONSTRAINTS D1).                     ║
# ║  - Deploys:    patroni-stack.yml (sibling file) into the                   ║
# ║                yral-agent-data-plane-overlay overlay network.              ║
# ║  - Followed by: redis-sentinel-install.sh + langfuse-install.sh; per-      ║
# ║                service alembic migrations come later, run by each service.║
# ║                                                                              ║
# ║  📥 INPUTS (environment variables, from GitHub Secrets via Actions)         ║
# ║  - YRAL_POSTGRES_SUPERUSER_PASSWORD     (from GitHub Secret)               ║
# ║  - YRAL_PATRONI_REPLICATION_PASSWORD    (from GitHub Secret)               ║
# ║  - YRAL_PATRONI_REST_API_PASSWORD       (from GitHub Secret)               ║
# ║  - YRAL_HETZNER_S3_ACCESS_KEY_ID        (from GitHub Secret, for WAL-G)    ║
# ║  - YRAL_HETZNER_S3_SECRET_ACCESS_KEY    (from GitHub Secret, for WAL-G)    ║
# ║  - YRAL_HETZNER_S3_WAL_BUCKET_NAME      (e.g. rishi-yral-wal-archive)      ║
# ║  - YRAL_HETZNER_S3_REGION               (e.g. fsn1)                        ║
# ║  - YRAL_HETZNER_S3_ENDPOINT             (e.g. https://fsn1.your-objectstorage.com) ║
# ║                                                                              ║
# ║  📤 OUTPUTS / SIDE EFFECTS                                                   ║
# ║  - 3 etcd Swarm services pinned to rishi-4/5/6 via placement constraints.  ║
# ║  - 3 Patroni Swarm services pinned to the same nodes — leader election    ║
# ║    decides which one becomes primary.                                       ║
# ║  - 2 pgBouncer Swarm services on rishi-4/5 (edge nodes only).              ║
# ║  - SHA-rotating Swarm secrets per CONSTRAINTS H2.                           ║
# ║  - Bind-mounted /data/patroni-data per node (per V2 infra doc §7.2 — bind ║
# ║    mounts survive `docker system prune` where named volumes did not).      ║
# ║                                                                              ║
# ║  ⚠️ DRAFT — NO STACKS DEPLOYED YET (per agent spec + A13)                    ║
# ║  Real `docker stack deploy` runs Day 5 with separate Rishi YES.            ║
# ║                                                                              ║
# ║  ⭐ START HERE                                                               ║
# ║  Read main(); every function is called from there in order.                ║
# ╚══════════════════════════════════════════════════════════════════════════╝

set -euo pipefail


# ──────────────────────── Constants ────────────────────────────────────────

# Stack name passed to `docker stack deploy`. All Swarm objects this script
# creates inherit this prefix (e.g. yral-v2-patroni_etcd-rishi-4).
PATRONI_STACK_NAME="yral-v2-patroni"

# Sibling stack file. Resolved relative to this script for path-safety.
THIS_SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATRONI_STACK_COMPOSE_FILE_PATH="${THIS_SCRIPT_DIRECTORY}/patroni-stack.yml"

# Per CONSTRAINTS H1, every stack we deploy gets registered with the
# resync systemd service. The registry lists stack names one per line.
SWARM_STACK_RESYNC_REGISTRY_PATH="/etc/yral-v2/stacks-to-resync.list"

# Per CONSTRAINTS H2, every Swarm secret name is suffixed with the SHA8 of
# its content so a content change creates a new secret + we can prune the
# old one after the consuming services roll over.

# Bind-mount root for Patroni's PGDATA — survives `docker system prune` per
# the V2 infra doc §7.2 (Docker volumes for Patroni were a known pain point).
PATRONI_BIND_MOUNT_HOST_PATH="/data/patroni-data"

# Names of every Swarm secret this stack consumes. Each one gets created
# (or rotated) by this script; the corresponding SHA-suffixed name is
# substituted into the rendered stack YAML.
PATRONI_SWARM_SECRET_NAMES=(
    yral_v2_postgres_superuser_password
    yral_v2_patroni_replication_password
    yral_v2_patroni_rest_api_password
    yral_v2_hetzner_s3_access_key_id
    yral_v2_hetzner_s3_secret_access_key
)


# ─────────────────────────── Entry point ───────────────────────────────────


main() {
    confirm_running_in_swarm_manager_context
    confirm_required_environment_variables_present
    confirm_data_plane_overlay_exists

    create_patroni_bind_mount_directories_on_each_node
    create_or_rotate_swarm_secrets_with_sha8_suffix
    render_patroni_stack_compose_file_to_temporary_path
    deploy_patroni_stack_into_swarm
    register_stack_with_swarm_resync_service
    print_post_install_summary
}


# ──────────────────── Pre-flight ────────────────────────────────────────────


confirm_running_in_swarm_manager_context() {
    # WHAT:  refuse to continue if `docker info` shows no Swarm or worker role.
    # WHEN:  first pre-flight check.
    # WHY:   `docker stack deploy` only works on a Swarm manager. Failing here
    #        gives a clear message instead of letting the deploy command
    #        emit "this node is not a swarm manager" mid-run.
    local swarm_local_node_state
    swarm_local_node_state="$(docker info --format '{{.Swarm.LocalNodeState}}')"
    if [[ "${swarm_local_node_state}" != "active" ]]; then
        echo "ERROR patroni-install: this node is not in an active Swarm (state=${swarm_local_node_state})" >&2
        exit 1
    fi
    if ! docker info --format '{{.Swarm.ControlAvailable}}' | grep --quiet true; then
        echo "ERROR patroni-install: this node is not a Swarm manager — run from rishi-4/5/6" >&2
        exit 1
    fi
}


confirm_required_environment_variables_present() {
    # WHAT:  fail fast if any of the secrets-manifest-declared env vars
    #        the GitHub Action populates is unset.
    # WHEN:  second pre-flight.
    # WHY:   missing one mid-deploy leaves Patroni unable to start; the
    #        leader election would then loop forever. Catch up front.
    local required_environment_variables=(
        YRAL_POSTGRES_SUPERUSER_PASSWORD
        YRAL_PATRONI_REPLICATION_PASSWORD
        YRAL_PATRONI_REST_API_PASSWORD
        YRAL_HETZNER_S3_ACCESS_KEY_ID
        YRAL_HETZNER_S3_SECRET_ACCESS_KEY
        YRAL_HETZNER_S3_WAL_BUCKET_NAME
        YRAL_HETZNER_S3_REGION
        YRAL_HETZNER_S3_ENDPOINT
    )

    local missing_count=0
    for environment_variable_name in "${required_environment_variables[@]}"; do
        if [[ -z "${!environment_variable_name:-}" ]]; then
            echo "ERROR patroni-install: required environment variable ${environment_variable_name} is unset" >&2
            missing_count=$((missing_count + 1))
        fi
    done

    if [[ "${missing_count}" -gt 0 ]]; then
        echo "ERROR patroni-install: ${missing_count} required environment variable(s) missing" >&2
        exit 1
    fi
}


confirm_data_plane_overlay_exists() {
    # WHAT:  check that node-bootstrap.sh's swarm-init phase already created
    #        yral-agent-data-plane-overlay.
    # WHEN:  third pre-flight.
    # WHY:   the stack file references this overlay as `external: true`. If
    #        it's missing, `docker stack deploy` would error mid-way. Better
    #        to fail with a clear pointer to node-bootstrap.sh first.
    if ! docker network ls --format '{{.Name}}' | grep --quiet --line-regexp yral-agent-data-plane-overlay; then
        echo "ERROR patroni-install: yral-agent-data-plane-overlay missing — run node-bootstrap.sh swarm-init first" >&2
        exit 1
    fi
}


# ──────────────────── Pre-deploy setup ──────────────────────────────────────


create_patroni_bind_mount_directories_on_each_node() {
    # WHAT:  ssh to every node and `mkdir -p /data/patroni-data`, then chown
    #        to UID 999 (the Postgres uid inside the official Patroni image).
    # WHEN:  before deploy — Patroni containers cannot start if the host
    #        path is missing or unwritable.
    # WHY:   bind mounts (not Docker volumes) per V2 infra doc §7.2 — they
    #        survive `docker system prune` and let WAL-G read PGDATA from
    #        a sidecar without bind-mount-into-bind-mount weirdness.
    local cluster_node_hostname
    for cluster_node_hostname in rishi-4 rishi-5 rishi-6; do
        ssh -o StrictHostKeyChecking=accept-new "rishi-deploy@${cluster_node_hostname}" \
            "sudo install --owner=999 --group=999 --mode=0700 --directory ${PATRONI_BIND_MOUNT_HOST_PATH}"
    done
}


create_or_rotate_swarm_secrets_with_sha8_suffix() {
    # WHAT:  for every secret in PATRONI_SWARM_SECRET_NAMES, hash its current
    #        environment-variable value with sha256, take the first 8 chars, and create a
    #        Swarm secret named `<base>_<sha8>`. Skip if the same name exists.
    # WHEN:  after pre-flight, before render_patroni_stack_compose_file.
    # WHY:   per CONSTRAINTS H2, content-rotating secret names mean a value
    #        change = a new Swarm secret = `docker stack deploy` redeploys
    #        the consuming services with the new mount. Old names are
    #        pruned by a separate cleanup workflow (not in this script).

    # Map secret-base-name → environment-variable-name that holds its value.
    declare -A swarm_secret_to_environment_variable=(
        ["yral_v2_postgres_superuser_password"]="YRAL_POSTGRES_SUPERUSER_PASSWORD"
        ["yral_v2_patroni_replication_password"]="YRAL_PATRONI_REPLICATION_PASSWORD"
        ["yral_v2_patroni_rest_api_password"]="YRAL_PATRONI_REST_API_PASSWORD"
        ["yral_v2_hetzner_s3_access_key_id"]="YRAL_HETZNER_S3_ACCESS_KEY_ID"
        ["yral_v2_hetzner_s3_secret_access_key"]="YRAL_HETZNER_S3_SECRET_ACCESS_KEY"
    )

    local swarm_secret_base_name
    for swarm_secret_base_name in "${PATRONI_SWARM_SECRET_NAMES[@]}"; do
        local environment_variable_name="${swarm_secret_to_environment_variable[${swarm_secret_base_name}]}"
        local secret_value="${!environment_variable_name}"

        local content_sha8
        content_sha8="$(printf '%s' "${secret_value}" | sha256sum | cut --characters=1-8)"
        local fully_qualified_secret_name="${swarm_secret_base_name}_${content_sha8}"

        if docker secret inspect "${fully_qualified_secret_name}" >/dev/null 2>&1; then
            echo "patroni-install: secret ${fully_qualified_secret_name} already exists — skipping"
            continue
        fi

        printf '%s' "${secret_value}" \
            | docker secret create "${fully_qualified_secret_name}" -

        # Export the resolved name into a script-scoped environment variable so
        # render_patroni_stack_compose_file_to_temporary_path can substitute
        # ${YRAL_PATRONI_STACK_RESOLVED_<UPPERCASED_BASE_NAME>}.
        local resolved_export_name
        resolved_export_name="YRAL_PATRONI_STACK_RESOLVED_$(echo "${swarm_secret_base_name}" | tr '[:lower:]' '[:upper:]')"
        export "${resolved_export_name}=${fully_qualified_secret_name}"
    done
}


render_patroni_stack_compose_file_to_temporary_path() {
    # WHAT:  envsubst the sibling patroni-stack.yml into a temp file, with
    #        the resolved SHA-suffixed secret names substituted in.
    # WHEN:  after secrets are created.
    # WHY:   the committed YAML uses `${YRAL_PATRONI_STACK_RESOLVED_*}`
    #        placeholders so a content rotation does not require editing the
    #        committed file. envsubst is part of `gettext-base`, available
    #        on Ubuntu by default.

    if [[ ! -f "${PATRONI_STACK_COMPOSE_FILE_PATH}" ]]; then
        echo "ERROR patroni-install: stack file not found at ${PATRONI_STACK_COMPOSE_FILE_PATH}" >&2
        exit 1
    fi

    PATRONI_RENDERED_STACK_COMPOSE_FILE_PATH="$(mktemp /tmp/yral-v2-patroni-rendered-stack.XXXXXX.yml)"
    envsubst < "${PATRONI_STACK_COMPOSE_FILE_PATH}" > "${PATRONI_RENDERED_STACK_COMPOSE_FILE_PATH}"
    export PATRONI_RENDERED_STACK_COMPOSE_FILE_PATH
}


deploy_patroni_stack_into_swarm() {
    # WHAT:  `docker stack deploy --compose-file <rendered> <stackname>`.
    # WHEN:  after the stack file is rendered.
    # WHY:   this is the moment that matters. --with-registry-auth so the
    #        worker nodes can pull the official Patroni image from Docker
    #        Hub when the service first schedules on them.
    docker stack deploy \
        --compose-file "${PATRONI_RENDERED_STACK_COMPOSE_FILE_PATH}" \
        --with-registry-auth \
        --prune \
        "${PATRONI_STACK_NAME}"
}


register_stack_with_swarm_resync_service() {
    # WHAT:  append the stack name to /etc/yral-v2/stacks-to-resync.list if
    #        not already present, on every cluster node.
    # WHEN:  after deploy.
    # WHY:   per CONSTRAINTS H1, the boot-time resync service iterates this
    #        list and re-deploys each stack. Without registration, this
    #        stack would not survive a reboot.
    local cluster_node_hostname
    for cluster_node_hostname in rishi-4 rishi-5 rishi-6; do
        ssh "rishi-deploy@${cluster_node_hostname}" \
            "grep --quiet --line-regexp ${PATRONI_STACK_NAME} ${SWARM_STACK_RESYNC_REGISTRY_PATH} \
                || echo ${PATRONI_STACK_NAME} | sudo tee --append ${SWARM_STACK_RESYNC_REGISTRY_PATH} >/dev/null"
    done
}


# ──────────────────── Final summary ─────────────────────────────────────────


print_post_install_summary() {
    cat <<SUMMARY

✅ patroni-install finished — Patroni stack deployed as ${PATRONI_STACK_NAME}.

Verify:
  docker stack ps ${PATRONI_STACK_NAME}                       # all replicas Running?
  docker service logs ${PATRONI_STACK_NAME}_patroni-rishi-4   # leader logs
  docker exec -it \$(docker ps -q -f name=${PATRONI_STACK_NAME}_patroni-rishi-4) \\
      patronictl list                                          # cluster topology

Next:
  ./redis-sentinel-install.sh
  ./langfuse-install.sh
SUMMARY
}


main "$@"


# ══════════════════════════════════════════════════════════════════════════
# RELATED FILES
# ─────────────
# - patroni-stack.yml        — the Compose stack this script deploys.
# - node-bootstrap.sh        — must run first (creates data-plane overlay).
# - redis-sentinel-install.sh, langfuse-install.sh  — siblings that run after.
# - ../secrets-manifest.yaml — declares every secret this script consumes.
# ══════════════════════════════════════════════════════════════════════════
