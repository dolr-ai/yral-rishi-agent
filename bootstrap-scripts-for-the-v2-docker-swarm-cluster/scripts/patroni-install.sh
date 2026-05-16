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
# ║                yral-v2-data-plane overlay network.                         ║
# ║  - Followed by: redis-sentinel-install.sh + langfuse-install.sh; per-      ║
# ║                service alembic migrations come later, run by each service.║
# ║                                                                              ║
# ║  📥 INPUTS (environment variables, from GitHub Secrets via Actions)         ║
# ║  - YRAL_POSTGRES_SUPERUSER_PASSWORD     (from GitHub Secret)               ║
# ║  - YRAL_PATRONI_REPLICATION_PASSWORD    (from GitHub Secret)               ║
# ║  - YRAL_PATRONI_REST_API_PASSWORD       (from GitHub Secret)               ║
# ║  - YRAL_RISHI_4_PUBLIC_IPV4             IPv4 of rishi-4 (used for SSH)    ║
# ║  - YRAL_RISHI_5_PUBLIC_IPV4             IPv4 of rishi-5 (used for SSH)    ║
# ║  - YRAL_RISHI_6_PUBLIC_IPV4             IPv4 of rishi-6 (used for SSH)    ║
# ║  - YRAL_PATRONI_PRODUCTION_MODE         "true"|"false" (default false).   ║
# ║                                          When true, the script REFUSES   ║
# ║                                          to deploy unless WAL-G is also  ║
# ║                                          enabled — codifies CONSTRAINTS  ║
# ║                                          D2 (3-layer backup; L2 = WAL-G  ║
# ║                                          PITR). Today's HA-only deploy   ║
# ║                                          leaves this unset (default      ║
# ║                                          false). Day-5b WAL-G            ║
# ║                                          enablement PR flips both to    ║
# ║                                          true together.                  ║
# ║  - YRAL_PATRONI_WAL_G_ENABLED           "true"|"false" (default false).   ║
# ║                                          When true, the 5 S3 env vars   ║
# ║                                          below are required + Spilo's   ║
# ║                                          WAL-G archive/restore is on.    ║
# ║  - YRAL_HETZNER_S3_ACCESS_KEY_ID        REQUIRED iff WAL_G_ENABLED=true  ║
# ║  - YRAL_HETZNER_S3_SECRET_ACCESS_KEY    REQUIRED iff WAL_G_ENABLED=true  ║
# ║  - YRAL_HETZNER_S3_WAL_BUCKET_NAME      REQUIRED iff WAL_G_ENABLED=true  ║
# ║  - YRAL_HETZNER_S3_REGION               REQUIRED iff WAL_G_ENABLED=true  ║
# ║  - YRAL_HETZNER_S3_ENDPOINT             REQUIRED iff WAL_G_ENABLED=true  ║
# ║                                                                              ║
# ║  🛠️ ONE-TIME OPERATOR SETUP (run AS ROOT, while root SSH window is open)    ║
# ║  Narrow sudoers per CONSTRAINTS C8 doesn't grant `sudo install -d` /       ║
# ║  `sudo tee --append` to rishi-deploy, so the script CANNOT create the     ║
# ║  bind-mount directories or append to the resync registry by itself. Run    ║
# ║  this batch once per fresh cluster, covers all three stateful services:   ║
# ║                                                                              ║
# ║    for ip in <rishi-4 ip> <rishi-5 ip> <rishi-6 ip>; do                    ║
# ║      ssh root@$ip 'set -e                                                  ║
# ║        install -d --owner=101 --group=103 --mode=0700 /data/patroni-data  ║
# ║        install -d --owner=999 --group=999 --mode=0700 /data/redis-data    ║
# ║        install -d --owner=999 --group=999 --mode=0700 /data/langfuse-data ║
# ║        install -d --owner=999 --group=999 --mode=0700 /data/etcd-$(hostname) ║
# ║                                                                              ║
# ║      # NOTE: /data/patroni-data uses uid 101 gid 103 because Spilo         ║
# ║      # (ghcr.io/zalando/spilo-15:3.0-p1) is Debian-based and its postgres  ║
# ║      # user is uid 101 / gid 103 — NOT the uid 999 used by the official    ║
# ║      # `postgres:*` Docker image. Confirmed via `docker exec <patroni> id  ║
# ║      # postgres` against the live container. Other stateful dirs stay at   ║
# ║      # 999:999 because their containers either run as root (etcd) or use   ║
# ║      # the standard `redis`/`langfuse` images which DO use uid 999.        ║
# ║        for stack in yral-v2-patroni yral-v2-redis yral-v2-langfuse; do    ║
# ║          grep -q -x "$stack" /etc/yral-v2/stacks-to-resync.list \\        ║
# ║            || echo "$stack" >> /etc/yral-v2/stacks-to-resync.list          ║
# ║        done                                                                ║
# ║      '                                                                     ║
# ║    done                                                                    ║
# ║                                                                              ║
# ║  NOTE: `/data/etcd-$(hostname)` is per-node — only the directory for       ║
# ║  THIS host's etcd member is created on each box (rishi-4 gets             ║
# ║  /data/etcd-rishi-4 only). patroni-stack.yml pins each etcd container    ║
# ║  to its named host so the bind mount is node-local.                      ║
# ║                                                                              ║
# ║  patroni-install.sh, redis-sentinel-install.sh, and langfuse-install.sh   ║
# ║  then verify these prereqs and fail loud (with this exact remediation)    ║
# ║  if missing.                                                               ║
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

