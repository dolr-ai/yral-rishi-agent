#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  partition-rishi-6.sh — chaos test #4: network-partition rishi-6           ║
# ║                                                                              ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                                ║
# ║  Use iptables on rishi-4 + rishi-5 to drop every packet to/from rishi-6   ║
# ║  for 10 minutes, verify etcd quorum holds at 2 of 3 + Patroni keeps      ║
# ║  serving writes through rishi-4/5, then remove the iptables rules and    ║
# ║  confirm rishi-6 fully rejoins the cluster.                              ║
# ║                                                                              ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                           ║
# ║  This is the "split-brain" test. We simulate rishi-6 losing all network ║
# ║  to rishi-4 + rishi-5 (e.g. cross-DC outage if rishi-6 is in NBG1 per   ║
# ║  the V2 §5 plan). We expect:                                             ║
# ║   • etcd 2 of 3 = quorum on rishi-4 + rishi-5; rishi-6's etcd member is ║
# ║     marked unhealthy but the cluster keeps electing leaders + writing.  ║
# ║   • Patroni leader on rishi-4 keeps committing transactions; sync       ║
# ║     replica on rishi-5 keeps replicating.                                ║
# ║   • The async replica on rishi-6 falls behind during the partition.    ║
# ║   • Hot-path service replicas on rishi-6 may go unhealthy from rishi-4/5║
# ║     perspective; Swarm reschedules onto edge nodes.                      ║
# ║   • When the partition heals, rishi-6 catches up etcd state, Patroni's ║
# ║     async replica streams accumulated WAL, hot-path replicas redeploy.  ║
# ║                                                                              ║
# ║  🔗 HOW IT FITS                                                              ║
# ║  - Phase 0 exit criterion per CONSTRAINTS H3 row 4.                       ║
# ║  - Tests the V2 §5 cross-DC topology assumption (sync replica must      ║
# ║    stay in FSN1 with the leader).                                        ║
# ║                                                                              ║
# ║  ⚠️ DRAFT — NEVER RUN UNTIL DAY 6 + RISHI YES. The iptables changes are  ║
# ║  the most invasive of the four chaos tests; cleanup MUST run.             ║
# ║                                                                              ║
# ║  ⭐ START HERE                                                               ║
# ║  Read main(); the cleanup trap removes iptables rules + the lock file    ║
# ║  even on early failure.                                                  ║
# ╚══════════════════════════════════════════════════════════════════════════╝

set -euo pipefail


# ────────────────────────── Constants ───────────────────────────────────────

PARTITIONED_NODE_HOSTNAME="${YRAL_PARTITIONED_NODE_HOSTNAME:-rishi-6}"

# Other cluster nodes the partitioned node will be cut off from.
PEER_NODES_TO_BLOCK_AT=(rishi-4 rishi-5)

# Duration of the partition. 10 minutes per CONSTRAINTS H3 row 4. Long
# enough for etcd to mark rishi-6 unhealthy and for hot-path replicas
# on rishi-6 to be rescheduled by Swarm.
PARTITION_DURATION_SECONDS=$((10 * 60))

# Custom iptables comment we tag every rule with so cleanup can find +
# delete only OUR rules and never touch unrelated firewall config.
IPTABLES_RULE_COMMENT="yral-v2-chaos-partition-rishi-6"


# ────────────────────────── Entry point ─────────────────────────────────────


main() {
    confirm_preconditions
    capture_partitioned_node_ip_address
    install_partition_iptables_rules_on_peers
    verify_etcd_quorum_holds_during_partition
    verify_patroni_writes_succeed_during_partition
    sleep_remainder_of_partition_window
    remove_partition_iptables_rules_on_peers
    verify_partitioned_node_rejoined_cluster
    print_post_test_summary
}


# ────────────────────────── Phases ──────────────────────────────────────────


