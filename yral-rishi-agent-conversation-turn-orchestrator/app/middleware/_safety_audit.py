# ---------------------------------------------------------------------------
# _safety_audit.py — ContextVar-based audit trail for safety middleware order
# verification. Production no-op; tests inject a list to read back.
#
# ⭐ START HERE: this module exposes ONE ContextVar (`SAFETY_AUDIT_TRAIL`)
# and ONE helper (`record(marker)`). Each safety middleware calls
# `record("H5_entry")` / `record("H5_exit")` etc. around its `call_next`.
# When `SAFETY_AUDIT_TRAIL.get()` returns None (the default — production
# behaviour), `record()` is a no-op: one `get()` + one `is None` check
# per middleware per request. Negligible.
#
# When a test sets the ContextVar to a list via `.set([])`, each
# middleware's `record(...)` calls APPEND to that list. The test then
# reads the list back after the request returns to verify
# entry/exit ordering matches the documented LIFO chain.
#
# WHY ContextVar + NOT request.state?
# `request.state` lives on the FastAPI `Request` object — it's only
# accessible from inside the request lifecycle. Tests that use
# `TestClient` get back a `Response`, not the `Request` — so they
# can't inspect `request.state` after the call returns. ContextVar
# values, by contrast, are shared by reference into the request task
# (asyncio.Task captures the ContextVar copy at task creation); the
# outer test code that set the ContextVar still holds the same list
# reference and reads the mutations made inside the request task.
#
# WHY A LEADING-UNDERSCORE FILENAME?
# Marks the module as INTERNAL to `app/middleware/`. Other services
# spawned from the same template shouldn't import `_safety_audit` —
# the cross-service contract is the middleware behaviour, not the
# audit trail mechanism. Tests within THIS service may import it.
#
# WHY NOT JUST USE A MODULE-LEVEL LIST?
# A module-level list would be shared across concurrent requests + test
# runs. ContextVar isolates per-task state, so parallel pytest runs +
# concurrent FastAPI workers wouldn't trample each other's audit lists.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib `ContextVar` — per-request async-safe context storage. We
# use it so test bodies can opt-in to the audit-trail recording for
# the order-verification test WITHOUT changing the production code
# path (default `None` → middleware writes never happen in prod).
from contextvars import ContextVar

# `Final` marks the ContextVar handle as immutable. The list it
# holds is mutable; the handle itself is not.
from typing import Final


# The ContextVar holding the optional audit list. `default=None` means
# production code path is: get() → None → no list mutation → no overhead.
# Tests set this to a fresh `[]` before each test; each middleware's
# `record(...)` call appends entry/exit markers.
SAFETY_AUDIT_TRAIL: Final[ContextVar[list[str] | None]] = ContextVar(
    "safety_audit_trail",
    default=None,
)


def record(marker: str) -> None:
    """Append `marker` to the audit trail if a test has set one.

    WHAT: appends `marker` to the ContextVar-held list, or no-ops when
          the ContextVar is at its `None` default.
    WHEN: called by safety middlewares at entry (before `call_next`)
          and exit (after `call_next`), and by `a10_adult_content_filter.py`
          to record the synthetic "handler" marker between its entry
          and exit (since the handler itself is out-of-scope for
          modification per the Day-3 directive).
    WHY:  centralises the "test injects a list, middlewares record"
          pattern in one tiny helper so each middleware's call site is
          ONE line and the no-op path is obvious to readers.
    """
    # Look up the per-request audit list (None in production where
    # the ContextVar's default value isn't overridden).
    trail = SAFETY_AUDIT_TRAIL.get()
    # No-op branch: production code path. List-of-markers absent →
    # no append, no overhead. Tests opt-in by .set()-ing a list at
    # the top of the test body and resetting on teardown.
    if trail is not None:
        # Test path: append the entry/exit marker so the order-
        # verification test can later assert the documented chain.
        trail.append(marker)


# ===========================================================================
# RELATED FILES:
#   __init__.py             — package marker + visual ASCII diagram of
#                             the safety chain
#   h5_prompt_injection.py  — calls record("H5_entry") + record("H5_exit")
#   h4_crisis_detection.py  — calls record("H4_entry") + record("H4_exit")
#   a10_adult_content_filter.py      — calls record("A10_entry"), record("handler"),
#                             record("A10_exit") — A10 owns the synthetic
#                             "handler" marker because the handler itself
#                             is out-of-scope per the Day-3 directive
#   ../../tests/test_safety_stack.py
#                          — sets SAFETY_AUDIT_TRAIL via ContextVar and
#                            asserts on the list contents
# ===========================================================================
