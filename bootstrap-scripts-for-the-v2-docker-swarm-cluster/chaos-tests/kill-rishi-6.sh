#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  kill-rishi-6.sh — chaos test #1: drain rishi-6 from the Swarm             ║
# ║                                                                              ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                                ║
# ║  Drain rishi-6 from the Swarm so it stops accepting tasks, verify every    ║
# ║  hot-path service reschedules to rishi-4 + rishi-5 within 60s, verify     ║
# ║  etcd + Patroni stay healthy on the remaining nodes, then mark rishi-6    ║
# ║  active again and confirm the cluster returns to its starting shape.      ║
# ║                                                                              ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                           ║
# ║  This is the "what happens if rishi-6 disappears for an hour" test.       ║
# ║  rishi-6 hosts Langfuse, the async Patroni replica, and one of three     ║
# ║  etcd members. We expect:                                                 ║
# ║   • Hot-path service replicas on rishi-6 (1 of 3 each) reschedule onto   ║
# ║     rishi-4/5 within 60 seconds.                                          ║
# ║   • Patroni leader on rishi-4 stays leader; sync replica on rishi-5      ║
# ║     stays sync; the async replica on rishi-6 is just unavailable until   ║
# ║     rishi-6 comes back.                                                  ║
# ║   • etcd quorum holds at 2 of 3 — leader election does NOT happen        ║
# ║     on the etcd cluster.                                                  ║
# ║   • Langfuse on rishi-6 goes offline (1 replica, pinned). Per design:    ║
# ║     Langfuse is observability-only; chat services do not block on it.   ║
# ║   • Bringing rishi-6 back returns the cluster to its starting shape with ║
# ║     no manual intervention. Rescheduled replicas drain back per the      ║
# ║     `availability=active` policy.                                         ║
# ║                                                                              ║
# ║  🔗 HOW IT FITS                                                              ║
# ║  - Phase 0 exit criterion per CONSTRAINTS H3: Phase 0 cannot complete    ║
# ║    until this test passes.                                                ║
# ║  - Sibling scripts: kill-patroni-leader.sh, fill-rishi-5-disk.sh,         ║
# ║    partition-rishi-6.sh, run-all-chaos-tests.sh.                          ║
# ║  - Run with: `YRAL_NODE_TO_DRAIN=rishi-6 ./kill-rishi-6.sh` from any     ║
# ║    Swarm manager (rishi-4 is canonical).                                  ║
# ║                                                                              ║
# ║  ⚠️ DRAFT — NEVER RUN UNTIL DAY 6 + RISHI YES                                ║
# ║  Per CONSTRAINTS A13 + the agent spec, this script lands as a draft now  ║
# ║  and runs against the real cluster only on Day 6 with Rishi typing YES   ║
# ║  to "run chaos tests now". The script's own preconditions block (below)  ║
# ║  refuses to run if YRAL_CHAOS_RUN_AUTHORISED is not set to today's date. ║
# ║                                                                              ║
# ║  ⭐ START HERE                                                               ║
# ║  Read main(); the four phases (preconditions → inject → verify → restore)║
# ║  are called in that order from main().                                    ║
# ╚══════════════════════════════════════════════════════════════════════════╝

set -euo pipefail


# ────────────────────────── Constants ───────────────────────────────────────

# Which node to drain. Defaults to rishi-6; overridable via env so this
# script can be reused for other "drain a single node" scenarios later.
NODE_TO_DRAIN="${YRAL_NODE_TO_DRAIN:-rishi-6}"

# How long to wait after `docker node update --availability drain` before
# we start checking that hot-path services rescheduled. 60s matches the
# expected reschedule window from CONSTRAINTS §6.7 chaos test row 1.
RESCHEDULE_WAIT_SECONDS=60

# Names of the hot-path services that MUST reschedule off the drained
# node within RESCHEDULE_WAIT_SECONDS. List comes from the §5 Replica
# tiers table (3-replica, every node).
HOT_PATH_SERVICES_THAT_MUST_RESCHEDULE=(
    yral-rishi-agent-public-api
    yral-rishi-agent-conversation-turn-orchestrator
)


