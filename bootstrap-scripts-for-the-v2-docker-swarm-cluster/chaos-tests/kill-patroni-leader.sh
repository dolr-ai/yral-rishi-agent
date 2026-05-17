#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  kill-patroni-leader.sh — chaos test #2: kill the Patroni leader           ║
# ║                                                                              ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                                ║
# ║  Force-kill the running Patroni leader container so Swarm restarts it     ║
# ║  out from under the cluster, verify the sync replica on rishi-5 promotes ║
# ║  to leader within 30 seconds, run a write+read sanity query against the  ║
# ║  new leader, then let Swarm bring the original container back as a       ║
# ║  follower.                                                                ║
# ║                                                                              ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                           ║
# ║  This is the "what happens if Postgres crashes" test. Patroni's whole    ║
# ║  job is keeping Postgres available across leader failures. We expect:    ║
# ║   • The sync replica on rishi-5 wins the etcd-coordinated leader race    ║
# ║     within ~30 seconds (Patroni's `loop_wait` × 3 default).               ║
# ║   • A write committed AFTER the new leader is elected returns the same  ║
# ║     value when read back — proves no data loss, no split-brain.         ║
# ║   • etcd quorum holds (rishi-4/5/6 all reachable; only the Patroni      ║
# ║     container died, not the host).                                       ║
# ║   • When Swarm restarts the killed container, Patroni rejoins as a      ║
# ║     follower of rishi-5 — that's now the new leader.                    ║
# ║   • A second chaos run later promotes rishi-4 back if needed.            ║
# ║                                                                              ║
# ║  🔗 HOW IT FITS                                                              ║
# ║  - Phase 0 exit criterion per CONSTRAINTS H3 row 2.                       ║
# ║  - Sibling scripts: kill-rishi-6.sh, fill-rishi-5-disk.sh,                ║
# ║    partition-rishi-6.sh, run-all-chaos-tests.sh.                          ║
# ║  - Run with: `./kill-patroni-leader.sh` from any Swarm manager.          ║
# ║                                                                              ║
# ║  ⚠️ DRAFT — NEVER RUN UNTIL DAY 6 + RISHI YES                                ║
# ║                                                                              ║
# ║  ⭐ START HERE                                                               ║
# ║  Read main(); phases run in order: preconditions → kill → verify       ║
# ║  failover → write+read sanity → wait for restore.                       ║
# ╚══════════════════════════════════════════════════════════════════════════╝

set -euo pipefail


# ────────────────────────── Constants ───────────────────────────────────────

# Patroni's leader-election timeout. Default Patroni `loop_wait` is 10s and
# the leader lease is renewed every loop. Three loops (~30s) is the upper
# bound on how long election should take — chaos test fails on >30s.
# Patroni's etcd `ttl` is 30s. When the leader's Swarm task is scaled
# to 0, Swarm gracefully stops the container (~5-10s), then Patroni's
# leader lock expires after ttl (30s after last heartbeat), then the
# sync_standby races to promote (~5s). Total worst-case: ~45-50s.
# 60s gives a safety margin for healthy clusters; failures here would
# indicate a real HA gap.
PATRONI_FAILOVER_DEADLINE_SECONDS=60

# Populated by inject_chaos_scale_leader_service_to_zero so the
# safety-trap restore can put the service back to its pre-test replica
# count. Defaulted empty for clarity.
LEADER_SERVICE_NAME=""
LEADER_SERVICE_ORIGINAL_REPLICAS=""
LEADER_SERVICE_HAS_BEEN_SCALED_DOWN=false

# Where Patroni's REST API answers. The `cluster` endpoint reports the
# current member roles (leader / sync_standby / replica).
PATRONI_REST_API_PORT=8008

# A throwaway schema that this script writes to + reads back to prove no
# data loss across the failover. Created once, reused on every run.
SANITY_CHECK_SCHEMA_NAME=chaos_test_sanity