# Post-deploy verifier window. `docker stack deploy` returns 0 as soon
# as the spec has been written to Docker Swarm's internal raft cluster
# store (the consensus store that tracks the desired state of every
# service) — NOT when tasks are actually running. If any task is
# Rejected (bad image, missing bind dir, missing constraint, etc.),
# Swarm keeps retrying it every few seconds while the script would
# have already printed "✅ patroni-install finished". Day-5 deploys 2
# + 3 bit us with exactly this silent-success mode (bind-dir-missing +
# wrong pgbouncer image tag). We now poll docker stack ps for a brief
# window and fail loud if anything lands in Rejected/Failed, and at
# the end of the window we also cross-check `docker stack services`
# to make sure each service has at least one replica scheduled
# (catches the "Swarm could not schedule ANY task on any node" case
# that wouldn't show as Rejected because no task got created at all).
PATRONI_DEPLOY_VERIFY_TIMEOUT_SECONDS="${PATRONI_DEPLOY_VERIFY_TIMEOUT_SECONDS:-30}"
PATRONI_DEPLOY_VERIFY_POLL_SECONDS="${PATRONI_DEPLOY_VERIFY_POLL_SECONDS:-5}"

# Per CONSTRAINTS H2, every Swarm secret name is suffixed with the SHA8 of
# its content so a content change creates a new secret + we can prune the
# old one after the consuming services roll over.

# Bind-mount root for Patroni's PGDATA — survives `docker system prune` per
# the V2 infra doc §7.2 (Docker volumes for Patroni were a known pain point).
# The directory is created ONCE per fresh cluster by the operator running
# the root-window batch from this file's header; this script only verifies.
PATRONI_BIND_MOUNT_HOST_PATH="/data/patroni-data"

# Per-node etcd data directories. patroni-stack.yml pins one etcd container
# per host with a NODE-SPECIFIC bind mount (etcd-rishi-4 binds /data/etcd-
# rishi-4 on rishi-4, etc.) so an etcd member's state stays on its own box
# and survives a reschedule on the same node. Each path exists on exactly
# one node (its own); checked + created by the same operator setup.
PATRONI_ETCD_BIND_MOUNT_HOST_PATH_PREFIX="/data/etcd-"

# Cluster node names — each node's public IPv4 comes in via
# `YRAL_RISHI_<digit>_PUBLIC_IPV4`. SSH targets are built from those IPs
# (not from hostnames) because the operator's laptop has no DNS / SSH
# config for these short names — same lesson as PR #29 advertise-addr.
CLUSTER_NODE_NAMES=(rishi-4 rishi-5 rishi-6)

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
    confirm_production_mode_requires_wal_g
    confirm_data_plane_overlay_exists
    confirm_patroni_bind_mount_directories_exist_on_each_node

    create_or_rotate_swarm_secrets_with_sha8_suffix
    render_patroni_stack_compose_file_to_temporary_path
    deploy_patroni_stack_into_swarm
    confirm_stack_actually_deployed
    confirm_stack_registered_with_swarm_resync_service
    print_post_install_summary
}


