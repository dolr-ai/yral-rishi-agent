"""Phase 5.6 — streak tracker.

Pure-function pins. The SQL update is exercised against the live cluster via
the deploy verification step.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_interval_is_daily():
    """24h cycle. Below this we'd thrash the DB; above it streaks lag a day."""
    from services.streak_tracker import STREAK_UPDATE_INTERVAL_SEC

    assert STREAK_UPDATE_INTERVAL_SEC == 24 * 60 * 60


def test_initial_delay_avoids_startup_thrash():
    """5 min startup delay so rolling deploys don't fire an immediate scan."""
    from services.streak_tracker import INITIAL_DELAY_SEC

    assert INITIAL_DELAY_SEC >= 60


def test_streak_block_silent_below_3():
    """Streaks 0-2 days are not interesting enough to mention; an empty
    block keeps the proactive prompt clean."""
    from services.proactive import _streak_block

    assert _streak_block(0) == ""
    assert _streak_block(1) == ""
    assert _streak_block(2) == ""


def test_streak_block_mentions_3_day_streak_optionally():
    from services.proactive import _streak_block

    block = _streak_block(3)
    assert "3 days in a row" in block
    assert "Optional" in block or "optional" in block.lower()


def test_streak_block_warmly_acknowledges_7_plus():
    """7+ days is a real signal; prompt nudges Gemini to acknowledge it
    without going overboard."""
    from services.proactive import _streak_block

    block = _streak_block(7)
    assert "7 days in a row" in block
    assert "warm" in block.lower() or "naturally" in block.lower()

    block30 = _streak_block(30)
    assert "30 days in a row" in block30


# ─── 2026-06-04 — empty-exception logging hygiene ───────────────────────


def _read_streak_source():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    return (repo / "app/services/streak_tracker.py").read_text()


def test_streak_loop_logs_exception_type_and_repr():
    """Some asyncpg / Redis errors stringify to "". Previous
    `f"...: {e}"` made the log line entirely blank — operator had
    nothing to triage. Pin the type-name + repr so the level line
    always carries signal."""
    src = _read_streak_source()
    assert "logger.exception(" in src, (
        "streak_loop must use logger.exception for traceback"
    )
    assert "type(e).__name__" in src
    assert "%r" in src or "{e!r}" in src, (
        "repr must appear so empty-str exceptions still log"
    )


def test_asyncpg_result_parse_failure_is_logged():
    """When pool.execute returns something we can't parse as 'UPDATE N',
    don't silently fall through to -1 — log the raw result so we can
    see what asyncpg actually sent."""
    src = _read_streak_source()
    # Both parse sites (updated + reset) must warn.
    assert src.count("could not parse asyncpg result") >= 2, (
        "both result/result2 parse fallbacks must log the raw value"
    )
