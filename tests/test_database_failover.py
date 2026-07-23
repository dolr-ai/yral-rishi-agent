"""Tests for the DATABASE_URL multi-host failover in app.database.

Reproduces the 2026-07-23 outage class: a Patroni replica wedged in
"starting up" sat first in DATABASE_URL, and asyncpg's own multi-host
connect raised CannotConnectNowError instead of failing over to the
healthy leader — so every app connection 503'd. _connect_with_failover
must skip a starting-up (or read-only) host and reach the leader.

Async cases run via asyncio.run (no pytest-asyncio in this repo).
"""

import asyncio

import asyncpg
import pytest

from app import database


# ─── _host_list parsing ──────────────────────────────────────────────────


def test_host_list_three_hosts():
    url = (
        "postgresql://postgres:secret@patroni-rishi-4:5432,"
        "patroni-rishi-5:5432,patroni-rishi-6:5432/yral_agent_db"
        "?sslmode=require&target_session_attrs=read-write"
    )
    assert database._host_list(url) == [
        ("patroni-rishi-4", 5432),
        ("patroni-rishi-5", 5432),
        ("patroni-rishi-6", 5432),
    ]


def test_host_list_password_containing_at_sign():
    # rsplit('@') must not be fooled by an '@' in the password.
    url = "postgresql://user:p@ss@h1:5432,h2:6543/db"
    assert database._host_list(url) == [("h1", 5432), ("h2", 6543)]


def test_host_list_defaults_port():
    url = "postgresql://u:p@only-host/db"
    assert database._host_list(url) == [("only-host", 5432)]


# ─── _connect_with_failover ──────────────────────────────────────────────

URL = "postgresql://u:p@h1:5432,h2:5432,h3:5432/db?target_session_attrs=read-write"
HOSTS = [("h1", 5432), ("h2", 5432), ("h3", 5432)]


def _fake_connect(behaviour):
    """Build a fake asyncpg.connect. `behaviour` maps host -> outcome; the
    default (no host kwarg) drives the normal multi-host path."""
    calls = []

    async def fake(dsn=None, host=None, port=None, **kwargs):
        calls.append(host)
        outcome = behaviour.get(host)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    fake.calls = calls
    return fake


def test_normal_path_unchanged(monkeypatch):
    # No host wedged: the primary asyncpg multi-host connect succeeds and
    # we never enter the per-host loop (host kwarg stays None).
    fake = _fake_connect({None: "leader-conn"})
    monkeypatch.setattr(database.asyncpg, "connect", fake)
    conn = asyncio.run(database._connect_with_failover(URL, HOSTS))
    assert conn == "leader-conn"
    assert fake.calls == [None]  # only the primary path ran


def test_skips_starting_up_first_host(monkeypatch):
    # The exact incident: primary connect raises CannotConnectNow; h1 is
    # still starting up, h2 is a read-only replica, h3 is the leader.
    behaviour = {
        None: asyncpg.CannotConnectNowError("the database system is starting up"),
        "h1": asyncpg.CannotConnectNowError("the database system is starting up"),
        "h2": asyncpg.TargetServerAttributeNotMatched("read-only"),
        "h3": "leader-conn",
    }
    fake = _fake_connect(behaviour)
    monkeypatch.setattr(database.asyncpg, "connect", fake)
    conn = asyncio.run(database._connect_with_failover(URL, HOSTS))
    assert conn == "leader-conn"
    assert fake.calls == [None, "h1", "h2", "h3"]


def test_skips_unreachable_host(monkeypatch):
    behaviour = {
        None: asyncpg.CannotConnectNowError("starting up"),
        "h1": ConnectionRefusedError(),
        "h2": "leader-conn",
        "h3": "leader-conn",
    }
    fake = _fake_connect(behaviour)
    monkeypatch.setattr(database.asyncpg, "connect", fake)
    conn = asyncio.run(database._connect_with_failover(URL, HOSTS))
    assert conn == "leader-conn"
    assert fake.calls == [None, "h1", "h2"]  # stops at first leader


def test_all_hosts_bad_raises(monkeypatch):
    behaviour = {
        None: asyncpg.CannotConnectNowError("starting up"),
        "h1": asyncpg.CannotConnectNowError("starting up"),
        "h2": asyncpg.CannotConnectNowError("starting up"),
        "h3": asyncpg.TargetServerAttributeNotMatched("read-only"),
    }
    fake = _fake_connect(behaviour)
    monkeypatch.setattr(database.asyncpg, "connect", fake)
    with pytest.raises(asyncpg.CannotConnectNowError) as exc:
        asyncio.run(database._connect_with_failover(URL, HOSTS))
    # the aggregated error names every host it tried
    assert "h1" in str(exc.value) and "h3" in str(exc.value)