# WHAT: return the public IPv4 for ${1} (a node name like 'rishi-4').
# WHEN: called everywhere the script SSHes to a cluster node.
# WHY:  the operator's laptop has no DNS / SSH config alias for the short
#       hostnames; the cluster.hosts.yaml shape promises IPv4 via the
#       YRAL_RISHI_<digit>_PUBLIC_IPV4 env vars and pre-flight asserts they
#       exist. Centralising the lookup avoids repeating the indirect-ref
#       dance at every SSH call site.
get_public_ipv4_for_node() {
    local node_name="$1"
    local node_digit="${node_name##rishi-}"
    local ip_environment_variable_name="YRAL_RISHI_${node_digit}_PUBLIC_IPV4"
    printf '%s' "${!ip_environment_variable_name}"
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
        YRAL_RISHI_4_PUBLIC_IPV4
        YRAL_RISHI_5_PUBLIC_IPV4
        YRAL_RISHI_6_PUBLIC_IPV4
    )
    # The 5 Hetzner S3 vars are only required when WAL-G archive is enabled.
    # Default (WAL_G_ENABLED unset or "false") deploys Patroni HA without
    # WAL-G — L1 sync replication still gives RPO 0 within the cluster;
    # adding L2 PITR is a separate Day-5b iteration once the Hetzner Object
    # Storage bucket is provisioned. Per Rishi 2026-05-14.
    if [[ "${YRAL_PATRONI_WAL_G_ENABLED:-false}" == "true" ]]; then
        required_environment_variables+=(
            YRAL_HETZNER_S3_ACCESS_KEY_ID
            YRAL_HETZNER_S3_SECRET_ACCESS_KEY
            YRAL_HETZNER_S3_WAL_BUCKET_NAME
            YRAL_HETZNER_S3_REGION
            YRAL_HETZNER_S3_ENDPOINT
        )
    fi

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


confirm_production_mode_requires_wal_g() {
    # WHAT:  refuse to deploy when YRAL_PATRONI_PRODUCTION_MODE=true unless
    #        YRAL_PATRONI_WAL_G_ENABLED=true also.
    # WHEN:  pre-flight, immediately after env-var-presence check.
    # WHY:   CONSTRAINTS D2 mandates 3-layer backup (L1 HA + L2 WAL-G PITR
    #        + L3 offsite). HA without L2 PITR is a real-but-narrow
    #        configuration acceptable for dev/staging + day-of HA testing,
    #        but a production deploy without WAL-G violates D2. This
    #        guard codifies the spirit of D2 without blocking today's HA
    #        smoke test (Rishi typed YES on the inverted default + this
    #        production-mode gate on 2026-05-14 — see PR #39 audit trail
    #        + this PR's body for full reasoning). The default is false
    #        on both flags; Day-5b's Hetzner Object Storage provisioning
    #        PR flips them both true together.
    local production_mode="${YRAL_PATRONI_PRODUCTION_MODE:-false}"
    local wal_g_enabled="${YRAL_PATRONI_WAL_G_ENABLED:-false}"
    if [[ "${production_mode}" == "true" && "${wal_g_enabled}" != "true" ]]; then
        echo "ERROR patroni-install: YRAL_PATRONI_PRODUCTION_MODE=true requires YRAL_PATRONI_WAL_G_ENABLED=true (CONSTRAINTS D2 — WAL-G is the L2 backup requirement)." >&2
        echo "  Either:" >&2
        echo "    - Set YRAL_PATRONI_WAL_G_ENABLED=true and provide the 5 S3 env vars, OR" >&2
        echo "    - Run with YRAL_PATRONI_PRODUCTION_MODE=false (dev/staging / day-of HA testing only)." >&2
        exit 1
    fi
}


confirm_data_plane_overlay_exists() {
    # WHAT:  check that node-bootstrap.sh's swarm-init phase already created
    #        yral-v2-data-plane (CONSTRAINTS C3).
    # WHEN:  third pre-flight.
    # WHY:   the stack file references this overlay as `external: true`. If
    #        it's missing, `docker stack deploy` would error mid-way. Better
    #        to fail with a clear pointer to node-bootstrap.sh first.
    if ! docker network ls --format '{{.Name}}' | grep --quiet --line-regexp yral-v2-data-plane; then
        echo "ERROR patroni-install: yral-v2-data-plane overlay missing — run node-bootstrap.sh swarm-init first" >&2
        exit 1
    fi
}