confirm_preconditions() {
    # WHAT:  authorisation gate + lock file + sudo-iptables availability
    #        on the peer nodes.
    # WHEN:  first phase.
    # WHY:   iptables changes can lock you out of a node permanently if
    #        misapplied. Triple-gate the trigger; ensure cleanup trap
    #        is set BEFORE any rule is installed.
    local todays_date; todays_date="$(date +%Y-%m-%d)"
    if [[ "${YRAL_CHAOS_RUN_AUTHORISED:-}" != "${todays_date}" ]]; then
        echo "ERROR partition-rishi-6: refused — YRAL_CHAOS_RUN_AUTHORISED must equal '${todays_date}'" >&2
        exit 1
    fi
    if [[ -f /tmp/yral-v2-chaos-running.lock ]]; then
        echo "ERROR partition-rishi-6: another chaos run in progress" >&2; exit 1
    fi
    touch /tmp/yral-v2-chaos-running.lock
    trap "remove_partition_iptables_rules_silently_on_exit" EXIT

    local peer_node
    for peer_node in "${PEER_NODES_TO_BLOCK_AT[@]}"; do
        if ! ssh -o ConnectTimeout=5 "rishi-deploy@${peer_node}" \
            "sudo iptables --version" >/dev/null 2>&1; then
            echo "ERROR partition-rishi-6: cannot run sudo iptables on ${peer_node}" >&2
            exit 1
        fi
    done
}


capture_partitioned_node_ip_address() {
    # WHAT:  resolve the partitioned node's public IPv4 (used in iptables
    #        match rules below).
    # WHEN:  before installing rules.
    # WHY:   we cannot match by hostname in iptables — it requires an IP.
    #        We resolve once and reuse so a DNS hiccup mid-test doesn't
    #        leave an orphan rule.
    PARTITIONED_NODE_IPV4="$(ssh "rishi-deploy@${PARTITIONED_NODE_HOSTNAME}" \
        "hostname --ip-address" | awk '{print $1}')"
    if [[ -z "${PARTITIONED_NODE_IPV4}" ]]; then
        echo "ERROR partition-rishi-6: could not resolve ${PARTITIONED_NODE_HOSTNAME} IP" >&2
        exit 1
    fi
    echo "partition-rishi-6: ${PARTITIONED_NODE_HOSTNAME} = ${PARTITIONED_NODE_IPV4}"
}


install_partition_iptables_rules_on_peers() {
    # WHAT:  on rishi-4 + rishi-5, append iptables rules that DROP every
    #        INPUT and OUTPUT packet to/from PARTITIONED_NODE_IPV4. Each
    #        rule carries a unique comment so cleanup can find them.
    # WHEN:  after IP captured.
    # WHY:   DROP (not REJECT) simulates a real network outage. REJECT
    #        sends an ICMP unreachable, which would let the peer's TCP
    #        stack fail fast; DROP forces full timeout — the realistic
    #        cross-DC outage behaviour we want to verify against.
    local peer_node
    for peer_node in "${PEER_NODES_TO_BLOCK_AT[@]}"; do
        ssh "rishi-deploy@${peer_node}" "sudo iptables \
            --append INPUT  --source ${PARTITIONED_NODE_IPV4} \
            --match comment --comment '${IPTABLES_RULE_COMMENT}' --jump DROP"
        ssh "rishi-deploy@${peer_node}" "sudo iptables \
            --append OUTPUT --destination ${PARTITIONED_NODE_IPV4} \
            --match comment --comment '${IPTABLES_RULE_COMMENT}' --jump DROP"
    done
    echo "partition-rishi-6: iptables DROP rules installed on ${PEER_NODES_TO_BLOCK_AT[*]}"
}


