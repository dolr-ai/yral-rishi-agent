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

import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request

import sentry_sdk

import autoheal
from patroni_leader import (
    INTERVAL_SEC as PATRONI_INTERVAL_SEC,
    REST_URLS as PATRONI_REST_URLS,
    LeaderState,
    check_once as patroni_check_once,
)
from replica_drift import (
    DRIFT_ALERT_SEC,
    INTERVAL_SEC as REPLICA_INTERVAL_SEC,
    DriftState,
    _desired_replica_count as _replica_desired,
    _running_replica_count as _replica_running,
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


def _emit_leader_alert(alert_ev) -> None:
    """Turn a patroni_leader.LeaderAlert into a log line + Sentry event.
    Kept out of the pure check_once function so patroni_leader stays
    Sentry-free (easier to test), mirroring _emit_replica_alert."""
    if alert_ev.kind == "leaderless":
        msg = (
            f"patroni cluster LEADERLESS for {alert_ev.duration_sec}s "
            f"({alert_ev.member_count} members, none holding the leader "
            f"role) — sync-mode will refuse auto-promotion; manual "
            f"failover likely needed"
        )
        level = "error"
    else:
        msg = (
            f"patroni leader recovered: {alert_ev.leader_name} after "
            f"{alert_ev.duration_sec}s leaderless"
        )
        level = "info"
    log.error(msg) if level == "error" else log.info(msg)
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("check", "patroni_leader")
        scope.set_tag("transition", alert_ev.kind)
        scope.set_level(level)
        sentry_sdk.capture_message(msg)


def _fetch_patroni_members() -> list[dict] | None:
    """Best-effort read of Patroni's cluster view. Any one reachable
    node returns the whole membership, so we try each REST URL and use
    the first that answers. Returns None when NONE answer — the caller
    then skips the poll rather than paging, so a watchdog-side network
    blip can't be mistaken for a leaderless cluster. A genuine
    leaderless cluster still has its Patroni REST up (processes run,
    just no elected leader), so it returns members with no leader."""
    for base in PATRONI_REST_URLS:
        url = f"{base.rstrip('/')}/cluster"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            members = data.get("members")
            if isinstance(members, list):
                return members
        except Exception as e:
            log.info("patroni REST %s unreachable: %s", url, e)
    return None


_docker_client = None
_docker_client_lock = threading.Lock()


def _get_docker_client():
    """Lazy singleton across the DNS + drift + autoheal call sites.
    None when docker.sock isn't mounted — every caller gracefully
    degrades to detect-only behavior in that case (autoheal skips,
    drift loop exits, DNS loop keeps running)."""
    global _docker_client
    with _docker_client_lock:
        if _docker_client is not None:
            return _docker_client
        try:
            import docker  # imported lazily so DNS-only deploys still boot

            _docker_client = docker.from_env()
        except Exception as e:
            log.warning(
                "docker client init failed — autoheal + replica-drift "
                "disabled (is /var/run/docker.sock mounted?): %s",
                e,
            )
            return None
    return _docker_client


def _emit_autoheal_result(result) -> None:
    """One log line + Sentry event per HealResult. Kept out of
    autoheal.py so that module stays Sentry-free (easier to test)."""
    kind = result.kind
    if kind == "skipped":
        return  # not-allowlisted / disabled globally — deliberate silence
    if kind == "started":
        msg = (
            f"auto-heal fired for {result.service_name}: ran "
            f"`docker service update --force` — signature={result.signature}"
        )
        level = "warning"
    elif kind == "verified":
        msg = (
            f"auto-heal verified for {result.service_name} after "
            f"force-update (signature={result.signature})"
        )
        level = "info"
    elif kind == "verify_failed":
        msg = (
            f"auto-heal VERIFY FAILED for {result.service_name}: "
            f"{result.detail} (signature={result.signature})"
        )
        level = "error"
    elif kind == "rate_limited":
        msg = (
            f"heal cap exhausted for {result.service_name}: "
            f"{result.detail} — leaving service unhealed until window rolls"
        )
        level = "error"
    elif kind == "global_rate_limited":
        msg = (
            f"GLOBAL heal cap exhausted — cluster-wide failure? "
            f"{result.detail}; service={result.service_name} unhealed"
        )
        level = "error"
    elif kind == "docker_error":
        msg = f"auto-heal DOCKER ERROR for {result.service_name}: {result.detail}"
        level = "error"
    elif kind == "disabled":
        # Post-verify cooldown — not the initial verify_failed event,
        # this fires on subsequent attempts against the same service
        # inside the cooldown window. Info-level so we see it but
        # don't page.
        msg = f"auto-heal SKIPPED for {result.service_name}: {result.detail}"
        level = "info"
    else:
        log.warning(
            "autoheal: unknown result kind %r for %s", kind, result.service_name
        )
        return

    if level == "warning":
        log.warning(msg)
    elif level == "error":
        log.error(msg)
    else:
        log.info(msg)
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("service", result.service_name)
        scope.set_tag("check", "autoheal")
        scope.set_tag("kind", kind)
        scope.set_tag("signature", result.signature)
        scope.set_level(level)
        sentry_sdk.capture_message(msg)


def _verify_drift_still(client, service_name: str) -> bool:
    """True iff the service is STILL below its desired replica count
    (heal didn't work). Any exception is treated as "still failing"
    so we err toward disabling rather than silently declaring victory."""
    try:
        svc = client.services.get(service_name)
        desired = _replica_desired(svc)
        if desired is None:
            return False  # global-mode → drift is meaningless
        return _replica_running(svc) < desired
    except Exception:
        return True


def _try_autoheal_for_dns(client, alias: str) -> None:
    """Handler for DNS-alias transition to MISSING. Resolves the
    alias to a Swarm service and runs the heal; Sentry emit lives
    in _emit_autoheal_result."""
    if client is None:
        return
    svc = autoheal.resolve_service_by_dns_alias(client, alias)
    if svc is None:
        log.info("autoheal: no swarm service matches alias=%s", alias)
        return

    def _verify_dns(_service_name: str) -> bool:
        return resolve(alias) is None

    result = autoheal.try_heal(client, svc.name, "dns", verify_fn=_verify_dns)
    _emit_autoheal_result(result)


def _replica_check_loop() -> None:
    """Runs in a daemon thread. If docker.sock isn't mounted, log
    once + exit — the DNS loop keeps running on the main thread."""
    client = _get_docker_client()
    if client is None:
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
                if a.kind == "drift":
                    # Only heal on transition INTO drift, not on recovery.
                    result = autoheal.try_heal(
                        client,
                        a.service_name,
                        "drift",
                        verify_fn=lambda name, _c=client: _verify_drift_still(_c, name),
                    )
                    _emit_autoheal_result(result)
        except Exception as e:
            # Docker API blips must not kill the thread — the DNS loop
            # is our fallback signal but ideally both stay up.
            log.warning("replica-drift check iteration failed: %s", e)
        time.sleep(REPLICA_INTERVAL_SEC)


def _patroni_leader_check_loop() -> None:
    """Runs in a daemon thread. Polls Patroni's REST cluster view and
    alerts when the cluster stays leaderless past the threshold — the
    page that the 2026-07-20 leaderless outage never produced. Fetch
    failures skip the poll (see _fetch_patroni_members) so they never
    advance the leaderless timer."""
    log.info(
        "watching patroni leadership every %ds via %s",
        PATRONI_INTERVAL_SEC,
        PATRONI_REST_URLS,
    )
    state = LeaderState()
    while True:
        try:
            members = _fetch_patroni_members()
            if members is None:
                log.warning(
                    "no patroni REST endpoint answered — skipping leader "
                    "check this poll (DNS-alias check covers process-down)"
                )
            else:
                for a in patroni_check_once(members, state):
                    _emit_leader_alert(a)
        except Exception as e:
            log.warning("patroni leader check iteration failed: %s", e)
        time.sleep(PATRONI_INTERVAL_SEC)


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
    threading.Thread(
        target=_patroni_leader_check_loop,
        name="patroni-leader",
        daemon=True,
    ).start()

    log.info("watching %d aliases every %ds: %s", len(ALIASES), INTERVAL_SEC, ALIASES)
    last: dict[str, str | None] = {a: resolve(a) for a in ALIASES}
    for a, ip in last.items():
        log.info("startup %s -> %s", a, ip or "NXDOMAIN")
        if ip is None:
            alert(a, was="unknown", now=None)
    # We deliberately do NOT auto-heal on the startup discovery of a
    # missing alias — startup often catches services still initializing;
    # the transition path below is the reliable "something broke" signal.
    while True:
        time.sleep(INTERVAL_SEC)
        for a in ALIASES:
            now = resolve(a)
            was = last[a]
            if (was is None) != (now is None):
                alert(a, was=was, now=now)
                if now is None:
                    # Transitioned INTO MISSING — try the standard fix.
                    _try_autoheal_for_dns(_get_docker_client(), a)
            last[a] = now


if __name__ == "__main__":
    main()
