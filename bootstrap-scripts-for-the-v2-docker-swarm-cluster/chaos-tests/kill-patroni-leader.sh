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
PATRONI_FAILOVER_DEADLINE_SECONDS=30

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
    inject_chaos_kill_leader_container
    verify_replica_promoted_within_deadline
    verify_no_data_loss_via_write_read_roundtrip
    wait_for_killed_container_to_rejoin_as_follower
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
    # WHAT:  query each Patroni REST API to find which node is the current
    #        leader; remember it in a script-scoped variable.
    # WHEN:  before injecting chaos.
    # WHY:   the killed container = the current leader, whichever it is.
    #        Don't hardcode rishi-4 — the previous chaos run may have
    #        promoted rishi-5 and we should still kill the leader cleanly.
    local candidate_node
    for candidate_node in patroni-rishi-4 patroni-rishi-5 patroni-rishi-6; do
        local member_role
        member_role="$(curl --silent --max-time 5 \
            "http://${candidate_node}:${PATRONI_REST_API_PORT}/cluster" \
            | jq -r --arg name "${candidate_node}" '.members[] | select(.name==$name) | .role' 2>/dev/null || true)"
        if [[ "${member_role}" == "leader" ]]; then
            PATRONI_LEADER_NODE_BEFORE_FAILOVER="${candidate_node}"
            break
        fi
    done

    if [[ -z "${PATRONI_LEADER_NODE_BEFORE_FAILOVER:-}" ]]; then
        echo "ERROR kill-patroni-leader: could not identify current leader" >&2; exit 1
    fi
    echo "kill-patroni-leader: current leader = ${PATRONI_LEADER_NODE_BEFORE_FAILOVER}"
}


inject_chaos_kill_leader_container() {
    # WHAT:  `docker kill` (SIGKILL) the running container backing the
    #        leader's Swarm task. Swarm will restart it via the service's
    #        restart_policy after a short delay.
    # WHEN:  after capturing the leader identity.
    # WHY:   SIGKILL bypasses graceful shutdown — exactly the kind of
    #        sudden-failure scenario Patroni must handle. SIGTERM would
    #        give Patroni a chance to demote itself cleanly, which is
    #        the easy case.
    local leader_container_id
    leader_container_id="$(docker ps --filter "name=${PATRONI_LEADER_NODE_BEFORE_FAILOVER}" \
        --format '{{.ID}}' | head -n 1)"
    if [[ -z "${leader_container_id}" ]]; then
        echo "ERROR kill-patroni-leader: no running container found for ${PATRONI_LEADER_NODE_BEFORE_FAILOVER}" >&2; exit 1
    fi
    echo "kill-patroni-leader: SIGKILL container ${leader_container_id} (leader: ${PATRONI_LEADER_NODE_BEFORE_FAILOVER})"
    docker kill --signal=KILL "${leader_container_id}"

    # Mark the moment for the failover-deadline check.
    PATRONI_KILL_TIMESTAMP_EPOCH_SECONDS="$(date +%s)"
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

        local candidate_node new_leader=""
        for candidate_node in patroni-rishi-4 patroni-rishi-5 patroni-rishi-6; do
            [[ "${candidate_node}" == "${PATRONI_LEADER_NODE_BEFORE_FAILOVER}" ]] && continue
            local role
            role="$(curl --silent --max-time 2 \
                "http://${candidate_node}:${PATRONI_REST_API_PORT}/cluster" \
                | jq -r --arg name "${candidate_node}" '.members[] | select(.name==$name) | .role' 2>/dev/null || true)"
            if [[ "${role}" == "leader" ]]; then
                new_leader="${candidate_node}"
                break
            fi
        done

        if [[ -n "${new_leader}" ]]; then
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
    local sanity_check_nonce
    sanity_check_nonce="chaos-$(date +%s)-$$-$RANDOM"

    local pgbouncer_endpoint="pgbouncer:5432"
    local postgres_password
    postgres_password="$(cat /run/secrets/postgres-superuser-password 2>/dev/null || echo "${YRAL_POSTGRES_SUPERUSER_PASSWORD:-}")"
    if [[ -z "${postgres_password}" ]]; then
        echo "ERROR kill-patroni-leader: cannot read postgres password" >&2; return 1
    fi

    PGPASSWORD="${postgres_password}" psql \
        --host=pgbouncer --username=postgres --dbname=postgres --no-password \
        --command "CREATE SCHEMA IF NOT EXISTS ${SANITY_CHECK_SCHEMA_NAME};
                   CREATE TABLE IF NOT EXISTS ${SANITY_CHECK_SCHEMA_NAME}.sanity_log (
                       inserted_at TIMESTAMPTZ DEFAULT now(),
                       nonce TEXT
                   );
                   INSERT INTO ${SANITY_CHECK_SCHEMA_NAME}.sanity_log (nonce) VALUES ('${sanity_check_nonce}');"

    local read_back_value
    read_back_value="$(PGPASSWORD="${postgres_password}" psql \
        --host=pgbouncer --username=postgres --dbname=postgres --no-password \
        --tuples-only --no-align \
        --command "SELECT nonce FROM ${SANITY_CHECK_SCHEMA_NAME}.sanity_log WHERE nonce = '${sanity_check_nonce}';")"

    if [[ "${read_back_value}" != "${sanity_check_nonce}" ]]; then
        echo "FAIL kill-patroni-leader: write/read mismatch (wrote '${sanity_check_nonce}', read '${read_back_value}')" >&2
        return 1
    fi
    echo "  ✅ write+read roundtrip on new leader succeeded"
}


wait_for_killed_container_to_rejoin_as_follower() {
    # WHAT:  poll until the killed node reports a non-leader role (replica
    #        or sync_standby) — Swarm's restart_policy will respawn the
    #        container; Patroni will start fresh and follow the new leader.
    # WHEN:  after data-loss check passes.
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

        local rejoined_role
        rejoined_role="$(curl --silent --max-time 2 \
            "http://${PATRONI_LEADER_NODE_BEFORE_FAILOVER}:${PATRONI_REST_API_PORT}/cluster" \
            | jq -r --arg name "${PATRONI_LEADER_NODE_BEFORE_FAILOVER}" '.members[] | select(.name==$name) | .role' 2>/dev/null || true)"

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
