"""The pool must be built once, however many callers race for it.

This is the 2026-09-25 outage. Several background loops call get_pool()
concurrently; with no lock they all saw `_pool is None`, all called
create_pool(), and all but the last leaked a live pool that nothing could
close. 98 idle connections accumulated across the two Postgres replicas until
no new pool could be created at all — and because /health/live never touches
the database, the containers went on reporting themselves healthy throughout.

These drive the real get_pool() with a fake asyncpg.create_pool and count how
many pools get built. Verified to fail against the unguarded version.

Written with plain `asyncio.run` rather than an async test plugin: this repo
has no other async tests and CI carries no pytest-asyncio, so a marker-based
test silently does not run there.
"""

import asyncio

import pytest

import database


class FakePool:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset_pool_state(monkeypatch):
    monkeypatch.setattr(database, "_pool", None)
    monkeypatch.setattr(database, "_pool_lock", asyncio.Lock())
    monkeypatch.setattr(
        database, "_read_database_url", lambda: "postgresql://u:p@h:5432/d"
    )


def install_counting_create_pool(monkeypatch, delay=0.02):
    """create_pool slow enough for racers to pile up behind it."""
    built = []

    async def fake_create_pool(**_kwargs):
        await asyncio.sleep(delay)  # the window the real bug raced through
        pool = FakePool()
        built.append(pool)
        return pool

    monkeypatch.setattr(database.asyncpg, "create_pool", fake_create_pool)
    return built


def test_twenty_concurrent_callers_build_exactly_one_pool(monkeypatch):
    built = install_counting_create_pool(monkeypatch)

    async def race():
        return await asyncio.gather(*(database.get_pool() for _ in range(20)))

    pools = asyncio.run(race())

    assert len(built) == 1, (
        f"{len(built)} pools built for 20 concurrent callers — "
        f"{len(built) - 1} leaked with live connections and no way to close them"
    )
    assert all(p is pools[0] for p in pools), "callers got different pools"


def test_a_later_caller_reuses_the_pool(monkeypatch):
    built = install_counting_create_pool(monkeypatch)

    async def twice():
        return await database.get_pool(), await database.get_pool()

    first, second = asyncio.run(twice())

    assert first is second
    assert len(built) == 1


def test_a_failed_creation_leaves_no_pool_behind(monkeypatch):
    """If create_pool raises, nothing half-built may keep connections."""

    async def failing_create_pool(**_kwargs):
        raise OSError("postgres unreachable")

    monkeypatch.setattr(database.asyncpg, "create_pool", failing_create_pool)

    with pytest.raises(OSError):
        asyncio.run(database.get_pool())
    assert database._pool is None

    # ...and the next attempt still succeeds, rather than being wedged.
    built = install_counting_create_pool(monkeypatch)
    assert asyncio.run(database.get_pool()) is built[0]


def test_racing_callers_after_a_failure_still_build_only_one(monkeypatch):
    """The real shape of the outage: the pool dies, then everything retries."""
    install_counting_create_pool(monkeypatch)
    asyncio.run(database.get_pool())

    database._pool = None  # the pool goes away, as after each database blip
    built = install_counting_create_pool(monkeypatch)

    async def race():
        await asyncio.gather(*(database.get_pool() for _ in range(15)))

    asyncio.run(race())
    assert len(built) == 1, f"{len(built)} pools rebuilt after a failure"


def test_close_pool_clears_it_so_the_next_call_rebuilds(monkeypatch):
    built = install_counting_create_pool(monkeypatch)

    async def open_close_open():
        first = await database.get_pool()
        await database.close_pool()
        assert first.closed is True
        assert database._pool is None
        return first, await database.get_pool()

    first, second = asyncio.run(open_close_open())

    assert second is not first
    assert len(built) == 2
