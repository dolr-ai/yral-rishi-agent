"""Regression tests for the watchdog auto-heal path.

Drives `autoheal.try_heal` across the five brief-mandated scenarios
plus the source-pin bar. All tests use a fake docker client + fake
time so no live Swarm access is needed.

The scenarios (from the brief):

  1. DNS transition on an allowlisted service → heal fires, correct
     service_name passed to `force_update`.
  2. Same signature on a NON-allowlisted service → skipped, no
     force_update call.
  3. Per-service cap exhaustion: 4 heals within one hour on the same
     service → 3rd succeeds + 4th rejected as rate_limited.
  4. Post-heal verify still failing → verify_failed + service added
     to the disabled-until cooldown set (subsequent attempt inside
     the hour → kind='disabled').
  5. Global cap: 11 heals across different services within an hour →
     11th rejected as global_rate_limited.

Plus source-pins for the default allowlist and env-driven knobs.
"""

import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "watchdog"))

REPO = Path(__file__).resolve().parents[1]


def _fresh_autoheal():
    """Reload the autoheal module + reset state between tests. Doing
    this at the top of each test keeps env-var overrides from leaking
    between scenarios."""
    if "autoheal" in sys.modules:
        del sys.modules["autoheal"]
    import autoheal as ah  # noqa: E402

    ah._reset_state_for_tests()
    return ah


# ─── fake docker-py Service + client ────────────────────────────────────


class _FakeService:
    def __init__(self, name: str) -> None:
        self.name = name
        self.force_update_calls = 0

    def force_update(self):
        self.force_update_calls += 1


class _FakeServiceCollection:
    def __init__(self, services: list[_FakeService]) -> None:
        self._services = services
        self.raise_on_get = False

    def list(self):
        return list(self._services)

    def get(self, name):
        if self.raise_on_get:
            raise RuntimeError("simulated docker api error")
        for s in self._services:
            if s.name == name:
                return s
        raise KeyError(name)


class _FakeClient:
    def __init__(self, services) -> None:
        self.services = _FakeServiceCollection(services)


# ─── source-pin ─────────────────────────────────────────────────────────


def test_default_allowlist_matches_brief():
    """The eight-prefix hardcoded list is load-bearing safety — a
    silent add or remove would change which services can be healed
    without operator review."""
    ah = _fresh_autoheal()
    assert set(ah._DEFAULT_ALLOWLIST_PREFIXES) == {
        "yral-rishi-agent",
        "yral-v2-patroni_",
        "yral-v2-redis_",
        "yral-v2-langfuse_",
        "yral-analytics",
        "yral-analytics-events",
        "yral-analytics-clickhouse",
        "overlay-watchdog",
    }


def test_config_knobs_have_brief_defaults():
    """Kill-switch defaults ON, caps at brief-mandated values."""
    ah = _fresh_autoheal()
    assert ah.ENABLED is True
    assert ah.MAX_PER_HOUR == 3
    assert ah.MAX_GLOBAL_PER_HOUR == 10
    assert ah.VERIFY_DELAY_SEC == 30


def test_allowlist_env_override(monkeypatch):
    """Env override must take precedence over the hardcoded default so
    Session 6 can widen the allowlist without a code deploy."""
    monkeypatch.setenv("WATCHDOG_AUTOHEAL_ALLOWLIST", "my-service,other-service")
    ah = _fresh_autoheal()
    assert ah.ALLOWLIST_PREFIXES == ("my-service", "other-service")


def test_kill_switch_disables_heal():
    """WATCHDOG_AUTOHEAL_ENABLED=false must revert to detect-only —
    ADHD-observability rule ships with a hot-edit knob."""
    os.environ["WATCHDOG_AUTOHEAL_ENABLED"] = "false"
    try:
        ah = _fresh_autoheal()
        svc = _FakeService("yral-rishi-agent")
        client = _FakeClient([svc])
        result = ah.try_heal(client, "yral-rishi-agent", "dns", now=1000.0)
        assert result.kind == "skipped"
        assert svc.force_update_calls == 0
    finally:
        del os.environ["WATCHDOG_AUTOHEAL_ENABLED"]


# ─── behavioural ────────────────────────────────────────────────────────


def test_allowlisted_service_heals_and_calls_force_update():
    """The load-bearing happy path: an allowlisted service in-scope
    triggers exactly one force_update on the resolved Service."""
    ah = _fresh_autoheal()
    svc = _FakeService("yral-v2-patroni_patroni-rishi-4")
    client = _FakeClient([svc])

    result = ah.try_heal(client, svc.name, "dns", now=1000.0)

    assert result.kind == "started"
    assert result.service_name == svc.name
    assert result.signature == "dns"
    assert svc.force_update_calls == 1, (
        "must invoke force_update exactly once per allowlisted heal"
    )


def test_non_allowlisted_service_alert_only():
    """yral-chat-ai* is deliberately EXCLUDED per Rule 7. A heal
    attempt on it must not call force_update."""
    ah = _fresh_autoheal()
    svc = _FakeService("yral-chat-ai")
    client = _FakeClient([svc])

    result = ah.try_heal(client, svc.name, "drift", now=1000.0)

    assert result.kind == "skipped"
    assert "allowlist" in result.detail
    assert svc.force_update_calls == 0, "non-allowlisted services must NEVER be healed"


