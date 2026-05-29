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
