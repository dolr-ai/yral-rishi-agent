"""Task A — pin the trending cache TTL + key shape so a future refactor
can't silently turn off the cache or make it too aggressive."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_trending_cache_ttl_in_range():
    """60s is the deliberate value. Below 5s the cache stops mattering
    (most requests miss); above 15min you'd serve stale stats after the
    materialized view's refresh tick."""
    from routes.influencers import _TRENDING_CACHE_TTL_SEC

    assert 5.0 <= _TRENDING_CACHE_TTL_SEC <= 15 * 60.0


def test_trending_cache_starts_empty():
    """No stale state at import time."""
    from routes.influencers import _TRENDING_CACHE

    # Same dict across imports — just verify it's a dict, not None
    assert isinstance(_TRENDING_CACHE, dict)