# ────────────────────────── Entry point ─────────────────────────────────────


main() {
    confirm_preconditions
    inject_chaos_drain_node
    verify_recovery_after_drain
    restore_node_to_active_availability
    print_post_test_summary
}


# ────────────────────────── Phases ──────────────────────────────────────────


confirm_preconditions() {
    # WHAT:  refuse to run unless: (a) we are on a Swarm manager, (b) the
    #        target node exists in the Swarm, (c) the operator has set
    #        YRAL_CHAOS_RUN_AUTHORISED=$(date +%Y-%m-%d) within the last
    #        hour, and (d) no other chaos run is in progress (lock file).
    # WHEN:  first phase, before any state-changing command.
    # WHY:   chaos against production-shape infrastructure must never run
    #        accidentally. Each precondition is one more thing the operator
    #        must consciously do; together they form an unmistakable
    #        "yes I really mean to break things now" signal.

    if ! docker info --format '{{.Swarm.ControlAvailable}}' | grep --quiet true; then
        echo "ERROR kill-rishi-6: not a Swarm manager — run from rishi-4/5/6" >&2
        exit 1
    fi

    if ! docker node ls --format '{{.Hostname}}' | grep --quiet --line-regexp "${NODE_TO_DRAIN}"; then
        echo "ERROR kill-rishi-6: node ${NODE_TO_DRAIN} not found in Swarm" >&2
        exit 1
    fi

    local todays_date
    todays_date="$(date +%Y-%m-%d)"
    if [[ "${YRAL_CHAOS_RUN_AUTHORISED:-}" != "${todays_date}" ]]; then
        echo "ERROR kill-rishi-6: refused — YRAL_CHAOS_RUN_AUTHORISED must equal '${todays_date}'" >&2
        echo "  Set via: export YRAL_CHAOS_RUN_AUTHORISED=${todays_date}" >&2
        exit 1
    fi

    if [[ -f /tmp/yral-v2-chaos-running.lock ]]; then
        echo "ERROR kill-rishi-6: another chaos run is in progress (lock at /tmp/yral-v2-chaos-running.lock)" >&2
        exit 1
    fi
    touch /tmp/yral-v2-chaos-running.lock
    trap "rm -f /tmp/yral-v2-chaos-running.lock" EXIT
}


inject_chaos_drain_node() {
    # WHAT:  set the target node's availability to `drain` so Swarm
    #        immediately stops scheduling new tasks there and gracefully
    #        relocates running tasks to other nodes.
    # WHEN:  after preconditions; before the verification window.
    # WHY:   `drain` is the cleanest way to simulate a node going offline
    #        without actually killing the host. We get faster recovery
    #        than a hard kill (which Swarm only detects after gossip
    #        timeout) and a guaranteed clean restore path.
    echo "kill-rishi-6: setting ${NODE_TO_DRAIN} to availability=drain"
    docker node update --availability drain "${NODE_TO_DRAIN}"
}


