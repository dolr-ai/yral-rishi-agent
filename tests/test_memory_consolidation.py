"""Phase 4.8 — nightly consolidation. Pins the merge threshold + stats shape."""


def test_merge_threshold_in_reasonable_range():
    """0.08 is the post-tuning value. Below 0.05 we'd merge too aggressively
    (genuine near-misses); above 0.2 we'd never merge in practice. Guard
    against accidental refactor that moves this to 0 or 1."""
    from services.memory_consolidation import MERGE_DISTANCE_THRESHOLD

    assert 0.0 < MERGE_DISTANCE_THRESHOLD < 0.3


def test_interval_is_daily():
    """The whole point of "nightly" — guard against accidentally setting to
    a small value that would hammer Postgres."""
    from services.memory_consolidation import CONSOLIDATION_INTERVAL_SEC

    assert CONSOLIDATION_INTERVAL_SEC == 24 * 60 * 60


def test_initial_delay_avoids_startup_thrash():
    """Don't slam Postgres on every container restart — 10 min lets the
    container warm up first."""
    from services.memory_consolidation import INITIAL_DELAY_SEC

    assert INITIAL_DELAY_SEC >= 60