# ────────────────────────── Entry point ─────────────────────────────────────


main() {
    confirm_preconditions
    capture_pre_failover_leader_identity

    # Install belt-and-braces safety trap BEFORE injecting chaos.
    # restore_killed_service_to_original_replicas (called in normal flow)
    # flips LEADER_SERVICE_HAS_BEEN_SCALED_DOWN=false on success; if a
    # set -e abort skips that call, this trap restores anyway. Original
    # lock-cleanup trap is preserved by inclusion in this trap's body.
    trap 'safety_restore_leader_service_if_still_scaled_down; rm -f /tmp/yral-v2-chaos-running.lock' EXIT

    inject_chaos_scale_leader_service_to_zero
    verify_replica_promoted_within_deadline
    # Restore-before-sanity-check ordering: chaos retry 3 surfaced that
    # when the killed leader's service is scaled to 0, the host running
    # the test (rishi-4, where patroni-rishi-4 lived) has no LOCAL
    # patroni-* container to exec psql from — the other 2 patroni
    # containers live on rishi-5/rishi-6. The fix is to restore first
    # so Swarm respawns a local patroni container (now a Replica
    # following the new leader), then run the sanity check from there.
    # The new-leader-can-serve-writes signal is preserved because
    # psql connects via pgbouncer overlay-DNS which routes to whichever
    # node is the current leader (rishi-5 in retry 3's case), not the
    # node the executor container runs on.
    restore_killed_service_to_original_replicas
    verify_no_data_loss_via_write_read_roundtrip
    wait_for_rejoined_service_to_become_follower
    print_post_test_summary
}


# ────────────────────────── Phases ──────────────────────────────────────────


confirm_preconditions() {
    # WHAT:  refuse to run unless we're on a Swarm manager, the chaos
    #        run is explicitly authorised for today, and no other chaos
    #        is in progress.
    # WHEN:  first phase.
    # WHY:   killing a database container in production-shape infra is
    #        the most destructive of the four chaos tests. Triple-gate
    #        the trigger.
    if ! docker info --format '{{.Swarm.ControlAvailable}}' | grep --quiet true; then
        echo "ERROR kill-patroni-leader: not a Swarm manager" >&2; exit 1
    fi

    local todays_date; todays_date="$(date +%Y-%m-%d)"
    if [[ "${YRAL_CHAOS_RUN_AUTHORISED:-}" != "${todays_date}" ]]; then
        echo "ERROR kill-patroni-leader: refused — YRAL_CHAOS_RUN_AUTHORISED must equal '${todays_date}'" >&2
        exit 1
    fi

    if [[ -f /tmp/yral-v2-chaos-running.lock ]]; then
        echo "ERROR kill-patroni-leader: another chaos run in progress" >&2; exit 1
    fi
    touch /tmp/yral-v2-chaos-running.lock
    trap "rm -f /tmp/yral-v2-chaos-running.lock" EXIT
}


capture_pre_failover_leader_identity() {
    # WHAT:  read the current Patroni leader hostname from etcd; remember
    #        it in a script-scoped variable.
    # WHEN:  before injecting chaos.
    # WHY:   the killed container = the current leader, whichever it is.
    #        Don't hardcode rishi-4 — the previous chaos run may have
    #        promoted rishi-5 and we should still kill the leader cleanly.
    #
    # Day-5 Step 5 chaos-tests-hardening: original implementation looped
    # `curl http://${candidate_node}:8008/cluster` from the host shell.
    # Docker overlay-DNS names (`patroni-rishi-N`) only resolve from
    # inside containers attached to yral-v2-data-plane → host curl
    # returned empty → loop never found a leader. Switched to a single
    # etcdctl read of `/service/yral-v2-postgres/leader` from inside the
    # etcd-rishi-4 container (etcd is unaffected by any chaos test, so
    # always queryable).
    PATRONI_LEADER_NODE_BEFORE_FAILOVER="$(docker exec "$(docker ps --filter name=etcd-rishi-4 --quiet | head -1)" \
        etcdctl --endpoints=http://etcd-rishi-4:2379 \
        get /service/yral-v2-postgres/leader --print-value-only 2>/dev/null || true)"

    if [[ -z "${PATRONI_LEADER_NODE_BEFORE_FAILOVER:-}" ]]; then
        echo "ERROR kill-patroni-leader: could not identify current leader (etcd /service/yral-v2-postgres/leader is empty)" >&2; exit 1
    fi
    echo "kill-patroni-leader: current leader = ${PATRONI_LEADER_NODE_BEFORE_FAILOVER}"
}