# ──────────────────── Pre-deploy setup ──────────────────────────────────────


confirm_patroni_bind_mount_directories_exist_on_each_node() {
    # WHAT:  ssh to every node as rishi-deploy and verify that BOTH of the
    #        bind-mount paths patroni-stack.yml expects exist with the
    #        right ownership:
    #          - ${PATRONI_BIND_MOUNT_HOST_PATH} — owned 101:103 (Spilo's
    #            postgres user; verified via `docker exec <patroni> id
    #            postgres` against the live ghcr.io/zalando/spilo-15:3.0-p1
    #            image, which is Debian-based and uses uid=101 gid=103 for
    #            postgres — NOT uid 999 like the official `postgres:*`
    #            Docker image; the first-attempt operator-setup used the
    #            wrong uid and Patroni's initdb failed with
    #            `could not access directory ".../pgdata/pgroot/data":
    #            Permission denied` because mode 0700 owned by uid 999
    #            blocks postgres uid 101 from traversing into the bind dir)
    #          - ${PATRONI_ETCD_BIND_MOUNT_HOST_PATH_PREFIX}${node_name}
    #            — owned 999:999 mode 0700 is fine here, the upstream
    #            quay.io/coreos/etcd image has no USER directive so the
    #            container runs as root inside, which has CAP_DAC_OVERRIDE
    #            and can traverse 0700 dirs regardless of ownership.
    # WHEN:  fourth pre-flight (after Swarm + env + overlay checks).
    # WHY:   bind mounts (not Docker volumes) per V2 infra doc §7.2.
    #        Narrow sudoers per CONSTRAINTS C8 doesn't grant rishi-deploy
    #        permission to `sudo install -d` — so creating the directory
    #        is a one-time operator setup that runs AS ROOT (during the
    #        root-window window for a fresh cluster; documented in this
    #        file's header). This function only VERIFIES the prereq and
    #        fails loud with the exact remediation command if missing.
    #        First-run Day-5 deploy missed the etcd dirs because they
    #        weren't in the original check — every etcd task entered
    #        Rejected state with "invalid mount config for type 'bind':
    #        bind source path does not exist". Now both paths are
    #        verified up-front so the same surprise can't repeat.
    local cluster_node_name
    for cluster_node_name in "${CLUSTER_NODE_NAMES[@]}"; do
        local cluster_node_ipv4
        cluster_node_ipv4="$(get_public_ipv4_for_node "${cluster_node_name}")"
        local etcd_bind_mount_host_path="${PATRONI_ETCD_BIND_MOUNT_HOST_PATH_PREFIX}${cluster_node_name}"

        # Map each bind-mount path to its expected uid:gid. Different uids
        # because Patroni's Spilo image and etcd's upstream image use
        # different in-container users (see role-comment block above).
        local -A bind_mount_path_to_expected_owner=(
            ["${PATRONI_BIND_MOUNT_HOST_PATH}"]="101:103"
            ["${etcd_bind_mount_host_path}"]="999:999"
        )

        local bind_mount_host_path
        for bind_mount_host_path in "${!bind_mount_path_to_expected_owner[@]}"; do
            local expected_ownership="${bind_mount_path_to_expected_owner[${bind_mount_host_path}]}"
            local actual_ownership
            actual_ownership="$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
                "rishi-deploy@${cluster_node_ipv4}" \
                "test -d ${bind_mount_host_path} && stat -c '%u:%g' ${bind_mount_host_path}" 2>/dev/null || echo "missing")"
            if [[ "${actual_ownership}" != "${expected_ownership}" ]]; then
                local expected_uid="${expected_ownership%:*}"
                local expected_gid="${expected_ownership#*:}"
                echo "ERROR patroni-install: ${bind_mount_host_path} on ${cluster_node_name} is '${actual_ownership}', expected '${expected_ownership}'" >&2
                echo "  Operator one-time setup (run AS ROOT, while root SSH window is open):" >&2
                echo "    ssh root@${cluster_node_ipv4} 'install -d --owner=${expected_uid} --group=${expected_gid} --mode=0700 ${bind_mount_host_path}'" >&2
                echo "  If the directory already exists with the wrong ownership (e.g. left over from an earlier attempt), chown it instead:" >&2
                echo "    ssh root@${cluster_node_ipv4} 'chown -R ${expected_uid}:${expected_gid} ${bind_mount_host_path}'" >&2
                exit 1
            fi
        done
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
    # All 5 secrets are ALWAYS created so the stack file's `external: true`
    # references always resolve. When WAL-G is disabled, the 2 S3 env vars
    # default to the non-empty `walg-disabled-placeholder` string (NOT
    # empty) because `docker secret create ... -` rejects 0-byte stdin
    # with `error reading from STDIN: data is empty`. The placeholder
    # content is harmless — Spilo never reads these secrets when
    # USE_WALG_BACKUP/RESTORE=false (set in the render step from
    # YRAL_PATRONI_USE_WALG). Matches the established pattern: WAL_BUCKET_
    # NAME below already uses `walg-disabled` as its non-empty default.
    if [[ "${YRAL_PATRONI_WAL_G_ENABLED:-false}" != "true" ]]; then
        export YRAL_HETZNER_S3_ACCESS_KEY_ID="${YRAL_HETZNER_S3_ACCESS_KEY_ID:-walg-disabled-placeholder}"
        export YRAL_HETZNER_S3_SECRET_ACCESS_KEY="${YRAL_HETZNER_S3_SECRET_ACCESS_KEY:-walg-disabled-placeholder}"
    fi
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

        # Export the resolved name into a script-scoped environment variable so
        # render_patroni_stack_compose_file_to_temporary_path can substitute
        # ${YRAL_PATRONI_STACK_RESOLVED_<UPPERCASED_BASE_NAME>}. MUST run in
        # BOTH the create-new and skip-existing branches — otherwise envsubst
        # writes an empty key into the rendered stack YAML, breaking
        # `docker stack deploy` with "yaml: line N: did not find expected key"
        # on every re-run after the first (because secrets are idempotent and
        # the second run hits the `continue` path for every existing secret).
        local resolved_export_name
        resolved_export_name="YRAL_PATRONI_STACK_RESOLVED_$(echo "${swarm_secret_base_name}" | tr '[:lower:]' '[:upper:]')"
        export "${resolved_export_name}=${fully_qualified_secret_name}"

        if docker secret inspect "${fully_qualified_secret_name}" >/dev/null 2>&1; then
            echo "patroni-install: secret ${fully_qualified_secret_name} already exists — skipping"
            continue
        fi

        printf '%s' "${secret_value}" \
            | docker secret create "${fully_qualified_secret_name}" -
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

    # Expose Spilo's WAL/S3 toggles to envsubst. The stack file reads:
    #   - ${YRAL_PATRONI_USE_WALG}                       — wal-g enable
    #   - ${YRAL_PATRONI_WALG_S3_PREFIX_RENDERED}        — wal-g prefix or ""
    #   - ${YRAL_PATRONI_S3_FORCE_PATH_STYLE_RENDERED}   — "true" or ""
    #   - ${YRAL_HETZNER_S3_REGION} / ${YRAL_HETZNER_S3_ENDPOINT}
    #   - ${YRAL_PATRONI_USE_WALE_S3_BACKUP_RENDERED}    — wal-e (older tool) on/off
    #   - ${YRAL_PATRONI_USE_WALE_GS_BACKUP_RENDERED}    — wal-e GS on/off
    #
    # When WAL-G is OFF, ALL backup-related env vars in the rendered YAML are
    # set to empty strings so Spilo's configure_spilo.py treats the
    # configuration as "no backup engine" and skips both the wal-g and the
    # wal-e standby-bootstrap paths. Day-5 deploy #7 caught the case where
    # leaving `WALG_S3_PREFIX: s3://walg-disabled/yral-v2-postgres` populated
    # caused Spilo's launcher to derive a wal-e config from it and then
    # `wale_restore.sh` hung indefinitely on `wal-e backup-list` (urllib
    # timing out against placeholder S3 credentials) before any replica
    # could fall through to pg_basebackup.
    #
    # ACCESS_KEY_ID and SECRET_ACCESS_KEY still need NON-empty placeholder
    # strings because they back Swarm secrets created in
    # create_or_rotate_swarm_secrets_with_sha8_suffix and `docker secret
    # create - <stdin>` rejects 0-byte input. Those placeholder credentials
    # never get read because the env vars that point Spilo at them
    # (WALG_S3_PREFIX, USE_WALG_*, USE_WALE_*) are all empty/false.
    if [[ "${YRAL_PATRONI_WAL_G_ENABLED:-false}" == "true" ]]; then
        export YRAL_PATRONI_USE_WALG="true"
        export YRAL_PATRONI_WALG_S3_PREFIX_RENDERED="s3://${YRAL_HETZNER_S3_WAL_BUCKET_NAME}/yral-v2-postgres"
        export YRAL_PATRONI_S3_FORCE_PATH_STYLE_RENDERED="true"
        export YRAL_PATRONI_USE_WALE_S3_BACKUP_RENDERED="true"
        export YRAL_PATRONI_USE_WALE_GS_BACKUP_RENDERED="false"
    else
        export YRAL_PATRONI_USE_WALG="false"
        export YRAL_PATRONI_WALG_S3_PREFIX_RENDERED=""
        export YRAL_PATRONI_S3_FORCE_PATH_STYLE_RENDERED=""
        export YRAL_PATRONI_USE_WALE_S3_BACKUP_RENDERED="false"
        export YRAL_PATRONI_USE_WALE_GS_BACKUP_RENDERED="false"
        export YRAL_HETZNER_S3_ACCESS_KEY_ID="${YRAL_HETZNER_S3_ACCESS_KEY_ID:-walg-disabled-placeholder}"
        export YRAL_HETZNER_S3_SECRET_ACCESS_KEY="${YRAL_HETZNER_S3_SECRET_ACCESS_KEY:-walg-disabled-placeholder}"
        export YRAL_HETZNER_S3_WAL_BUCKET_NAME=""
        export YRAL_HETZNER_S3_REGION=""
        export YRAL_HETZNER_S3_ENDPOINT=""
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


