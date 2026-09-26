"""The pool must be built once, however many callers race for it.

This is the 2026-09-25 outage. Several background loops call get_pool()
concurrently; with no lock they all saw `_pool is None`, all called
create_pool(), and all but the last leaked a live pool that nothing could close.
98 idle connections accumulated across the two Postgres replicas until no new
pool could be created at all — and because /health/live never touches the
database, the containers went on reporting themselves healthy throughout.

These drive the real get_pool() with a fake asyncpg.create_pool and count how
many pools get built. They fail against the unguarded version.
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
    monkeypatch.setattr(database, "_read_database_url", lambda: "postgresql://u:p@h:5432/d")


def install_counting_create_pool(monkeypatch, delay=0.02):
    """create_pool that is slow enough for racers to pile up behind it."""
    built = []

    async def fake_create_pool(**_kwargs):
        await asyncio.sleep(delay)  # the window the real bug raced through
        pool = FakePool()
        built.append(pool)
        return pool

    monkeypatch.setattr(database.asyncpg, "create_pool", fake_create_pool)
    return built


@pytest.mark.asyncio
async def test_twenty_concurrent_callers_build_exactly_one_pool(monkeypatch):
    built = install_counting_create_pool(monkeypatch)

    pools = await asyncio.gather(*(database.get_pool() for _ in range(20)))

    assert len(built) == 1, (
        f"{len(built)} pools built for 20 concurrent callers — "
        f"{len(built) - 1} leaked with live connections and no way to close them"
    )
    assert all(p is pools[0] for p in pools), "callers got different pools"


@pytest.mark.asyncio
async def test_a_later_caller_reuses_the_pool(monkeypatch):
    built = install_counting_create_pool(monkeypatch)

    first = await database.get_pool()
    second = await database.get_pool()

    assert first is second
    assert len(built) == 1


@pytest.mark.asyncio
async def test_a_failed_creation_leaves_no_pool_behind(monkeypatch):
    """If create_pool raises, nothing half-built may keep connections."""

    async def failing_create_pool(**_kwargs):
        raise OSError("postgres unreachable")

    monkeypatch.setattr(database.asyncpg, "create_pool", failing_create_pool)

    with pytest.raises(OSError):
        await database.get_pool()
    assert database._pool is None

    # ...and the next attempt can still succeed, rather than being wedged.
    built = install_counting_create_pool(monkeypatch)
    pool = await database.get_pool()
    assert pool is built[0]


@pytest.mark.asyncio
async def test_racing_callers_after_a_failure_still_build_only_one(monkeypatch):
    """The real shape of the outage: the pool dies, then everything retries."""
    install_counting_create_pool(monkeypatch)
    await database.get_pool()

    # the pool goes away, as it did after each database blip
    database._pool = None
    built = install_counting_create_pool(monkeypatch)

    await asyncio.gather(*(database.get_pool() for _ in range(15)))
    assert len(built) == 1, f"{len(built)} pools rebuilt after a failure"


@pytest.mark.asyncio
async def test_close_pool_clears_it_so_the_next_call_rebuilds(monkeypatch):
    built = install_counting_create_pool(monkeypatch)

    first = await database.get_pool()
    await database.close_pool()

    assert first.closed is True
    assert database._pool is None

    second = await database.get_pool()
    assert second is not first
    assert len(built) == 2