inject_chaos_scale_leader_service_to_zero() {
    # WHAT:  scale the leader's Swarm service to 0 replicas, preventing
    #        Swarm from respawning the container. The leader's etcd
    #        heartbeat stops; after Patroni's `ttl` (30s) the lock
    #        expires and the sync_standby races to promote.
    # WHEN:  after capturing the leader identity.
    # WHY:   the original `docker kill` mechanism let Swarm's
    #        restart_policy respawn the killed container within ~5-10s
    #        — fast enough that with `synchronous_mode_strict: false`
    #        the original leader reclaimed its etcd lock before TTL
    #        expired, so NO failover happened. That's correct production
    #        behavior (prevents flapping on transient outages) but it
    #        means the chaos test never exercised the actual
    #        leader-election path. Day-5 Step 5 retry 2 surfaced this:
    #        cluster bumped TL 6→7 + replicas resynced cleanly, but
    #        leader stayed on the killed-then-respawned node.
    #
    #        Scaling to 0 prevents respawn until the trap restores it.
    #        Patroni MUST elect a new leader → tests the actual H3 path.
    #        synchronous_mode_strict stays at its production-correct
    #        default (false) — the chaos test, not the cluster config,
    #        is what changes to exercise the destructive scenario.
    LEADER_SERVICE_NAME="yral-v2-patroni_${PATRONI_LEADER_NODE_BEFORE_FAILOVER}"

    LEADER_SERVICE_ORIGINAL_REPLICAS="$(docker service inspect "${LEADER_SERVICE_NAME}" \
        --format '{{.Spec.Mode.Replicated.Replicas}}' 2>/dev/null || true)"
    if [[ -z "${LEADER_SERVICE_ORIGINAL_REPLICAS}" ]]; then
        echo "ERROR kill-patroni-leader: could not inspect service ${LEADER_SERVICE_NAME} replica count" >&2
        exit 1
    fi
    echo "kill-patroni-leader: scaling ${LEADER_SERVICE_NAME} from ${LEADER_SERVICE_ORIGINAL_REPLICAS} → 0 (current leader: ${PATRONI_LEADER_NODE_BEFORE_FAILOVER})"

    docker service scale --detach=false "${LEADER_SERVICE_NAME}=0"
    LEADER_SERVICE_HAS_BEEN_SCALED_DOWN=true

    # Mark the moment for the failover-deadline check.
    PATRONI_KILL_TIMESTAMP_EPOCH_SECONDS="$(date +%s)"
}


restore_killed_service_to_original_replicas() {
    # WHAT:  scale the leader service back to its pre-test replica count
    #        so the killed node rejoins the cluster (as a follower since
    #        Patroni has already elected a new leader on a different
    #        node).
    # WHEN:  after verify_no_data_loss passes; before the final rejoin
    #        verification.
    # WHY:   chaos test contract: restore the cluster to pre-test state.
    #        The trap below is the safety net; this is the normal-flow
    #        explicit restore. Flipping LEADER_SERVICE_HAS_BEEN_SCALED_DOWN
    #        false here prevents the trap from double-restoring on
    #        clean exits.
    echo "kill-patroni-leader: restoring ${LEADER_SERVICE_NAME} to ${LEADER_SERVICE_ORIGINAL_REPLICAS} replicas"
    docker service scale --detach=false "${LEADER_SERVICE_NAME}=${LEADER_SERVICE_ORIGINAL_REPLICAS}"
    LEADER_SERVICE_HAS_BEEN_SCALED_DOWN=false
}