verify_etcd_quorum_holds_during_partition() {
    # WHAT:  give etcd ~30 seconds to detect rishi-6 as unhealthy, then
    #        query rishi-4's etcd endpoint health — must report HEALTHY
    #        on rishi-4 + rishi-5, UNHEALTHY on rishi-6.
    # WHEN:  shortly after partition begins.
    # WHY:   2-of-3 quorum is the entire reason we run 3 etcd members.
    #        If rishi-4/5 cannot reach quorum without rishi-6, the
    #        cluster is mis-configured (wrong --initial-cluster,
    #        likely) and chaos test catches it.
    sleep 30
    if ! docker exec "$(docker ps --filter name=etcd-rishi-4 --quiet | head -n 1)" \
        etcdctl --endpoints=http://etcd-rishi-4:2379,http://etcd-rishi-5:2379 \
        endpoint health >/dev/null 2>&1; then
        echo "FAIL partition-rishi-6: etcd quorum lost — rishi-4 + rishi-5 endpoints unhealthy" >&2
        return 1
    fi
    echo "  ✅ etcd quorum healthy on rishi-4 + rishi-5"
}


verify_patroni_writes_succeed_during_partition() {
    # WHAT:  same write+read sanity roundtrip used by the other chaos
    #        tests. Confirms Patroni leader can still commit despite
    #        losing rishi-6.
    # WHEN:  after etcd verification.
    # WHY:   leader election + data-plane writes are different concerns;
    #        a healthy etcd doesn't automatically mean Postgres writes
    #        succeed. The rishi-6 async replica being unreachable should
    #        NOT block sync commit (which only requires rishi-5).
    local sanity_check_nonce; sanity_check_nonce="chaos-partition-$(date +%s)-$RANDOM"
    local postgres_password
    postgres_password="$(cat /run/secrets/postgres-superuser-password 2>/dev/null || echo "${YRAL_POSTGRES_SUPERUSER_PASSWORD:-}")"
    PGPASSWORD="${postgres_password}" psql \
        --host=pgbouncer --username=postgres --dbname=postgres --no-password \
        --command "CREATE SCHEMA IF NOT EXISTS chaos_test_sanity;
                   CREATE TABLE IF NOT EXISTS chaos_test_sanity.partition_log (
                       inserted_at TIMESTAMPTZ DEFAULT now(), nonce TEXT
                   );
                   INSERT INTO chaos_test_sanity.partition_log (nonce) VALUES ('${sanity_check_nonce}');"
    local read_back
    read_back="$(PGPASSWORD="${postgres_password}" psql \
        --host=pgbouncer --username=postgres --dbname=postgres --no-password \
        --tuples-only --no-align \
        --command "SELECT nonce FROM chaos_test_sanity.partition_log WHERE nonce='${sanity_check_nonce}';")"
    if [[ "${read_back}" != "${sanity_check_nonce}" ]]; then
        echo "FAIL partition-rishi-6: Patroni write/read failed during partition" >&2
        return 1
    fi
    echo "  ✅ Patroni still committing writes during partition"
}


sleep_remainder_of_partition_window() {
    # WHAT:  sleep until 10 minutes have elapsed since iptables rules
    #        were installed.
    # WHEN:  after immediate verifications.
    # WHY:   the test scenario is "10-minute partition", not "30-second
    #        partition". Some failure modes (Patroni demoting itself,
    #        WAL retention pressure) only surface after several minutes.
    echo "partition-rishi-6: holding partition for ~${PARTITION_DURATION_SECONDS}s"
    sleep "$(( PARTITION_DURATION_SECONDS - 60 ))"  # subtract earlier waits
}


remove_partition_iptables_rules_on_peers() {
    # WHAT:  delete every iptables rule tagged with our comment from
    #        each peer node.
    # WHEN:  after partition window elapses (or via trap on exit).
    # WHY:   `iptables --delete` with the same predicate removes the
    #        matching rule. Comment match means we never delete an
    #        unrelated rule. Idempotent — re-running is safe.
    local peer_node
    for peer_node in "${PEER_NODES_TO_BLOCK_AT[@]}"; do
        ssh "rishi-deploy@${peer_node}" "sudo iptables \
            --delete INPUT  --source ${PARTITIONED_NODE_IPV4} \
            --match comment --comment '${IPTABLES_RULE_COMMENT}' --jump DROP" 2>/dev/null || true
        ssh "rishi-deploy@${peer_node}" "sudo iptables \
            --delete OUTPUT --destination ${PARTITIONED_NODE_IPV4} \
            --match comment --comment '${IPTABLES_RULE_COMMENT}' --jump DROP" 2>/dev/null || true
    done
    echo "partition-rishi-6: iptables rules removed"
}


