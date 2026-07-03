"""Swarm service replica-drift watchdog — a second signal on top of
the DNS-alias check.

Why: on 2026-06-29 the langfuse-web Swarm task wedged in "No such
container" reconciliation after Saikat's weekly k3s update; Swarm's
exponential backoff eventually stopped retrying and the service sat
at 0/1 replicas for ~4 days. The DNS-alias watchdog didn't catch it
because the alias was gone entirely (service declared 0 replicas →
no alias registered → no NXDOMAIN transition, just permanent
absence). Same root class as the patroni-rishi-4 restart storm
(2026-06-22) but manifests differently.

This module runs on the Swarm manager (docker.sock is mounted RO)
and compares each service's desired replica count against how many
tasks are actually in state=running AND desired_state=running. When
the drift persists past REPLICA_DRIFT_ALERT_SEC we emit ONE Sentry
warning per drift episode. When it recovers we emit an info message
and clear the state — so a subsequent re-drift alerts again.

Pure function `check_once` is deliberately DB/loop/sleep-free so
tests can drive the state machine without a live Swarm.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

log = logging.getLogger("watchdog.replica_drift")


# Env-var knobs. Poll interval is short (60s default) because the
# alert threshold (5m default) needs multiple samples to establish
# "persistent" drift.
INTERVAL_SEC = int(os.environ.get("WATCHDOG_REPLICA_INTERVAL_SEC", "60"))
DRIFT_ALERT_SEC = int(os.environ.get("WATCHDOG_REPLICA_DRIFT_ALERT_SEC", "300"))
# yral- prefix catches yral-rishi-agent, yral-v2-*, yral-analytics*. Ignores
# the amorae stack until amorae is declared v2-critical.
SERVICES_FILTER = os.environ.get("WATCHDOG_REPLICA_SERVICES_FILTER", "yral-")


@dataclass
class DriftState:
    """Per-service tracking state. `drift_started_at` = None means
    "healthy right now"; a non-None value is the epoch timestamp when
    we first observed the drift. `alerted` prevents re-firing the
    same alert every poll while drift continues."""

    drift_started_at: float | None = None
    alerted: bool = False


@dataclass
class DriftAlert:
    """One transition event to emit outside the pure function. Kept
    plain so the caller (sync loop) can turn it into a Sentry
    capture_message + log line without importing Sentry here."""

    service_name: str
    kind: str  # "drift" or "recovery"
    running: int
    desired: int
    duration_sec: int


def _list_target_services(client, filter_prefix: str):
    """Return the subset of Swarm services whose name starts with
    `filter_prefix`. A one-liner today but split out so tests can
    exercise the filter without stubbing the whole loop."""
    return [s for s in client.services.list() if s.name.startswith(filter_prefix)]


def _running_replica_count(service) -> int:
    """Count tasks with both Status.State == 'running' AND
    DesiredState == 'running' — the exact wedged-task condition langfuse-web
    hit on 2026-06-29 was DesiredState=running but Status.State=failed with
    "No such container" reconciliation. Filtering on both catches that."""
    running = 0
    for task in service.tasks(filters={"desired-state": "running"}):
        state = (task.get("Status") or {}).get("State")
        if state == "running":
            running += 1
    return running


def _desired_replica_count(service) -> int | None:
    """Read the target replicas from the service spec. Returns None
    for global-mode services (no replicas key) — we skip those."""
    mode = (service.attrs.get("Spec") or {}).get("Mode") or {}
    replicated = mode.get("Replicated") or {}
    return replicated.get("Replicas")


def check_once(
    client,
    state: dict[str, DriftState],
    *,
    now: float | None = None,
    filter_prefix: str = SERVICES_FILTER,
    drift_alert_sec: int = DRIFT_ALERT_SEC,
) -> list[DriftAlert]:
    """One poll. Mutates `state` in place; returns the list of alert
    events the caller should emit to Sentry.

    Idempotency: we ONLY emit a "drift" alert on the poll when we
    first cross the threshold; subsequent polls while drift persists
    return no new alerts. Recovery emits once when running catches up
    to desired.
    """
    if now is None:
        now = time.time()

    alerts: list[DriftAlert] = []
    seen_names: set[str] = set()

    for service in _list_target_services(client, filter_prefix):
        name = service.name
        seen_names.add(name)
        desired = _desired_replica_count(service)
        if desired is None:
            # Global-mode service — no meaningful replica count. Skip.
            continue
        running = _running_replica_count(service)

        st = state.setdefault(name, DriftState())

        if running < desired:
            if st.drift_started_at is None:
                st.drift_started_at = now
                log.info(
                    "replica drift STARTED: %s running=%d desired=%d",
                    name,
                    running,
                    desired,
                )
            duration = int(now - st.drift_started_at)
            if not st.alerted and duration >= drift_alert_sec:
                alerts.append(
                    DriftAlert(
                        service_name=name,
                        kind="drift",
                        running=running,
                        desired=desired,
                        duration_sec=duration,
                    )
                )
                st.alerted = True
        else:
            if st.drift_started_at is not None and st.alerted:
                # Only emit recovery if we ever alerted on this
                # drift episode — sub-threshold blips clear silently.
                duration = int(now - st.drift_started_at)
                alerts.append(
                    DriftAlert(
                        service_name=name,
                        kind="recovery",
                        running=running,
                        desired=desired,
                        duration_sec=duration,
                    )
                )
            if st.drift_started_at is not None:
                log.info(
                    "replica drift CLEARED: %s running=%d desired=%d",
                    name,
                    running,
                    desired,
                )
            st.drift_started_at = None
            st.alerted = False

    # Drop tracking state for services that no longer exist (e.g.
    # docker stack rm) so the dict doesn't grow unbounded.
    for stale in [k for k in state if k not in seen_names]:
        del state[stale]

    return alerts
