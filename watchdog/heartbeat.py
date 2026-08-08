"""External dead-man's-switch — the alert path that survives Sentry.

On 2026-08-08 Sentry's embedded Docker DNS registry was evicted on
rishi-3. Every container stayed "Up" but stopped resolving its siblings,
so `web` lost `pgbouncer` and `relay` lost `redis`. Sentry served 500s
and dropped every inbound event for ~45 hours, and nobody was paged.

The watchdog could not have saved us, and making it global would not
have helped: its only alert channel is `sentry_sdk.capture_message`,
pointed at the very host that was down. A monitor that reports THROUGH
the thing it monitors has no failure mode in which it speaks up.

This module inverts the direction. Instead of pushing an alert out when
something breaks, the watchdog pings an external URL while things are
FINE. Silence is the alarm — so it fires for the whole class of faults
that kill the reporter too:

  - Sentry down (2026-08-08)
  - the watchdog itself crashed or descheduled
  - the Swarm, the host, or the network gone

`WATCHDOG_HEARTBEAT_URL` is deliberately vendor-neutral: any
dead-man's-switch that treats "no ping for N minutes" as an incident
works (healthchecks.io, Better Stack, or an owned endpoint). Empty URL
means disabled, so this is inert until an operator opts in.

Pure functions take an injected `opener` so tests never touch the
network; only `run_forever` does real I/O.
"""

from __future__ import annotations

import logging
import os
import time
import urllib.request

log = logging.getLogger("watchdog.heartbeat")

# Public surfaces whose reachability the heartbeat vouches for. Sentry is
# first BECAUSE it is our blind spot: when it dies, this check is the only
# thing left that can notice.
DEFAULT_ENDPOINTS = (
    "https://sentry.rishi.yral.com/_health/",
    "https://agent.rishi.yral.com/health",
)

URL = os.environ.get("WATCHDOG_HEARTBEAT_URL", "").strip()
INTERVAL_SEC = int(os.environ.get("WATCHDOG_HEARTBEAT_INTERVAL_SEC", "60"))
TIMEOUT_SEC = int(os.environ.get("WATCHDOG_HEARTBEAT_TIMEOUT_SEC", "10"))
ENDPOINTS = tuple(
    e.strip()
    for e in os.environ.get(
        "WATCHDOG_HEARTBEAT_ENDPOINTS", ",".join(DEFAULT_ENDPOINTS)
    ).split(",")
    if e.strip()
)


def _default_opener(url: str, timeout: int):
    return urllib.request.urlopen(url, timeout=timeout)


def check_endpoint(
    url: str, opener=_default_opener, timeout: int = TIMEOUT_SEC
) -> bool:
    """True iff the URL answers 2xx. Any exception is a failure — a
    connection refused and a 500 are the same thing to a dead-man's
    switch, and collapsing them keeps the decision one boolean wide."""
    try:
        with opener(url, timeout) as resp:
            return 200 <= getattr(resp, "status", 0) < 300
    except Exception as e:  # noqa: BLE001 — any failure is a failure
        log.warning("heartbeat endpoint %s failed: %s", url, e)
        return False


def all_healthy(endpoints, opener=_default_opener, timeout: int = TIMEOUT_SEC) -> bool:
    """Every endpoint must pass. Deliberately AND, not majority: this
    gates a liveness ping, and a half-broken fleet must not read as fine."""
    return all(check_endpoint(u, opener, timeout) for u in endpoints)


def ping_url(base: str, healthy: bool) -> str:
    """healthchecks.io-style convention: the bare URL signals success and
    a `/fail` suffix signals a known-bad state. Providers that only
    understand the bare URL still work — they just see silence instead of
    an explicit failure, which trips the same alarm a beat later."""
    return base if healthy else base.rstrip("/") + "/fail"


def beat(
    base: str,
    endpoints,
    opener=_default_opener,
    timeout: int = TIMEOUT_SEC,
) -> bool:
    """One cycle: probe the endpoints, then report. Returns what was
    reported so callers/tests can assert on it. A failed ping is logged,
    never raised — the switch firing on silence is the intended fallback."""
    healthy = all_healthy(endpoints, opener, timeout)
    try:
        opener(ping_url(base, healthy), timeout).close()
    except Exception as e:  # noqa: BLE001
        log.warning("heartbeat ping failed (switch will fire on silence): %s", e)
    return healthy


def run_forever() -> None:
    """Daemon-thread entry point. No-op when unconfigured so local dev and
    any deploy that hasn't set the URL behave exactly as before."""
    if not URL:
        log.info("heartbeat disabled — WATCHDOG_HEARTBEAT_URL unset")
        return
    log.info(
        "heartbeat every %ds to dead-man's switch, vouching for %d endpoints: %s",
        INTERVAL_SEC,
        len(ENDPOINTS),
        list(ENDPOINTS),
    )
    while True:
        try:
            beat(URL, ENDPOINTS)
        except Exception as e:  # noqa: BLE001 — thread must never die
            log.warning("heartbeat iteration failed: %s", e)
        time.sleep(INTERVAL_SEC)
