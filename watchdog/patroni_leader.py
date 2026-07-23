"""Patroni leader-presence watchdog — a third signal alongside the
DNS-alias and replica-drift checks.

Why: on 2026-07-20 all three Patroni nodes rebooted in the same
minute (external maintenance). Leadership orphaned onto a degraded
node, and ~27h later synchronous-mode's safety guard correctly
REFUSED to auto-promote either survivor — a 2h40m leaderless outage
that needed a manual failover. It paged nobody: the DNS-alias check
still resolved (the Patroni processes were up, just leaderless) and
the replica-drift check watches Swarm task counts, not cluster
state. `cluster_unlocked: true` produced zero alerts.

This check closes that gap. It reads Patroni's own REST `/cluster`
view (reachable over the overlay at http://patroni-rishi-N:8008)
and alerts when NO member holds the leader role for longer than the
threshold. A brief re-election during a healthy failover is normal
and clears well under the threshold, so we only page on a genuine
leaderless cluster.

Pure function `check_once` is deliberately HTTP/loop/sleep-free —
it takes an already-fetched members list — so tests can drive the
state machine without a live cluster. The fetch + Sentry emit are
thin wrappers in watchdog.py, mirroring replica_drift.py.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

log = logging.getLogger("watchdog.patroni_leader")


# Env-var knobs. Poll interval is short; the alert threshold is set
# above a healthy election (which completes in seconds) but far below
# the 2h40m outage we are guarding against, so a real leaderless
# cluster pages within ~2 minutes while normal failovers stay silent.
INTERVAL_SEC = int(os.environ.get("WATCHDOG_PATRONI_INTERVAL_SEC", "60"))
LEADERLESS_ALERT_SEC = int(
    os.environ.get("WATCHDOG_PATRONI_LEADERLESS_ALERT_SEC", "90")
)
# Any one reachable node returns the whole cluster view, so we list
# all three and use the first that answers.
REST_URLS = [
    u.strip()
    for u in os.environ.get(
        "WATCHDOG_PATRONI_REST_URLS",
        "http://patroni-rishi-4:8008,"
        "http://patroni-rishi-5:8008,"
        "http://patroni-rishi-6:8008",
    ).split(",")
    if u.strip()
]
# Patroni reports "leader" for a normal primary and "standby_leader"
# for a standby-cluster primary; either satisfies leader-presence.
LEADER_ROLES = {"leader", "standby_leader"}


@dataclass
class LeaderState:
    """Whole-cluster tracking state (one Patroni cluster, so one
    object — not a per-service dict like replica_drift). `leaderless_started_at`
    None means "a leader is present right now"; a non-None value is the
    epoch timestamp when the cluster first went leaderless. `alerted`
    prevents re-firing every poll while it stays leaderless."""

    leaderless_started_at: float | None = None
    alerted: bool = False


@dataclass
class LeaderAlert:
    """One transition event to emit outside the pure function. Kept
    plain so the caller can turn it into a Sentry capture_message +
    log line without importing Sentry here."""

    kind: str  # "leaderless" or "recovery"
    leader_name: str | None
    member_count: int
    duration_sec: int


def leader_name(members: list[dict]) -> str | None:
    """Return the name of the member holding the leader role in a
    running state, or None if the cluster is leaderless. Matches the
    `cluster_unlocked` condition from the 2026-07-20 incident, where
    every member reported a non-leader role."""
    for m in members:
        if m.get("role") in LEADER_ROLES and m.get("state") == "running":
            return m.get("name")
    return None


def check_once(
    members: list[dict],
    state: LeaderState,
    *,
    now: float | None = None,
    alert_sec: int = LEADERLESS_ALERT_SEC,
) -> list[LeaderAlert]:
    """One poll. Mutates `state` in place; returns the list of alert
    events the caller should emit to Sentry.

    Idempotency mirrors replica_drift.check_once: we emit "leaderless"
    ONCE on the poll where the outage first crosses the threshold, and
    "recovery" ONCE when a leader reappears after we alerted. A brief
    sub-threshold election clears silently.
    """
    if now is None:
        now = time.time()

    leader = leader_name(members)
    alerts: list[LeaderAlert] = []

    if leader is None:
        if state.leaderless_started_at is None:
            state.leaderless_started_at = now
            log.info("patroni cluster went LEADERLESS (members=%d)", len(members))
        duration = int(now - state.leaderless_started_at)
        if not state.alerted and duration >= alert_sec:
            alerts.append(
                LeaderAlert(
                    kind="leaderless",
                    leader_name=None,
                    member_count=len(members),
                    duration_sec=duration,
                )
            )
            state.alerted = True
    else:
        if state.leaderless_started_at is not None and state.alerted:
            # Only emit recovery if we ever alerted this episode —
            # sub-threshold blips clear silently.
            duration = int(now - state.leaderless_started_at)
            alerts.append(
                LeaderAlert(
                    kind="recovery",
                    leader_name=leader,
                    member_count=len(members),
                    duration_sec=duration,
                )
            )
        if state.leaderless_started_at is not None:
            log.info("patroni leader RESTORED: %s", leader)
        state.leaderless_started_at = None
        state.alerted = False

    return alerts