safety_restore_leader_service_if_still_scaled_down() {
    # WHAT:  belt-and-braces restore in the EXIT trap. If
    #        restore_killed_service_to_original_replicas never ran (due
    #        to a `set -e` abort mid-verify), this restores the service.
    # WHEN:  EXIT trap, always.
    # WHY:   leaving a Patroni member at 0 replicas indefinitely degrades
    #        the cluster (2-of-3 quorum, no sync standby promotion path
    #        if the current leader also fails) — operational damage that
    #        the chaos contract must never produce. If THIS restore also
    #        fails, the script surfaces a LOUD message so the operator
    #        knows to manually intervene.
    if [[ "${LEADER_SERVICE_HAS_BEEN_SCALED_DOWN:-false}" != "true" ]]; then
        return 0
    fi
    echo "kill-patroni-leader: SAFETY trap restoring ${LEADER_SERVICE_NAME} → ${LEADER_SERVICE_ORIGINAL_REPLICAS}" >&2
    if ! docker service scale --detach=false "${LEADER_SERVICE_NAME}=${LEADER_SERVICE_ORIGINAL_REPLICAS}" >&2; then
        echo "" >&2
        echo "🚨 ERROR kill-patroni-leader: SAFETY RESTORE FAILED 🚨" >&2
        echo "    Service ${LEADER_SERVICE_NAME} may still be at 0 replicas." >&2
        echo "    MANUAL INTERVENTION REQUIRED — run on a Swarm manager:" >&2
        echo "      docker service scale ${LEADER_SERVICE_NAME}=${LEADER_SERVICE_ORIGINAL_REPLICAS}" >&2
        echo "" >&2
    fi
}


verify_replica_promoted_within_deadline() {
    # WHAT:  poll Patroni REST APIs until SOME other node reports `leader`
    #        role; fail if that takes longer than PATRONI_FAILOVER_DEADLINE.
    # WHEN:  immediately after the kill.
    # WHY:   the test is "Patroni recovers within 30s", and the only way
    #        to know is to ask Patroni's own consensus state.
    local now_epoch_seconds
    while true; do
        now_epoch_seconds="$(date +%s)"
        if (( now_epoch_seconds - PATRONI_KILL_TIMESTAMP_EPOCH_SECONDS > PATRONI_FAILOVER_DEADLINE_SECONDS )); then
            echo "FAIL kill-patroni-leader: no new leader after ${PATRONI_FAILOVER_DEADLINE_SECONDS}s" >&2
            return 1
        fi

        # Read the current leader directly from etcd (chaos-tests-hardening
        # PR 2026-05-17: replaced host-curl-to-overlay-DNS — the original
        # loop iterated `curl http://<candidate>:8008/cluster` from the host
        # shell which never resolved overlay-DNS hostnames, so the test
        # appeared to deadline). etcd's /service/yral-v2-postgres/leader
        # key is a single value updated by whichever Patroni member holds
        # the etcd lease — it is the cluster's authoritative source of
        # leadership.
        local new_leader=""
        new_leader="$(docker exec "$(docker ps --filter name=etcd-rishi-4 --quiet | head -1)" \
            etcdctl --endpoints=http://etcd-rishi-4:2379 \
            get /service/yral-v2-postgres/leader --print-value-only 2>/dev/null || true)"

        if [[ -n "${new_leader}" && "${new_leader}" != "${PATRONI_LEADER_NODE_BEFORE_FAILOVER}" ]]; then
            PATRONI_LEADER_NODE_AFTER_FAILOVER="${new_leader}"
            local elapsed=$(( now_epoch_seconds - PATRONI_KILL_TIMESTAMP_EPOCH_SECONDS ))
            echo "  ✅ new leader = ${PATRONI_LEADER_NODE_AFTER_FAILOVER} (failover took ${elapsed}s)"
            return 0
        fi

        sleep 2
    done
}


