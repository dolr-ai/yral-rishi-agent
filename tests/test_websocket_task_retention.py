"""Regression test for the WebSocket fire-and-forget GC race.

Sentry issues #40 / #42 / #46 / #48 / #53 / #61 / #91 / #229 / #242 /
#243 — all fingerprints of "Task was destroyed but it is pending!"
firing ~190x/24h pre-fix. Root cause: bare `asyncio.create_task(...)`
sites in chat.py let the GC collect the task mid-flight once the
request handler returned + the local reference dropped. Mobile users
silently missed the "new message" WebSocket push.

Fix: `websocket_manager.spawn(coro)` keeps a strong reference in a
module-level set + auto-discards on done-callback.

Tests below pin both the contract (spawn exists, behaves right) and
the regression (a fire-and-forget task survives request-handler exit
+ a forced GC pass). The GC-race test is the load-bearing one — it
fails on the old `asyncio.create_task` shape, passes on the new
`spawn` shape.
"""

import asyncio
import gc
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]

# The behavioural tests import websocket_manager → fastapi. CI has it;
# the local dev box often doesn't. Skip gracefully there.
try:
    import fastapi  # noqa: F401

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

requires_fastapi = pytest.mark.skipif(
    not _FASTAPI_AVAILABLE, reason="fastapi not installed (CI only)"
)


# ─── source-pin ─────────────────────────────────────────────────────────


def test_spawn_helper_present_in_websocket_manager():
    """The helper must exist with the documented shape — a future
    refactor that drops it will reintroduce the GC race."""
    src = (REPO / "app" / "services" / "websocket_manager.py").read_text()
    assert "_BACKGROUND_TASKS: set[asyncio.Task] = set()" in src
    assert "def spawn(coro)" in src
    assert "_BACKGROUND_TASKS.add(task)" in src
    assert "task.add_done_callback(_BACKGROUND_TASKS.discard)" in src


def test_chat_routes_use_spawn_not_bare_create_task():
    """Every fire-and-forget site in chat.py must go through
    `websocket_manager.spawn` — bare `asyncio.create_task` for
    fire-and-forget brings the GC race back. `asyncio.gather` is
    still fine (the await keeps the reference live)."""
    src = (REPO / "app" / "routes" / "chat.py").read_text()
    # Only allowed asyncio.* use today is gather. Any new
    # asyncio.create_task in this file = regression candidate.
    assert "asyncio.create_task" not in src, (
        "fire-and-forget tasks in chat.py must go through "
        "websocket_manager.spawn for GC-safety"
    )
    # Sanity: at least the five known spawn sites are still present.
    assert src.count("websocket_manager.spawn(") >= 5


# ─── behavioural — the spawn helper itself ──────────────────────────────


@requires_fastapi
def test_spawn_returns_running_task_and_retains_reference():
    """The returned Task must be live; the module-level set must
    contain it until the coro completes."""
    from services import websocket_manager

    async def _run():
        async def _payload():
            await asyncio.sleep(0)
            return "done"

        task = websocket_manager.spawn(_payload())
        # Retention: the set contains the task while pending.
        assert task in websocket_manager._BACKGROUND_TASKS
        result = await task
        # Done-callback discards it from the set on completion.
        assert task not in websocket_manager._BACKGROUND_TASKS
        return result

    assert asyncio.run(_run()) == "done"


@requires_fastapi
def test_spawn_survives_caller_scope_exit_and_gc_pressure():
    """The load-bearing regression test. Pre-fix shape (bare
    `asyncio.create_task` with the task assigned only to a local
    variable that goes out of scope before the await completes) lets
    the GC reap the task → "Task was destroyed but it is pending!"
    in prod.

    This test reproduces that exact pattern: spawn a task whose body
    flips a flag after one event-loop turn, drop the caller's local
    reference, force gc.collect(), then yield enough for the task to
    run. If the spawn helper works, the flag flips; if a future
    refactor reverts spawn to bare create_task, the GC kills the
    task and the flag stays False."""
    from services import websocket_manager

    flipped = []  # mutable so closure can write to it without nonlocal

    async def _run():
        async def _payload():
            # One event-loop hop to mirror the prod broadcast path:
            # spawn() returns immediately, but the broadcast does
            # `await ws.send_text(...)` which yields to the loop.
            await asyncio.sleep(0)
            flipped.append(True)

        def _caller():
            # The task reference lives only inside this function call.
            # On return, the outer scope has NO strong ref to the task.
            websocket_manager.spawn(_payload())

        _caller()
        # Force the GC to run BEFORE the task gets a chance to execute.
        # With a bare create_task this would collect the task and
        # cancel it; with spawn() the module-level set holds it alive.
        gc.collect()
        # Yield a few times so the task body actually runs.
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(_run())
    assert flipped == [True], (
        "spawn()'d task was destroyed before completing — the "
        "_BACKGROUND_TASKS retention set is broken or has regressed"
    )


@requires_fastapi
def test_spawn_concurrent_tasks_all_complete():
    """Multiple spawn()'d tasks fired back-to-back must all complete
    + all get discarded from the retention set."""
    from services import websocket_manager

    counter = [0]

    async def _bump():
        await asyncio.sleep(0)
        counter[0] += 1

    async def _run():
        tasks = [websocket_manager.spawn(_bump()) for _ in range(10)]
        # All retained while pending.
        assert all(t in websocket_manager._BACKGROUND_TASKS for t in tasks)
        await asyncio.gather(*tasks)
        # All discarded after done.
        assert not any(t in websocket_manager._BACKGROUND_TASKS for t in tasks)

    asyncio.run(_run())
    assert counter[0] == 10


@requires_fastapi
def test_spawn_does_not_swallow_exceptions():
    """Behavior pin: if the spawned coro raises, the exception lives
    on the Task and surfaces when awaited — same as bare create_task
    semantics. Spawn is purely a retention helper, not an error
    handler. A future PR that wraps the inner coro in a try/except
    would change debugging visibility."""
    from services import websocket_manager

    async def _run():
        async def _payload():
            raise RuntimeError("intentional")

        task = websocket_manager.spawn(_payload())
        try:
            await task
        except RuntimeError as e:
            assert str(e) == "intentional"
        else:
            raise AssertionError("expected RuntimeError to propagate")
        # And the set has been discarded.
        assert task not in websocket_manager._BACKGROUND_TASKS

    asyncio.run(_run())