confirm_stack_actually_deployed() {
    # WHAT:  two-layer post-deploy verifier.
    #        Layer 1 (loop, ${PATRONI_DEPLOY_VERIFY_TIMEOUT_SECONDS}s
    #          total at ${PATRONI_DEPLOY_VERIFY_POLL_SECONDS}s ticks):
    #          fail loud if ANY task whose desired-state is `running`
    #          is currently in `Rejected` or `Failed` state. This
    #          catches the Day-5 deploys-2+3 bug class — tasks Swarm
    #          tries to start but can't because of bad image / bad
    #          mount / unsatisfiable constraint. Such tasks loop every
    #          ~5s so they always surface inside the 30s window.
    #        Layer 2 (single check at end of the window):
    #          fail loud if ANY service in the stack has 0 running
    #          replicas. This catches the "Swarm could not place ANY
    #          task on any node" case where no `docker stack ps` row
    #          would even exist (e.g. placement constraint matches no
    #          node, or all nodes drained).
    # WHEN:  immediately after deploy_patroni_stack_into_swarm.
    # WHY:   `docker stack deploy` exits 0 as soon as the spec is in
    #        Docker Swarm's internal raft cluster store — it does NOT
    #        wait for tasks to actually schedule, pull images, or
    #        transition to Running. Day-5 deploys 2 + 3 surfaced bugs
    #        where Swarm Rejected every task (missing bind dir,
    #        missing image tag) yet `docker stack deploy` returned 0
    #        and the script printed "✅ patroni-install finished"
    #        anyway.
    # WHAT WE DELIBERATELY DON'T DO:
    #        We do NOT wait for all replicas to reach Running. That
    #        would be wrong for Patroni — replicas legitimately spend
    #        several minutes in `Preparing` / `Starting` while
    #        `pg_basebackup` from the leader runs. Codex's review
    #        suggested making success require N/N replicas; that's
    #        the right shape for stateless services but wrong here.
    # NOTE:  same shape will port to redis-sentinel-install.sh and
    #        langfuse-install.sh in follow-up PRs; kept local to
    #        patroni-install.sh for now per A2.1 single-concern.
    local deadline_epoch=$(($(date +%s) + PATRONI_DEPLOY_VERIFY_TIMEOUT_SECONDS))
    local rejected_or_failed_tasks=""
    while [[ "$(date +%s)" -lt "${deadline_epoch}" ]]; do
        rejected_or_failed_tasks="$(
            docker stack ps "${PATRONI_STACK_NAME}" \
                --filter desired-state=running \
                --format '{{.Name}}	{{.CurrentState}}	{{.Error}}' \
                2>/dev/null \
                | awk -F'	' '$2 ~ /^(Rejected|Failed)/ {print}'
        )"
        if [[ -n "${rejected_or_failed_tasks}" ]]; then
            echo "ERROR patroni-install: ${PATRONI_STACK_NAME} has Rejected/Failed tasks (docker stack deploy returned 0 anyway):" >&2
            echo "${rejected_or_failed_tasks}" >&2
            echo "" >&2
            echo "Full stack ps for debugging:" >&2
            docker stack ps "${PATRONI_STACK_NAME}" --no-trunc >&2 || true
            exit 1
        fi
        sleep "${PATRONI_DEPLOY_VERIFY_POLL_SECONDS}"
    done

    # Layer 2: check that Swarm at least scheduled SOMETHING for every
    # service. `docker stack services` reports replicas as `R/D` where
    # R is currently-running and D is desired. We tolerate `0/N` only
    # if we never saw a Rejected/Failed task above (which would mean
    # tasks ARE being created and are in early lifecycle); a service
    # with 0/N AND no scheduling attempts visible in stack ps is the
    # "placement matches no node" failure mode.
    local zero_replica_services
    zero_replica_services="$(
        docker stack services "${PATRONI_STACK_NAME}" \
            --format '{{.Name}}	{{.Replicas}}' \
            2>/dev/null \
            | awk -F'	' '$2 ~ /^0\// {print}'
    )"
    if [[ -n "${zero_replica_services}" ]]; then
        # Cross-check: is there ANY task in stack ps for these services?
        # If not, it's the placement-matches-no-node case; fail loud.
        local service_name first_field placed_count=0
        while IFS=$'\t' read -r service_name first_field; do
            placed_count="$(
                docker stack ps "${PATRONI_STACK_NAME}" \
                    --filter "name=${service_name}" \
                    --format '{{.ID}}' 2>/dev/null \
                    | wc -l | tr -d ' '
            )"
            if [[ "${placed_count}" == "0" ]]; then
                echo "ERROR patroni-install: service ${service_name} has 0 replicas and Swarm never created any task — likely an unsatisfiable placement constraint." >&2
                echo "" >&2
                echo "Full stack services for debugging:" >&2
                docker stack services "${PATRONI_STACK_NAME}" >&2 || true
                exit 1
            fi
        done <<< "${zero_replica_services}"
    fi

    echo "patroni-install: post-deploy verifier — no Rejected/Failed tasks and every service has at least one task placed, after ${PATRONI_DEPLOY_VERIFY_TIMEOUT_SECONDS}s"
}


