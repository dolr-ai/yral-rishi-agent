"""Regression tests for the watchdog external heartbeat.

Reproduces the 2026-08-08 blind-spot without touching the network: the
Sentry surface returns 500 while every other endpoint is fine, and the
heartbeat must report FAILURE outward rather than staying quiet.

The pure functions (`check_endpoint`, `all_healthy`, `ping_url`, `beat`)
are the whole test surface — `run_forever` is a sleep loop around `beat`,
so if `beat` is right the loop is right too. Same split the replica-drift
tests use.
"""

# ─── fake HTTP opener ───────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener_for(status_by_url: dict, record: list | None = None):
    """Build an opener over a {url: status_or_Exception} map. Anything not
    in the map raises, which models connection-refused."""

    def _opener(url: str, timeout: int):
        if record is not None:
            record.append(url)
        outcome = status_by_url.get(url)
        if outcome is None:
            raise OSError(f"connection refused: {url}")
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResponse(outcome)

    return _opener


# ─── check_endpoint ─────────────────────────────────────────────────────


def test_2xx_is_healthy():
    import heartbeat as hb

    opener = _opener_for({"http://a": 200})
    assert hb.check_endpoint("http://a", opener) is True


def test_500_is_unhealthy():
    """The exact 2026-08-08 signature — Sentry answered, but with a 500."""
    import heartbeat as hb

    opener = _opener_for({"http://a": 500})
    assert hb.check_endpoint("http://a", opener) is False


def test_connection_error_is_unhealthy():
    import heartbeat as hb

    opener = _opener_for({})
    assert hb.check_endpoint("http://gone", opener) is False


# ─── all_healthy ────────────────────────────────────────────────────────


def test_all_healthy_requires_every_endpoint():
    """AND, not majority: a half-broken fleet must not read as fine."""
    import heartbeat as hb

    opener = _opener_for({"http://a": 200, "http://b": 500})
    assert hb.all_healthy(["http://a", "http://b"], opener) is False
    assert hb.all_healthy(["http://a"], opener) is True


# ─── ping_url ───────────────────────────────────────────────────────────


def test_ping_url_success_and_fail_shapes():
    import heartbeat as hb

    assert hb.ping_url("https://hc.example/uuid", True) == "https://hc.example/uuid"
    assert (
        hb.ping_url("https://hc.example/uuid", False) == "https://hc.example/uuid/fail"
    )


def test_ping_url_tolerates_trailing_slash():
    import heartbeat as hb

    assert (
        hb.ping_url("https://hc.example/uuid/", False) == "https://hc.example/uuid/fail"
    )


# ─── beat — the 2026-08-08 scenario end to end ──────────────────────────


def test_beat_pings_success_when_all_up():
    import heartbeat as hb

    calls: list = []
    opener = _opener_for(
        {
            "https://sentry/_health/": 200,
            "https://agent/health": 200,
            "https://hc/id": 200,
        },
        record=calls,
    )
    healthy = hb.beat(
        "https://hc/id", ["https://sentry/_health/", "https://agent/health"], opener
    )
    assert healthy is True
    assert calls[-1] == "https://hc/id"


def test_beat_pings_fail_when_sentry_is_down():
    """Sentry 500s, the agent is fine. The switch must be told explicitly
    rather than left to time out — that is the ~45h detection gap."""
    import heartbeat as hb

    calls: list = []
    opener = _opener_for(
        {
            "https://sentry/_health/": 500,
            "https://agent/health": 200,
            "https://hc/id/fail": 200,
        },
        record=calls,
    )
    healthy = hb.beat(
        "https://hc/id", ["https://sentry/_health/", "https://agent/health"], opener
    )
    assert healthy is False
    assert calls[-1] == "https://hc/id/fail"


def test_beat_never_raises_when_switch_unreachable():
    """If the dead-man's switch itself is unreachable we must not kill the
    thread — silence already trips the alarm."""
    import heartbeat as hb

    opener = _opener_for({"https://sentry/_health/": 200, "https://agent/health": 200})
    assert (
        hb.beat(
            "https://hc/unreachable",
            ["https://sentry/_health/", "https://agent/health"],
            opener,
        )
        is True
    )


def test_run_forever_is_noop_without_url(monkeypatch):
    """Unconfigured deploys must behave exactly as before this change."""
    import heartbeat as hb

    monkeypatch.setattr(hb, "URL", "")
    hb.run_forever()  # returns immediately instead of looping
