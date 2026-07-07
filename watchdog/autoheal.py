"""Automatic recovery for the two Saikat-Monday failure signatures
the watchdog already detects (DNS alias missing + replica drift).

Every one of the recent same-signature outages recovered with the
same one-line command:

    docker service update --force <service-name>

This module wraps that into a rate-limited, allowlist-gated,
verify-after-run auto-heal. Pure functions + explicit state so tests
can drive the machine without live Swarm access.

Design goals — order matters:

  1. **Bounded blast radius.** Only allowlisted services (and only
     ones whose name matches a hardcoded prefix) can be healed.
     Explicit deny list would be trickier to reason about; explicit
     allow means a new service is un-healable until an operator
     opts it in.

  2. **Rate-limit against flap loops.** A broken service that keeps
     failing after each heal would spiral into a heal loop that
     nukes Swarm's scheduler. Cap per-service (default 3/hour) and
     global (default 10/hour). When a per-service cap trips, the
     service is escalated to Sentry ERROR and left alone — that's
     the "wake up an operator" signal because normal recovery
     didn't work.

  3. **Verify-after-heal.** After the force-update, sleep + re-run
     the detection check. If the service still fails, mark it
     "disabled for the rest of the hour" and Sentry ERROR — again,
     the operator wake-up.

  4. **Kill switch.** WATCHDOG_AUTOHEAL_ENABLED=false reverts to
     alert-only behavior. Rishi's ADHD-observability rule: every
     protective system ships with a hot-edit knob.

The module is deliberately docker-py-free at import time; the client
is passed to `try_heal(client, ...)` so tests can inject a stub and
so watchdog.py can keep its "docker.sock not mounted → skip" fallback.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock

log = logging.getLogger("watchdog.autoheal")


# ─── config knobs (env-driven, defaults from brief) ─────────────────────


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


ENABLED = _env_bool("WATCHDOG_AUTOHEAL_ENABLED", True)

# Prefixes deliberately match ONLY the services whose known-good
# recovery is `docker service update --force`. New services are
# opt-in via env override, not by default.
_DEFAULT_ALLOWLIST_PREFIXES: tuple[str, ...] = (
    "yral-rishi-agent",
    "yral-v2-patroni_",
    "yral-v2-redis_",
    "yral-v2-langfuse_",
    "yral-analytics",
    "yral-analytics-events",
    "yral-analytics-clickhouse",
    "overlay-watchdog",
)


def _parse_allowlist() -> tuple[str, ...]:
    override = os.environ.get("WATCHDOG_AUTOHEAL_ALLOWLIST", "").strip()
    if override:
        return tuple(p.strip() for p in override.split(",") if p.strip())
    return _DEFAULT_ALLOWLIST_PREFIXES


ALLOWLIST_PREFIXES = _parse_allowlist()
MAX_PER_HOUR = int(os.environ.get("WATCHDOG_AUTOHEAL_MAX_PER_HOUR", "3"))
MAX_GLOBAL_PER_HOUR = int(os.environ.get("WATCHDOG_AUTOHEAL_MAX_GLOBAL_PER_HOUR", "10"))
VERIFY_DELAY_SEC = int(os.environ.get("WATCHDOG_AUTOHEAL_VERIFY_DELAY_SEC", "30"))

_WINDOW_SEC = 60 * 60  # rolling 1-hour window for both caps


# ─── state (module-level singleton, thread-safe) ────────────────────────


@dataclass
class _AutohealState:
    heals_per_service: dict[str, deque[float]] = field(default_factory=dict)
    global_heals: deque[float] = field(default_factory=deque)
    # service_name → epoch_ts at which the auto-heal disable expires
    # (set when verify_fn returns still-failing after a heal).
    disabled_until: dict[str, float] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)


_STATE = _AutohealState()


def _reset_state_for_tests() -> None:
    """Test-only: clear the module singleton between scenarios."""
    with _STATE.lock:
        _STATE.heals_per_service.clear()
        _STATE.global_heals.clear()
        _STATE.disabled_until.clear()


# ─── result type ────────────────────────────────────────────────────────


@dataclass
class HealResult:
    """Return type for try_heal(). `kind` drives the caller's Sentry
    emit — Sentry lives in watchdog.py, not here."""

    kind: str
    service_name: str
    signature: str
    detail: str = ""


# ─── helpers ────────────────────────────────────────────────────────────


def is_allowlisted(service_name: str, prefixes: tuple[str, ...] | None = None) -> bool:
    """Prefix match — a service is allowlisted iff its name starts with
    any allowlist prefix. Prefixes lets tests inject a custom allowlist
    without mutating the module constant."""
    if prefixes is None:
        prefixes = ALLOWLIST_PREFIXES
    return any(service_name.startswith(p) for p in prefixes)


def _trim(dq: deque, now: float) -> int:
    """Drop timestamps older than the rolling window, return the
    remaining count. Trimming lazily on read keeps the state small
    during quiet periods."""
    cutoff = now - _WINDOW_SEC
    while dq and dq[0] < cutoff:
        dq.popleft()
    return len(dq)


def resolve_service_by_dns_alias(client, alias: str):
    """DNS aliases don't map 1:1 to Swarm service names (services carry
    stack prefixes like `yral-v2-patroni_patroni-rishi-4`). Fuzzy match
    on substring — good enough for the current outage pattern where
    the alias appears verbatim in the service name."""
    try:
        services = client.services.list()
    except Exception as e:
        log.warning("autoheal service list failed for alias=%s: %s", alias, e)
        return None
    for svc in services:
        if alias in svc.name:
            return svc
    return None


def _check_and_reserve(service_name: str, now: float) -> HealResult | None:
    """Under lock: run the pre-heal gauntlet + record the timestamp on
    approval. Returns None to proceed; HealResult to reject."""
    with _STATE.lock:
        deadline = _STATE.disabled_until.get(service_name)
        if deadline is not None and now < deadline:
            return HealResult(
                kind="disabled",
                service_name=service_name,
                signature="",
                detail="in verify-failure cooldown",
            )
        # Trim expired cooldowns to keep the dict small.
        if deadline is not None and now >= deadline:
            del _STATE.disabled_until[service_name]

        gcount = _trim(_STATE.global_heals, now)
        if gcount >= MAX_GLOBAL_PER_HOUR:
            return HealResult(
                kind="global_rate_limited",
                service_name=service_name,
                signature="",
                detail=f"{gcount}/{MAX_GLOBAL_PER_HOUR} in rolling hour",
            )

        dq = _STATE.heals_per_service.setdefault(service_name, deque())
        scount = _trim(dq, now)
        if scount >= MAX_PER_HOUR:
            return HealResult(
                kind="rate_limited",
                service_name=service_name,
                signature="",
                detail=f"{scount}/{MAX_PER_HOUR} in rolling hour",
            )

        dq.append(now)
        _STATE.global_heals.append(now)
    return None


def _rollback_reservation(service_name: str, now: float) -> None:
    """Docker API blew up before the force-update actually landed —
    unwind the timestamp so the caller can retry on the next poll
    without silently burning the per-service quota."""
    with _STATE.lock:
        dq = _STATE.heals_per_service.get(service_name)
        if dq and dq[-1] == now:
            dq.pop()
        if _STATE.global_heals and _STATE.global_heals[-1] == now:
            _STATE.global_heals.pop()


# ─── main entry point ────────────────────────────────────────────────────


def try_heal(
    client,
    service_name: str,
    signature: str,
    *,
    verify_fn=None,
    now: float | None = None,
    sleep_fn=time.sleep,
    allowlist_prefixes: tuple[str, ...] | None = None,
) -> HealResult:
    """One-shot heal attempt. Order (documented in the module docstring):
    kill-switch → allowlist → cooldown → global cap → per-service cap →
    docker force-update → optional verify.

    `verify_fn(service_name)` should return True when the service is
    STILL failing after the heal (so verify_fn=None means "don't
    verify"). Kept as a callable so DNS + drift can inject their
    respective post-heal checks without autoheal knowing either.

    `sleep_fn` + `now` are injected for tests — production callers
    take the defaults (time.sleep + time.time())."""
    if not ENABLED:
        return HealResult(
            kind="skipped",
            service_name=service_name,
            signature=signature,
            detail="autoheal disabled",
        )
    if not is_allowlisted(service_name, allowlist_prefixes):
        return HealResult(
            kind="skipped",
            service_name=service_name,
            signature=signature,
            detail="not on allowlist",
        )
    if now is None:
        now = time.time()

    rejection = _check_and_reserve(service_name, now)
    if rejection is not None:
        rejection.signature = signature
        return rejection

    try:
        svc = client.services.get(service_name)
        svc.force_update()
    except Exception as e:
        log.exception("autoheal docker error for %s", service_name)
        _rollback_reservation(service_name, now)
        return HealResult(
            kind="docker_error",
            service_name=service_name,
            signature=signature,
            detail=str(e)[:200],
        )

    if verify_fn is None:
        return HealResult(
            kind="started",
            service_name=service_name,
            signature=signature,
        )

    sleep_fn(VERIFY_DELAY_SEC)
    try:
        still_failing = bool(verify_fn(service_name))
    except Exception as e:
        # Verify blew up → assume still-failing (safer than assuming
        # healthy). Same disable-for-hour policy.
        log.warning("autoheal verify raised for %s: %s", service_name, e)
        still_failing = True

    if not still_failing:
        return HealResult(
            kind="verified",
            service_name=service_name,
            signature=signature,
        )
    with _STATE.lock:
        _STATE.disabled_until[service_name] = now + _WINDOW_SEC
    return HealResult(
        kind="verify_failed",
        service_name=service_name,
        signature=signature,
        detail=f"still failing after {VERIFY_DELAY_SEC}s; disabled until +1h",
    )
