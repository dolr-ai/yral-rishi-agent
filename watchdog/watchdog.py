"""Resolves a list of Docker Swarm overlay DNS aliases on a loop.
Alerts Sentry on state transitions (resolved <-> NXDOMAIN).

Background: Saikat's weekly Monday cluster updates trigger simultaneous
container restarts on rishi-4. Swarm's gossip-replicated service DNS
occasionally loses an alias during the restart storm — the container
runs fine but other services can't resolve its hostname. This watchdog
catches that within 5 minutes so we can re-register the alias before
anyone notices.
"""

import logging
import os
import socket
import sys
import time

import sentry_sdk

DEFAULT_ALIASES = [
    "patroni-rishi-4", "patroni-rishi-5", "patroni-rishi-6",
    "etcd-rishi-4", "etcd-rishi-5", "etcd-rishi-6",
    "redis-sentinel-rishi-4", "redis-sentinel-rishi-5", "redis-sentinel-rishi-6",
    "redis-primary", "pgbouncer",
]

ALIASES = [a.strip() for a in os.environ.get("WATCHDOG_ALIASES", ",".join(DEFAULT_ALIASES)).split(",") if a.strip()]
INTERVAL_SEC = int(os.environ.get("WATCHDOG_INTERVAL_SEC", "300"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("dns-watchdog")

if dsn := os.environ.get("SENTRY_DSN", "").strip():
    sentry_sdk.init(dsn=dsn, environment=os.environ.get("SENTRY_ENVIRONMENT", "production"), traces_sample_rate=0)


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


def main() -> None:
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
