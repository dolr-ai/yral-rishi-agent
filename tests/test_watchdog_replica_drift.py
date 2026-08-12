"""Regression tests for the watchdog replica-drift check.

Reproduces the 2026-06-29 langfuse-web class of failure without
needing a live Swarm: build fake docker-py `Service` objects with
configurable desired-vs-running replica counts, drive `check_once`
across simulated time, and assert the correct Sentry alerts fire
(or don't).

The pure `check_once` function is the whole test surface — the
sleep loop + Sentry emit are trivially thin wrappers in
watchdog.py; if `check_once` is right, the loop is right too.
"""

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


# ─── fake docker-py Service + client ────────────────────────────────────


class _FakeService:
    """Minimal stand-in for `docker.models.services.Service`. Only
    surfaces what `check_once` reads: `.name`, `.attrs['Spec']['Mode']
    ['Replicated']['Replicas']`, and `.tasks(filters=...)`."""

    def __init__(self, name: str, desired: int, running: int) -> None:
        self.name = name
        self.attrs = {"Spec": {"Mode": {"Replicated": {"Replicas": desired}}}}
        self._running = running

    def tasks(self, filters=None):
        # The check only asks for tasks with desired-state=running,
        # so we return _running "running" tasks + one "failed" task
        # to make sure the state filter is doing its job.
        return [{"Status": {"State": "running"}} for _ in range(self._running)] + [
            {"Status": {"State": "failed"}}
        ]


class _FakeGlobalModeService:
    """Global-mode services have no `Replicated` key; the check should
    silently skip them (returning `None` from _desired_replica_count)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.attrs = {"Spec": {"Mode": {"Global": {}}}}

    def tasks(self, filters=None):
        return []


class _FakeClient:
    def __init__(self, services) -> None:
        self._services = services
        self.services = self

    def list(self):
        return list(self._services)


# ─── source-pin ─────────────────────────────────────────────────────────


def test_defaults_match_brief():
    """The brief locks the poll interval + alert threshold + service
    filter. Env-var overrides are fine, but the compile-time defaults
    are the operator's fallback — pin them here."""
    import replica_drift as rd

    assert rd.INTERVAL_SEC == 60
    assert rd.DRIFT_ALERT_SEC == 300
    assert rd.SERVICES_FILTER == "yral-"


def test_watchdog_starts_replica_thread():
    """A future refactor that drops the thread spawn would silently
    revert the whole extension. Pin the wire-up so CI catches that."""
    src = (REPO / "watchdog" / "watchdog.py").read_text()
    assert "_replica_check_loop" in src
    assert "threading.Thread(" in src
    assert "daemon=True" in src
    # DNS-alias code path must remain untouched — pin that the old
    # while True + resolve + alert block is still there.
    assert "resolve(a)" in src
    assert "alias" in src.lower()


def test_stack_file_mounts_docker_sock_readonly_on_manager():
    """The docker.sock mount widens the trust boundary; the mitigations
    (RO, manager placement) are load-bearing. A future edit that flips
    the mount to RW or drops the placement constraint must fail CI."""
    src = (REPO / "bootstrap" / "scripts" / "overlay-watchdog-stack.yml").read_text()
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in src, (
        "docker.sock mount must be read-only"
    )
    assert "node.role == manager" in src, (
        "watchdog placement must pin to manager nodes (worker escalation guard)"
    )


def test_requirements_pins_docker_py():
    """docker-py is what makes the replica check possible. A future
    dependency prune that drops it silently breaks the extension."""
    src = (REPO / "watchdog" / "requirements.txt").read_text()
    assert "docker==" in src


# ─── behavioural — check_once ───────────────────────────────────────────


def test_fresh_drift_below_threshold_does_not_alert():
    """First observation of a service below its desired count sets
    the drift_started_at timestamp but does NOT alert — the brief
    explicitly wants only persistent drift to fire."""
    import replica_drift as rd

    client = _FakeClient([_FakeService("yral-langfuse-web", desired=1, running=0)])
    state: dict[str, rd.DriftState] = {}

    alerts = rd.check_once(client, state, now=1000.0, drift_alert_sec=300)

    assert alerts == []
    st = state["yral-langfuse-web"]
    assert st.drift_started_at == 1000.0
    assert st.alerted is False


def test_persistent_drift_over_threshold_alerts_once():
    """The load-bearing test. Drift starts at t=1000; poll at t=1400
    (400s later, > 300s threshold) fires the drift alert exactly
    once. A follow-up poll while drift continues fires NO extra
    alerts (idempotency)."""
    import replica_drift as rd

    svc = _FakeService("yral-langfuse-web", desired=1, running=0)
    client = _FakeClient([svc])
    state: dict[str, rd.DriftState] = {}

    # t=1000: first observation, no alert
    a1 = rd.check_once(client, state, now=1000.0, drift_alert_sec=300)
    assert a1 == []
    # t=1400: 400s of drift, > 300s threshold → alert
    a2 = rd.check_once(client, state, now=1400.0, drift_alert_sec=300)
    assert len(a2) == 1
    ev = a2[0]
    assert ev.service_name == "yral-langfuse-web"
    assert ev.kind == "drift"
    assert ev.running == 0
    assert ev.desired == 1
    assert ev.duration_sec == 400
    # t=1500: still drifting; no new alert (idempotent).
    a3 = rd.check_once(client, state, now=1500.0, drift_alert_sec=300)
    assert a3 == []
    assert state["yral-langfuse-web"].alerted is True