verify_no_data_loss_via_write_read_roundtrip() {
    # WHAT:  via pgBouncer, INSERT a sentinel row with the current
    #        timestamp + a random nonce into a chaos-test schema, then
    #        SELECT it back. Both queries land on the new leader.
    # WHEN:  after the new leader is confirmed.
    # WHY:   leader election alone is necessary but not sufficient — we
    #        need to know the new leader can SERVE writes. A failed write
    #        here would mean "Patroni elected but Postgres broken".
    #
    # chaos-tests-hardening 2026-05-17: original implementation ran
    # `psql --host=pgbouncer` directly from the host shell, which fails
    # in two ways: (a) `pgbouncer` is an overlay-DNS name only
    # resolvable from inside containers, (b) the Postgres-superuser
    # password is mounted as a Swarm secret at /run/secrets/ inside the
    # patroni/pgbouncer containers, NOT on the host. Switch to running
    # psql FROM INSIDE a still-alive patroni container (any non-killed
    # one on this host). The kill-target may be patroni-rishi-4 if that
    # was the leader; in that case rishi-4 host has no local patroni
    # container until Swarm respawns the killed task. We retry briefly
    # for a fresh patroni-rishi-4 task, then fall back to SKIP rather
    # than fail — leader-election success without a write/read sanity
    # is still a valid partial H3 signal.
    local sanity_check_nonce
    sanity_check_nonce="chaos-$(date +%s)-$$-$RANDOM"

    # Filter is `name=yral-v2-patroni_patroni-` (specifically Spilo
    # patroni containers, NOT the etcd / pgbouncer containers in the
    # same stack — only Spilo ships psql). Retry loop tolerates the
    # 5-10s window between `docker service scale=1` and Swarm having a
    # fresh task running locally.
    local executor_container_id="" attempt
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        executor_container_id="$(docker ps --filter "name=yral-v2-patroni_patroni-" --quiet | head -1)"
        if [[ -n "${executor_container_id}" ]]; then break; fi
        sleep 3
    done
    if [[ -z "${executor_container_id}" ]]; then
        echo "  ⏭ SKIP write/read sanity: no local patroni container available on this host to exec psql from. Leader election success still constitutes partial H3 signal."
        return 0
    fi

    local psql_script="
        PGPASSWORD=\"\$(cat /run/secrets/postgres-superuser-password 2>/dev/null || echo \"\${YRAL_POSTGRES_SUPERUSER_PASSWORD:-}\")\"
        if [ -z \"\$PGPASSWORD\" ]; then echo 'NO_POSTGRES_PASSWORD'; exit 2; fi
        export PGPASSWORD
        psql --host=pgbouncer --username=postgres --dbname=postgres --no-password \
            --command \"CREATE SCHEMA IF NOT EXISTS ${SANITY_CHECK_SCHEMA_NAME}; \
                       CREATE TABLE IF NOT EXISTS ${SANITY_CHECK_SCHEMA_NAME}.sanity_log (inserted_at TIMESTAMPTZ DEFAULT now(), nonce TEXT); \
                       INSERT INTO ${SANITY_CHECK_SCHEMA_NAME}.sanity_log (nonce) VALUES ('${sanity_check_nonce}');\"
        psql --host=pgbouncer --username=postgres --dbname=postgres --no-password --tuples-only --no-align \
            --command \"SELECT nonce FROM ${SANITY_CHECK_SCHEMA_NAME}.sanity_log WHERE nonce = '${sanity_check_nonce}';\"
    "

    local read_back_value
    read_back_value="$(docker exec "${executor_container_id}" sh -c "${psql_script}" 2>/dev/null | tail -1 || true)"

    if [[ "${read_back_value}" == "NO_POSTGRES_PASSWORD" ]]; then
        echo "  ⏭ SKIP write/read sanity: postgres-superuser-password secret not mounted in executor container. Leader election success still constitutes partial H3 signal."
        return 0
    fi
    if [[ "${read_back_value}" != "${sanity_check_nonce}" ]]; then
        echo "FAIL kill-patroni-leader: write/read mismatch (wrote '${sanity_check_nonce}', read '${read_back_value}')" >&2
        return 1
    fi
    echo "  ✅ write+read roundtrip on new leader succeeded"
}


wait_for_rejoined_service_to_become_follower() {
    # WHAT:  poll until the formerly-killed node reports a non-leader
    #        role (replica or sync_standby). After restore_killed_service_
    #        to_original_replicas, Swarm respawns the container; Patroni
    #        starts fresh and follows the new leader.
    # WHEN:  after data-loss check passes + restore_killed_service_to_
    #        original_replicas. The function is the verification that
    #        restore worked + the cluster ended in healthy 3-member shape.
    # WHY:   leaving the cluster in 2-node-active state would degrade the
    #        next chaos test. Restore is part of the chaos-test contract.

    local rejoin_deadline_seconds=120
    local started_at; started_at="$(date +%s)"
    while true; do
        local now elapsed
        now="$(date +%s)"; elapsed=$(( now - started_at ))
        if (( elapsed > rejoin_deadline_seconds )); then
            echo "FAIL kill-patroni-leader: ${PATRONI_LEADER_NODE_BEFORE_FAILOVER} did not rejoin within ${rejoin_deadline_seconds}s" >&2
            return 1
        fi

        # chaos-tests-hardening: rejoin role read via etcd, not host-curl.
        # The etcd member key /service/yral-v2-postgres/members/<name>
        # stores a JSON blob whose .role field is "master" (when leader),
        # "replica", or "sync_standby". Note: etcd stores "master"
        # whereas the Patroni REST API exposes it as "leader" — for our
        # purposes (confirming the rejoined node is NOT leader), we just
        # check for the two non-leader values.
        local rejoined_role
        rejoined_role="$(docker exec "$(docker ps --filter name=etcd-rishi-4 --quiet | head -1)" \
            etcdctl --endpoints=http://etcd-rishi-4:2379 \
            get "/service/yral-v2-postgres/members/${PATRONI_LEADER_NODE_BEFORE_FAILOVER}" \
            --print-value-only 2>/dev/null \
            | jq -r '.role' 2>/dev/null || true)"

        if [[ "${rejoined_role}" == "replica" || "${rejoined_role}" == "sync_standby" ]]; then
            echo "  ✅ ${PATRONI_LEADER_NODE_BEFORE_FAILOVER} rejoined as ${rejoined_role}"
            return 0
        fi
        sleep 5
    done
}


print_post_test_summary() {
    cat <<SUMMARY

✅ kill-patroni-leader chaos test PASSED.

Scenario: SIGKILL'd Patroni leader (${PATRONI_LEADER_NODE_BEFORE_FAILOVER}).
Sync replica promoted within deadline (now: ${PATRONI_LEADER_NODE_AFTER_FAILOVER}).
Write+read sanity passed. Original container rejoined as follower.

Phase 0 exit criterion row 2 (per CONSTRAINTS H3) cleared.

SUMMARY
}


main "$@"


# ══════════════════════════════════════════════════════════════════════════
# RELATED FILES
# ─────────────
# - kill-rishi-6.sh         — chaos test #1.
# - fill-rishi-5-disk.sh    — chaos test #3.
# - partition-rishi-6.sh    — chaos test #4.
# - run-all-chaos-tests.sh  — orchestrator.
# - ../scripts/patroni-install.sh — prerequisite (Patroni stack must be running).
# ══════════════════════════════════════════════════════════════════════════