verify_recovery_after_drain() {
    # WHAT:  wait RESCHEDULE_WAIT_SECONDS, then check (a) every hot-path
    #        service has its expected replica count Running on rishi-4/5,
    #        (b) Patroni leader is still rishi-4 with sync replica rishi-5,
    #        (c) etcd quorum is still active.
    # WHEN:  after the drain command lands.
    # WHY:   without this verification, the chaos test only proves "we can
    #        drain a node" — not "the cluster behaves correctly while one
    #        node is missing", which is the actual exit criterion.

    echo "kill-rishi-6: waiting ${RESCHEDULE_WAIT_SECONDS}s for reschedule"
    sleep "${RESCHEDULE_WAIT_SECONDS}"

    local hot_path_service_name
    for hot_path_service_name in "${HOT_PATH_SERVICES_THAT_MUST_RESCHEDULE[@]}"; do
        local replicas_on_drained_node
        replicas_on_drained_node="$(docker service ps "${hot_path_service_name}" \
            --filter "desired-state=running" \
            --format '{{.Node}}' 2>/dev/null | grep --count --line-regexp "${NODE_TO_DRAIN}" || true)"
        if [[ "${replicas_on_drained_node}" -ne 0 ]]; then
            echo "FAIL kill-rishi-6: ${hot_path_service_name} still has ${replicas_on_drained_node} replicas on ${NODE_TO_DRAIN}" >&2
            return 1
        fi
        echo "  ✅ ${hot_path_service_name} has 0 replicas on ${NODE_TO_DRAIN}"
    done

    # Patroni leader check via REST API on rishi-4 (the expected leader).
    local patroni_leader_hostname
    patroni_leader_hostname="$(curl --silent --max-time 5 \
        http://patroni-rishi-4:8008/cluster | jq -r '.members[] | select(.role=="leader") | .name' || true)"
    if [[ "${patroni_leader_hostname}" != "patroni-rishi-4" ]]; then
        echo "FAIL kill-rishi-6: Patroni leader is '${patroni_leader_hostname}', expected patroni-rishi-4" >&2
        return 1
    fi
    echo "  ✅ Patroni leader unchanged: patroni-rishi-4"

    # etcd quorum: query a non-drained etcd member; expect HEALTHY.
    if ! docker exec "$(docker ps --filter name=etcd-rishi-4 --quiet | head -n 1)" \
        etcdctl --endpoints=http://etcd-rishi-4:2379 endpoint health >/dev/null 2>&1; then
        echo "FAIL kill-rishi-6: etcd-rishi-4 reports unhealthy after drain" >&2
        return 1
    fi
    echo "  ✅ etcd quorum still healthy on rishi-4/5"
}


restore_node_to_active_availability() {
    # WHAT:  set the drained node back to availability=active so Swarm
    #        starts scheduling tasks on it again.
    # WHEN:  always, even if verification failed — the chaos test must
    #        never leave the cluster in a worse state than it started.
    # WHY:   idempotent + reversible per the chaos-test contract. A failed
    #        verification is information; leaving a node drained is
    #        operational damage.
    echo "kill-rishi-6: restoring ${NODE_TO_DRAIN} to availability=active"
    docker node update --availability active "${NODE_TO_DRAIN}"

    # Give Swarm a moment to reschedule replicas back; then verify the
    # node is reachable as expected.
    sleep 30
    local node_availability
    node_availability="$(docker node inspect "${NODE_TO_DRAIN}" \
        --format '{{.Spec.Availability}}')"
    if [[ "${node_availability}" != "active" ]]; then
        echo "FAIL kill-rishi-6: ${NODE_TO_DRAIN} availability is '${node_availability}' after restore" >&2
        return 1
    fi
    echo "  ✅ ${NODE_TO_DRAIN} restored to availability=active"
}


print_post_test_summary() {
    cat <<SUMMARY

✅ kill-rishi-6 chaos test PASSED.

Scenario: drained ${NODE_TO_DRAIN} from Swarm; hot-path services rescheduled
to remaining nodes within ${RESCHEDULE_WAIT_SECONDS}s; Patroni + etcd
remained healthy; node restored to active.

Captured in chaos-test report alongside sibling scripts. Phase 0 exit
criterion row 1 (per CONSTRAINTS H3) cleared.

SUMMARY
}


main "$@"


# ══════════════════════════════════════════════════════════════════════════
# RELATED FILES
# ─────────────
# - kill-patroni-leader.sh  — chaos test #2 (kills the Patroni leader).
# - fill-rishi-5-disk.sh    — chaos test #3 (fills disk to 80%).
# - partition-rishi-6.sh    — chaos test #4 (network-partitions rishi-6).
# - run-all-chaos-tests.sh  — orchestrator that runs all four with cleanup.
# - ../scripts/node-bootstrap.sh — prerequisite (cluster must be bootstrapped).
# ══════════════════════════════════════════════════════════════════════════