def test_per_service_cap_exhaustion():
    """3 heals within an hour on the same service succeed; the 4th
    is refused with kind='rate_limited'. Prevents flap loops."""
    ah = _fresh_autoheal()
    svc = _FakeService("yral-rishi-agent")
    client = _FakeClient([svc])

    # 3 fresh heals — all succeed
    for i in range(3):
        r = ah.try_heal(client, svc.name, "drift", now=1000.0 + i * 10)
        assert r.kind == "started", f"heal {i + 1} unexpectedly failed: {r}"

    # 4th within the hour — cap trips
    result = ah.try_heal(client, svc.name, "drift", now=1000.0 + 30)
    assert result.kind == "rate_limited"
    assert svc.force_update_calls == 3, (
        f"expected exactly 3 force_update calls; got {svc.force_update_calls}"
    )

    # Same service after the window rolls forward → allowed again
    r_after = ah.try_heal(client, svc.name, "drift", now=1000.0 + 3700)
    assert r_after.kind == "started"


def test_global_cap_exhaustion_across_services():
    """11 heals across 11 different allowlisted services in one hour
    → 11th refused as global_rate_limited."""
    ah = _fresh_autoheal()
    # Craft 11 unique allowlisted service names — vary the suffix so
    # the per-service cap doesn't trip first.
    services = [_FakeService(f"yral-rishi-agent-{i}") for i in range(11)]
    client = _FakeClient(services)

    for i in range(10):
        r = ah.try_heal(client, services[i].name, "drift", now=1000.0 + i)
        assert r.kind == "started", f"heal {i + 1} unexpectedly failed: {r}"

    result = ah.try_heal(client, services[10].name, "drift", now=1000.0 + 20)
    assert result.kind == "global_rate_limited"
    assert services[10].force_update_calls == 0, (
        "11th service must NOT be healed once the global cap trips"
    )


def test_post_heal_verify_failure_marks_service_disabled():
    """After a heal + 30s sleep, verify_fn returns still-failing → the
    service is disabled for the rest of the hour + a subsequent
    attempt inside the window returns kind='disabled'."""
    ah = _fresh_autoheal()
    svc = _FakeService("yral-v2-langfuse_langfuse-web")
    client = _FakeClient([svc])

    # verify_fn returns True → "still failing"
    def _still_failing(_name):
        return True

    def _no_sleep(_):
        pass  # verify delay would be 30s in prod

    result = ah.try_heal(
        client,
        svc.name,
        "drift",
        verify_fn=_still_failing,
        now=1000.0,
        sleep_fn=_no_sleep,
    )
    assert result.kind == "verify_failed"
    assert "still failing" in result.detail

    # A follow-up attempt inside the disable window is a no-op:
    result2 = ah.try_heal(client, svc.name, "drift", now=1300.0)
    assert result2.kind == "disabled"
    assert svc.force_update_calls == 1, (
        "disabled service must NOT be healed again in the cooldown"
    )


def test_verify_success_returns_verified():
    """Happy path with verify: force_update runs, sleep + verify shows
    the service recovered → kind='verified'. Does NOT disable the
    service."""
    ah = _fresh_autoheal()
    svc = _FakeService("yral-rishi-agent")
    client = _FakeClient([svc])

    def _recovered(_name):
        return False  # not failing anymore

    sleeps = []
    result = ah.try_heal(
        client,
        svc.name,
        "drift",
        verify_fn=_recovered,
        now=1000.0,
        sleep_fn=lambda d: sleeps.append(d),
    )
    assert result.kind == "verified"
    # Verify delay was consulted — critical safety property from brief
    assert sleeps == [ah.VERIFY_DELAY_SEC]
    # Service NOT added to the cooldown set
    assert svc.name not in ah._STATE.disabled_until


def test_docker_error_rolls_back_reservation():
    """If force_update raises, the reservation must be rolled back so
    a later attempt has full quota. Otherwise a cluster-wide Swarm
    outage would silently burn all 10 global heals on transient
    errors."""
    ah = _fresh_autoheal()
    svc = _FakeService("yral-rishi-agent")
    client = _FakeClient([svc])
    client.services.raise_on_get = True

    result = ah.try_heal(client, svc.name, "dns", now=1000.0)
    assert result.kind == "docker_error"

    # Quota rolled back — a successful subsequent call should count
    # as the first (not the second) heal.
    client.services.raise_on_get = False
    from collections import deque

    dq = ah._STATE.heals_per_service.get(svc.name, deque())
    assert len(dq) == 0, "docker_error must not consume the per-service quota"


# ─── DNS alias resolution ───────────────────────────────────────────────


def test_resolve_service_by_dns_alias_substring_match():
    """Aliases like 'patroni-rishi-4' need to resolve to service
    'yral-v2-patroni_patroni-rishi-4' — substring match is the
    documented approach."""
    ah = _fresh_autoheal()
    svc = _FakeService("yral-v2-patroni_patroni-rishi-4")
    client = _FakeClient([svc, _FakeService("unrelated-service")])

    found = ah.resolve_service_by_dns_alias(client, "patroni-rishi-4")
    assert found is svc

    # Nothing to match → None (caller degrades gracefully)
    missing = ah.resolve_service_by_dns_alias(client, "nonexistent-alias")
    assert missing is None


# ─── watchdog wire-up source-pin ────────────────────────────────────────


def test_watchdog_hooks_autoheal_on_dns_missing_transition():
    """A future refactor that drops the autoheal hook in the DNS loop
    would silently regress the whole extension. Pin the wire-up."""
    src = (REPO / "watchdog" / "watchdog.py").read_text()
    assert "import autoheal" in src
    assert "_try_autoheal_for_dns(_get_docker_client(), a)" in src
    # Startup-discovery MUST NOT auto-heal — pin the intentional
    # "transition only" comment so a future edit sees the reasoning.
    assert "do NOT auto-heal on the startup discovery" in src


def test_watchdog_hooks_autoheal_on_drift_transition():
    """Drift loop must fire autoheal on kind='drift' events only
    (recovery events don't need healing)."""
    src = (REPO / "watchdog" / "watchdog.py").read_text()
    assert 'if a.kind == "drift":' in src
    assert "autoheal.try_heal(" in src
