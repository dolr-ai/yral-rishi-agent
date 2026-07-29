"""Regression tests for the watchdog Patroni leader-presence check.

Reproduces the 2026-07-20 leaderless-cascade class of failure without
needing a live cluster: feed `check_once` the members list that
Patroni's REST `/cluster` returns (leader present, leaderless, and
recovering) and drive the state machine across simulated time.

The pure `check_once` function is the whole test surface — the HTTP
fetch + Sentry emit are trivially thin wrappers in watchdog.py; if
`check_once` is right, the loop is right too. Mirrors
test_watchdog_replica_drift.py.
"""


from patroni_leader import LeaderState, check_once, leader_name  # noqa: E402


# ─── fixtures matching Patroni REST /cluster "members" shape ─────────────


def _members(leader_role="leader"):
    """Healthy three-node cluster. `leader_role=None` makes every
    member a running replica → the leaderless (cluster_unlocked)
    condition from the incident."""
    return [
        {
            "name": "patroni-rishi-4",
            "role": leader_role or "replica",
            "state": "running",
        },
        {"name": "patroni-rishi-5", "role": "replica", "state": "running"},
        {"name": "patroni-rishi-6", "role": "replica", "state": "running"},
    ]


LEADERLESS = _members(leader_role=None)
HEALTHY = _members()


# ─── leader_name helper ──────────────────────────────────────────────────


def test_leader_name_finds_running_leader():
    assert leader_name(HEALTHY) == "patroni-rishi-4"


def test_leader_name_none_when_leaderless():
    assert leader_name(LEADERLESS) is None


def test_leader_name_ignores_non_running_leader():
    # A member claiming the leader role but still "starting" is not a
    # usable leader — the incident's degraded node looked like this.
    members = [{"name": "patroni-rishi-5", "role": "leader", "state": "starting"}]
    assert leader_name(members) is None


def test_leader_name_accepts_standby_leader():
    members = [{"name": "n", "role": "standby_leader", "state": "running"}]
    assert leader_name(members) == "n"


# ─── check_once state machine ────────────────────────────────────────────


def test_healthy_cluster_never_alerts():
    st = LeaderState()
    assert check_once(HEALTHY, st, now=0) == []
    assert check_once(HEALTHY, st, now=1000) == []
    assert st.leaderless_started_at is None


def test_brief_election_stays_silent():
    # Leaderless for less than the threshold, then a leader returns —
    # a normal healthy failover must not page.
    st = LeaderState()
    assert check_once(LEADERLESS, st, now=0, alert_sec=90) == []
    assert check_once(LEADERLESS, st, now=30, alert_sec=90) == []
    assert check_once(HEALTHY, st, now=45, alert_sec=90) == []
    assert st.leaderless_started_at is None


def test_sustained_leaderless_pages_once_then_recovers():
    st = LeaderState()
    # First observation starts the clock, no alert yet.
    assert check_once(LEADERLESS, st, now=0, alert_sec=90) == []
    # Still under threshold.
    assert check_once(LEADERLESS, st, now=60, alert_sec=90) == []
    # Crosses threshold → exactly one leaderless alert.
    alerts = check_once(LEADERLESS, st, now=100, alert_sec=90)
    assert [a.kind for a in alerts] == ["leaderless"]
    assert alerts[0].leader_name is None
    assert alerts[0].member_count == 3
    # Persisting leaderless does not re-page.
    assert check_once(LEADERLESS, st, now=200, alert_sec=90) == []
    # Leader returns → exactly one recovery alert.
    rec = check_once(HEALTHY, st, now=250, alert_sec=90)
    assert [a.kind for a in rec] == ["recovery"]
    assert rec[0].leader_name == "patroni-rishi-4"
    assert st.leaderless_started_at is None


def test_recovery_silent_if_never_alerted():
    # Sub-threshold leaderless that recovers emits nothing at all.
    st = LeaderState()
    check_once(LEADERLESS, st, now=0, alert_sec=90)
    assert check_once(HEALTHY, st, now=30, alert_sec=90) == []


def test_re_drift_after_recovery_pages_again():
    st = LeaderState()
    check_once(LEADERLESS, st, now=0, alert_sec=90)
    assert check_once(LEADERLESS, st, now=100, alert_sec=90)[0].kind == "leaderless"
    check_once(HEALTHY, st, now=150, alert_sec=90)
    # A brand new leaderless episode must alert again.
    check_once(LEADERLESS, st, now=200, alert_sec=90)
    assert check_once(LEADERLESS, st, now=300, alert_sec=90)[0].kind == "leaderless"