confirm_stack_registered_with_swarm_resync_service() {
    # WHAT:  verify ${PATRONI_STACK_NAME} appears in
    #        ${SWARM_STACK_RESYNC_REGISTRY_PATH} on every cluster node.
    # WHEN:  after deploy, last pre-completion check.
    # WHY:   per CONSTRAINTS H1, the boot-time resync service iterates this
    #        list and re-deploys each stack on reboot. Narrow sudoers per
    #        CONSTRAINTS C8 doesn't grant rishi-deploy `sudo tee --append`,
    #        so adding the line is a one-time operator setup that runs
    #        AS ROOT (documented in this file's header). This function
    #        only VERIFIES the prereq and fails loud with the exact
    #        remediation command if missing.
    local cluster_node_name
    for cluster_node_name in "${CLUSTER_NODE_NAMES[@]}"; do
        local cluster_node_ipv4
        cluster_node_ipv4="$(get_public_ipv4_for_node "${cluster_node_name}")"
        if ! ssh -o BatchMode=yes "rishi-deploy@${cluster_node_ipv4}" \
            "grep --quiet --line-regexp ${PATRONI_STACK_NAME} ${SWARM_STACK_RESYNC_REGISTRY_PATH}" 2>/dev/null; then
            echo "ERROR patroni-install: ${PATRONI_STACK_NAME} not registered in ${SWARM_STACK_RESYNC_REGISTRY_PATH} on ${cluster_node_name}" >&2
            echo "  Operator one-time setup (run AS ROOT, while root SSH window is open):" >&2
            echo "    ssh root@${cluster_node_ipv4} 'grep -q -x \"${PATRONI_STACK_NAME}\" ${SWARM_STACK_RESYNC_REGISTRY_PATH} || echo \"${PATRONI_STACK_NAME}\" >> ${SWARM_STACK_RESYNC_REGISTRY_PATH}'" >&2
            exit 1
        fi
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