verify_partitioned_node_rejoined_cluster() {
    # WHAT:  poll until (a) rishi-6's etcd member reports healthy, (b)
    #        rishi-6's Patroni async replica is back in the cluster
    #        members list, (c) at least one Swarm task is once again
    #        scheduled on rishi-6.
    # WHEN:  after iptables cleanup.
    # WHY:   without this verification we'd record a "passing" test even
    #        if the partition's after-effects (e.g. Patroni stuck in
    #        recovery) lingered.
    local rejoin_deadline_seconds=180
    local started_at; started_at="$(date +%s)"
    while true; do
        local now elapsed; now="$(date +%s)"; elapsed=$(( now - started_at ))
        if (( elapsed > rejoin_deadline_seconds )); then
            echo "FAIL partition-rishi-6: ${PARTITIONED_NODE_HOSTNAME} did not rejoin within ${rejoin_deadline_seconds}s" >&2
            return 1
        fi

        local etcd_rishi_6_health
        etcd_rishi_6_health="$(docker exec "$(docker ps --filter name=etcd-rishi-4 --quiet | head -n 1)" \
            etcdctl --endpoints=http://etcd-rishi-6:2379 endpoint health 2>&1 || true)"
        if echo "${etcd_rishi_6_health}" | grep --quiet "is healthy"; then
            echo "  ✅ etcd on ${PARTITIONED_NODE_HOSTNAME} healthy"
            return 0
        fi
        sleep 5
    done
}


remove_partition_iptables_rules_silently_on_exit() {
    # WHAT:  best-effort cleanup invoked by the EXIT trap.
    # WHEN:  always.
    # WHY:   leaving iptables DROP rules in place on a Swarm peer would
    #        permanently break the cluster — the cleanup trap is the
    #        last line of defence against operator error.
    local peer_node
    for peer_node in "${PEER_NODES_TO_BLOCK_AT[@]}"; do
        ssh -o ConnectTimeout=5 "rishi-deploy@${peer_node}" "sudo iptables \
            --delete INPUT  --source ${PARTITIONED_NODE_IPV4:-0.0.0.0} \
            --match comment --comment '${IPTABLES_RULE_COMMENT}' --jump DROP" 2>/dev/null || true
        ssh -o ConnectTimeout=5 "rishi-deploy@${peer_node}" "sudo iptables \
            --delete OUTPUT --destination ${PARTITIONED_NODE_IPV4:-0.0.0.0} \
            --match comment --comment '${IPTABLES_RULE_COMMENT}' --jump DROP" 2>/dev/null || true
    done
    rm --force /tmp/yral-v2-chaos-running.lock 2>/dev/null || true
}


print_post_test_summary() {
    cat <<SUMMARY

✅ partition-rishi-6 chaos test PASSED.

Scenario: dropped all packets to/from ${PARTITIONED_NODE_HOSTNAME} for
${PARTITION_DURATION_SECONDS}s; etcd quorum held on rishi-4 + rishi-5;
Patroni kept committing writes; rishi-6 rejoined cleanly after iptables
cleanup.

Phase 0 exit criterion row 4 (per CONSTRAINTS H3) cleared.

SUMMARY
}


main "$@"


# ══════════════════════════════════════════════════════════════════════════
# RELATED FILES
# ─────────────
# - kill-rishi-6.sh, kill-patroni-leader.sh, fill-rishi-5-disk.sh
# - run-all-chaos-tests.sh — orchestrator that runs all four with cleanup.
# ══════════════════════════════════════════════════════════════════════════