def test_recovery_after_alerted_drift_emits_recovery():
    """Once we've alerted on a drift and the service comes back to
    running == desired, emit a recovery event so the operator sees
    the close-out. Also clears state so a subsequent re-drift alerts
    fresh."""
    import replica_drift as rd

    svc = _FakeService("yral-langfuse-web", desired=1, running=0)
    client = _FakeClient([svc])
    state: dict[str, rd.DriftState] = {}

    rd.check_once(client, state, now=1000.0, drift_alert_sec=300)
    rd.check_once(client, state, now=1400.0, drift_alert_sec=300)
    assert state["yral-langfuse-web"].alerted is True

    # Recovery: bump running to match desired.
    svc._running = 1
    a3 = rd.check_once(client, state, now=1500.0, drift_alert_sec=300)
    assert len(a3) == 1
    assert a3[0].kind == "recovery"
    assert a3[0].running == 1
    assert a3[0].desired == 1
    # State cleared so a future re-drift starts fresh.
    assert state["yral-langfuse-web"].drift_started_at is None
    assert state["yral-langfuse-web"].alerted is False


def test_short_blip_clears_silently_without_alert_or_recovery():
    """Sub-threshold drift (< 5 min) followed by recovery must NOT
    emit either a drift alert OR a recovery event. Otherwise every
    routine service restart pages Rishi."""
    import replica_drift as rd

    svc = _FakeService("yral-rishi-agent", desired=2, running=1)
    client = _FakeClient([svc])
    state: dict[str, rd.DriftState] = {}

    rd.check_once(client, state, now=1000.0, drift_alert_sec=300)
    # t=1100: 100s of drift (way below threshold)
    a2 = rd.check_once(client, state, now=1100.0, drift_alert_sec=300)
    assert a2 == []

    # Recovery before threshold: no drift alert AND no recovery event.
    svc._running = 2
    a3 = rd.check_once(client, state, now=1120.0, drift_alert_sec=300)
    assert a3 == [], "sub-threshold blip must not emit a recovery event"
    assert state["yral-rishi-agent"].drift_started_at is None


def test_multi_service_independent_tracking():
    """A drift on service A must not clear when service B recovers,
    and vice versa. Each service is tracked with its own state."""
    import replica_drift as rd

    a_svc = _FakeService("yral-langfuse-web", desired=1, running=0)
    b_svc = _FakeService("yral-rishi-agent", desired=3, running=3)
    client = _FakeClient([a_svc, b_svc])
    state: dict[str, rd.DriftState] = {}

    # Both observed; only A is drifting.
    rd.check_once(client, state, now=1000.0, drift_alert_sec=300)
    assert state["yral-langfuse-web"].drift_started_at == 1000.0
    assert state["yral-rishi-agent"].drift_started_at is None

    # A alerts at t=1400; B still healthy.
    alerts = rd.check_once(client, state, now=1400.0, drift_alert_sec=300)
    assert len(alerts) == 1
    assert alerts[0].service_name == "yral-langfuse-web"

    # Now B drifts; A still drifting. Independent tracking.
    b_svc._running = 1
    rd.check_once(client, state, now=1500.0, drift_alert_sec=300)
    assert state["yral-rishi-agent"].drift_started_at == 1500.0
    assert state["yral-langfuse-web"].alerted is True  # unchanged


def test_service_filter_ignores_non_matching_names():
    """The brief pins the yral- prefix so we don't page on amorae or
    other stacks until they're declared v2-critical. A future edit
    that broadens the filter should have to update this test AND the
    docstring."""
    import replica_drift as rd

    matching = _FakeService("yral-rishi-agent", desired=1, running=0)
    ignored = _FakeService("amorae-web", desired=1, running=0)
    client = _FakeClient([matching, ignored])
    state: dict[str, rd.DriftState] = {}

    rd.check_once(client, state, now=1000.0, drift_alert_sec=300)
    assert "yral-rishi-agent" in state
    assert "amorae-web" not in state


def test_global_mode_services_skipped():
    """Global-mode services (e.g. node exporters) have no Replicas
    key. `check_once` should silently skip them rather than tripping
    on a missing dict key."""
    import replica_drift as rd

    replicated = _FakeService("yral-rishi-agent", desired=1, running=1)
    global_svc = _FakeGlobalModeService("yral-node-exporter")
    client = _FakeClient([replicated, global_svc])
    state: dict[str, rd.DriftState] = {}

    alerts = rd.check_once(client, state, now=1000.0, drift_alert_sec=300)

    assert alerts == []
    assert "yral-rishi-agent" in state
    assert "yral-node-exporter" not in state, (
        "global-mode services should not be tracked (no replicas concept)"
    )


def test_removed_services_are_pruned_from_state():
    """A service that disappears between polls (docker stack rm) must
    have its state dropped so the tracking dict doesn't grow forever."""
    import replica_drift as rd

    svc = _FakeService("yral-langfuse-web", desired=1, running=1)
    client = _FakeClient([svc])
    state: dict[str, rd.DriftState] = {}
    rd.check_once(client, state, now=1000.0, drift_alert_sec=300)
    assert "yral-langfuse-web" in state

    # Service goes away.
    client._services = []
    rd.check_once(client, state, now=1100.0, drift_alert_sec=300)
    assert "yral-langfuse-web" not in state
