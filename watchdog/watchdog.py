"""Overlay watchdog — two independent checks running concurrently:

  1. DNS-alias check (original): resolves a list of Swarm overlay
     aliases; alerts Sentry on NXDOMAIN <-> resolved transitions.
     Catches the 2026-06-22 patroni-rishi-4 gossip-lost-alias class
     of failure.

  2. Replica-drift check (2026-07-03 extension): reads Docker Swarm
     services via docker.sock and alerts when actual running task
     count stays below desired replicas for > threshold. Catches
     the 2026-06-29 langfuse-web wedged-task class of failure that
     the DNS check misses (0 replicas → no alias to lose → no
     NXDOMAIN transition).

Both loops run in the same process. DNS check owns the main thread
(preserves the exact pre-extension behavior); replica-drift check
runs in a daemon thread. If the docker.sock isn't mounted (local
dev, or the deploy hasn't been updated yet) the replica-drift
thread logs once and exits — the DNS loop keeps running unaffected.
"""

import logging
import os
import socket
import sys
import threading
import time

import sentry_sdk

from replica_drift import (
    DRIFT_ALERT_SEC,
    INTERVAL_SEC as REPLICA_INTERVAL_SEC,
    DriftState,
    check_once as replica_check_once,
)

DEFAULT_ALIASES = [
    "patroni-rishi-4",
    "patroni-rishi-5",
    "patroni-rishi-6",
    "etcd-rishi-4",
    "etcd-rishi-5",
    "etcd-rishi-6",
    "redis-sentinel-rishi-4",
    "redis-sentinel-rishi-5",
    "redis-sentinel-rishi-6",
    "redis-primary",
    "pgbouncer",
]

ALIASES = [
    a.strip()
    for a in os.environ.get("WATCHDOG_ALIASES", ",".join(DEFAULT_ALIASES)).split(",")
    if a.strip()
]
INTERVAL_SEC = int(os.environ.get("WATCHDOG_INTERVAL_SEC", "300"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("dns-watchdog")

if dsn := os.environ.get("SENTRY_DSN", "").strip():
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        traces_sample_rate=0,
    )


def resolve(alias: str) -> str | None:
    try:
        return socket.gethostbyname(alias)
    except socket.gaierror:
        return None


def alert(alias: str, was: str | None, now: str | None) -> None:
    transition = "RECOVERED" if now else "MISSING"
    msg = f"overlay-dns alias {alias} {transition} (was={was} now={now})"
    log.warning(msg)
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("alias", alias)
        scope.set_tag("transition", transition)
        scope.set_level("warning" if now else "error")
        sentry_sdk.capture_message(msg)


def _emit_replica_alert(alert_ev) -> None:
    """Turn a replica_drift.DriftAlert into a log line + Sentry event.
    Kept out of the pure check_once function so replica_drift stays
    Sentry-free (easier to test)."""
    if alert_ev.kind == "drift":
        msg = (
            f"swarm service {alert_ev.service_name} stuck at "
            f"{alert_ev.running}/{alert_ev.desired} for "
            f"{alert_ev.duration_sec}s"
        )
        level = "warning"
    else:
        msg = (
            f"swarm service {alert_ev.service_name} recovered to "
            f"{alert_ev.running}/{alert_ev.desired} after "
            f"{alert_ev.duration_sec}s"
        )
        level = "info"
    log.warning(msg) if level == "warning" else log.info(msg)
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("service", alert_ev.service_name)
        scope.set_tag("check", "replica_drift")
        scope.set_tag("transition", alert_ev.kind)
        scope.set_level(level)
        sentry_sdk.capture_message(msg)


def _replica_check_loop() -> None:
    """Runs in a daemon thread. If docker.sock isn't mounted, log
    once + exit — the DNS loop keeps running on the main thread."""
    try:
        import docker  # imported lazily so DNS-only deploys still boot

        client = docker.from_env()
    except Exception as e:
        log.warning(
            "replica-drift check disabled — docker client init failed "
            "(is /var/run/docker.sock mounted?): %s",
            e,
        )
        return

    log.info(
        "watching swarm services every %ds; alert threshold %ds",
        REPLICA_INTERVAL_SEC,
        DRIFT_ALERT_SEC,
    )
    state: dict[str, DriftState] = {}
    while True:
        try:
            alerts = replica_check_once(client, state)
            for a in alerts:
                _emit_replica_alert(a)
        except Exception as e:
            # Docker API blips must not kill the thread — the DNS loop
            # is our fallback signal but ideally both stay up.
            log.warning("replica-drift check iteration failed: %s", e)
        time.sleep(REPLICA_INTERVAL_SEC)


def main() -> None:
    # Kick off the replica-drift loop in a daemon thread — inherits
    # SIGTERM from the main thread's exit, no explicit shutdown hook
    # needed. Additive: the DNS loop below is byte-identical to the
    # pre-extension code path.
    threading.Thread(
        target=_replica_check_loop,
        name="replica-drift",
        daemon=True,
    ).start()

    log.info("watching %d aliases every %ds: %s", len(ALIASES), INTERVAL_SEC, ALIASES)
    last: dict[str, str | None] = {a: resolve(a) for a in ALIASES}
    for a, ip in last.items():
        log.info("startup %s -> %s", a, ip or "NXDOMAIN")
        if ip is None:
            alert(a, was="unknown", now=None)
    while True:
        time.sleep(INTERVAL_SEC)
        for a in ALIASES:
            now = resolve(a)
            was = last[a]
            if (was is None) != (now is None):
                alert(a, was=was, now=now)
            last[a] = now


if __name__ == "__main__":
    main()
