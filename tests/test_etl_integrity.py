"""Phase 3 — V2 integrity verifier pinning.

The live verifier behavior runs after deploy; here we pin static
configuration + the filename regex + the dispatch table."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_to_naive_utc_strips_tzinfo():
    """For TIMESTAMP (no tz) columns. asyncpg's codec rejects tz-aware
    datetimes with 'can't subtract offset-naive and offset-aware'. The
    adapter must produce a tz-NAIVE result preserving UTC wall-clock."""
    from datetime import datetime, timezone, timedelta
    from services.etl_integrity import _to_naive_utc

    # tz-aware UTC → naive UTC (same wall-clock)
    aware = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    naive = _to_naive_utc(aware)
    assert naive.tzinfo is None
    assert naive.year == 2026 and naive.hour == 12

    # tz-aware in IST → naive UTC (wall-clock converted)
    ist = datetime(
        2026, 5, 30, 17, 30, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))
    )
    naive2 = _to_naive_utc(ist)
    assert naive2.tzinfo is None
    assert naive2.hour == 12  # 17:30 IST = 12:00 UTC

    # ISO string (no tz, postgres wire format) → naive UTC
    naive3 = _to_naive_utc("2026-05-30 12:00:00.123456")
    assert naive3.tzinfo is None and naive3.hour == 12

    # ISO string (with tz) → naive UTC
    naive4 = _to_naive_utc("2026-05-30T17:30:00+05:30")
    assert naive4.tzinfo is None and naive4.hour == 12


def test_to_aware_utc_attaches_tzinfo():
    """For TIMESTAMPTZ columns. asyncpg's codec requires tz-aware
    datetimes. The adapter must always produce a tz-aware result."""
    from datetime import datetime, timezone
    from services.etl_integrity import _to_aware_utc

    # naive → assume UTC, attach tzinfo
    naive = datetime(2026, 5, 30, 12, 0, 0)
    aware = _to_aware_utc(naive)
    assert aware.tzinfo == timezone.utc
    assert aware.hour == 12

    # ISO string with offset → normalize to UTC tzinfo
    aware2 = _to_aware_utc("2026-05-30T17:30:00+05:30")
    assert aware2.tzinfo == timezone.utc
    assert aware2.hour == 12

    # ISO string without tz → assume UTC
    aware3 = _to_aware_utc("2026-05-30T12:00:00")
    assert aware3.tzinfo == timezone.utc


def test_record_does_not_pass_string_to_timestamptz_column():
    """Regression for the bug where _record passed snapshot_iso (str)
    to TIMESTAMPTZ. asyncpg's codec rejects strings even with $::cast
    in the SQL. The fix is _to_aware_utc on the param."""
    import inspect
    from services.etl_integrity import _record

    src = inspect.getsource(_record)
    # The param must be converted before reaching the execute call
    assert "_to_aware_utc(snapshot_iso)" in src


def test_verify_hourly_uses_naive_utc():
    """`created_at` columns are TIMESTAMP (no tz); _to_naive_utc keeps
    asyncpg's codec happy. (The sister `_verify_tick` test was dropped
    along with the tick verifier itself on 2026-06-26 — see
    `_verify_hourly` docstring.)"""
    import inspect
    from services.etl_integrity import _verify_hourly

    src = inspect.getsource(_verify_hourly)
    assert "_to_naive_utc" in src


def test_tick_verifier_no_longer_exposed():
    """Tick verifier dropped 2026-06-26 (Sentry YRAL-RISHI-AGENT-56).
    A future PR that re-introduces it without re-reading the
    `_verify_hourly` architectural comment will trip this test."""
    from services import etl_integrity

    assert not hasattr(etl_integrity, "_verify_tick"), (
        "tick verifier was deliberately removed — its 5-min window can't "
        "distinguish ETL-not-arrived from v2-native-write. Re-add only "
        "after solving that problem."
    )
    assert not hasattr(etl_integrity, "TICK_DRIFT_TOLERANCE")


def test_canonicalize_for_compare_microsecond_precision():
    """Regression for the actual mismatch seen in production Layer 2
    sample (2026-05-30): chat-ai sent '2026-03-18T01:48:25.53554' (5
    digit μs, trailing zero trimmed by Postgres row_to_json); V2 had
    '2026-03-18T01:48:25.535540' (6 digit μs from asyncpg datetime
    isoformat). Same instant — must canonicalize to equal."""
    from services.etl_integrity import _canonicalize_for_compare

    chat_ai = "2026-03-18T01:48:25.53554"  # 5 digits
    v2 = "2026-03-18T01:48:25.535540"  # 6 digits
    assert _canonicalize_for_compare(chat_ai) == _canonicalize_for_compare(v2)


def test_canonicalize_handles_datetime_vs_str_cross():
    """V2 reads via asyncpg → datetime; chat-ai S3 JSON → str. Both
    must canonicalize to the same bytes when they represent the same
    instant."""
    from datetime import datetime, timezone
    from services.etl_integrity import _canonicalize_for_compare

    dt = datetime(2026, 3, 18, 1, 48, 25, 535540, tzinfo=timezone.utc)
    s = "2026-03-18T01:48:25.53554"
    assert _canonicalize_for_compare(dt) == _canonicalize_for_compare(s)


def test_canonicalize_passes_non_timestamp_strings_through():
    """UUIDs, plain text, JSON blobs — anything that isn't a timestamp
    string must round-trip unchanged so the comparison is meaningful
    for those columns too."""
    from services.etl_integrity import _canonicalize_for_compare

    assert _canonicalize_for_compare("cc792feb-7a2c-4d8d") == "cc792feb-7a2c-4d8d"
    assert _canonicalize_for_compare("plain text") == "plain text"
    assert _canonicalize_for_compare(None) is None
    assert _canonicalize_for_compare("ai_chat") == "ai_chat"


def test_canonicalize_handles_postgres_space_format():
    """Postgres's default text format uses a space separator; ISO uses
    T. Both must canonicalize to the same string."""
    from services.etl_integrity import _canonicalize_for_compare

    a = _canonicalize_for_compare("2026-03-18 01:48:25.535540")
    b = _canonicalize_for_compare("2026-03-18T01:48:25.535540")
    assert a == b


def test_canonicalize_tz_aware_vs_naive_yield_same_string():
    """If chat-ai's column is naive UTC and V2's is naive UTC, both
    forms must produce the same canonical output. The canonical form
    attaches UTC to naive inputs (Postgres stores UTC by convention)."""
    from datetime import datetime, timezone
    from services.etl_integrity import _canonicalize_for_compare

    naive = datetime(2026, 3, 18, 1, 48, 25, 535540)
    aware = datetime(2026, 3, 18, 1, 48, 25, 535540, tzinfo=timezone.utc)
    assert _canonicalize_for_compare(naive) == _canonicalize_for_compare(aware)


def test_filename_regex_recognizes_all_four_layers():
    """The exporter emits four families of files into _integrity/.
    Regex still accepts `tick` so the dispatch loop can recognize +
    skip-silently (tick files keep arriving from rishi-1)."""
    from services.etl_integrity import _FILENAME_RE

    for layer in ("tick", "hourly", "sample", "sentinel"):
        m = _FILENAME_RE.match(f"{layer}_20260529T144530Z.json")
        assert m is not None
        assert m["layer"] == layer

    # Other filenames in the prefix (none expected today) must NOT match.
    assert _FILENAME_RE.match("garbage_20260529T144530Z.json") is None
    # Old non-versioned filenames must NOT match
    assert _FILENAME_RE.match("tick.json") is None


def test_dispatch_table_excludes_tick_layer():
    """Tick verifier dropped 2026-06-26. The dispatch table covers
    hourly + sample + sentinel only; `run_once` skips tick files via
    the `verifier is None` guard."""
    from services.etl_integrity import _VERIFIERS

    assert set(_VERIFIERS.keys()) == {"hourly", "sample", "sentinel"}
    assert "tick" not in _VERIFIERS


def test_dispatch_loop_skips_unknown_layer():
    """Belt-and-braces for the tick-skip path: `verifier is None` →
    `continue` instead of `KeyError`. Pin the guard so a future
    refactor can't accidentally raise on tick files."""
    import inspect
    from services.etl_integrity import run_once

    src = inspect.getsource(run_once)
    assert '_VERIFIERS.get(obj["layer"])' in src
    assert "if verifier is None:" in src


def test_intervals_and_thresholds_sensible():
    """Poll interval ≤ tick emission cadence (5 min), or we miss
    payloads. Initial delay ≥ etl warm time."""
    from services.etl_integrity import (
        INTEGRITY_INTERVAL_SEC,
        INITIAL_DELAY_SEC,
        HOURLY_DRIFT_TOLERANCE,
        SAMPLE_MISMATCH_TOLERANCE,
        SENTINEL_STALENESS_LIMIT_SEC,
    )
    from services.etl_chat_ai import INITIAL_DELAY_SEC as ETL_DELAY

    assert INTEGRITY_INTERVAL_SEC <= 5 * 60
    assert INITIAL_DELAY_SEC >= ETL_DELAY
    # hourly threshold preserved at 50 — the magnitude was correct; the
    # direction was the bug (fixed by the one-sided check in _verify_hourly).
    assert HOURLY_DRIFT_TOLERANCE == 50
    # sample is strict — any content hash mismatch counts
    assert SAMPLE_MISMATCH_TOLERANCE == 0
    # sentinel staleness limit must be > one sync interval to avoid
    # false positives from normal sync lag
    assert SENTINEL_STALENESS_LIMIT_SEC >= 5 * 60


def test_checked_tables_match_etl_synced_tables():
    """If ETL syncs a table but integrity doesn't check it, drift goes
    unobserved. The two lists must track each other."""
    from services.etl_integrity import CHECKED_TABLES
    from services.etl_chat_ai import SYNCED_TABLES

    synced = {t["name"] for t in SYNCED_TABLES}
    checked = set(CHECKED_TABLES)
    assert checked == synced


def test_backcompat_constants_preserved():
    """Old constants kept for compat with anything that was reading
    them. They no longer drive behavior."""
    from services.etl_integrity import (
        MAX_DRIFT_ROWS,
        FAIL_DRIFT_ROWS,
        WARN_SAMPLE_MISMATCHES,
        FAIL_SAMPLE_MISMATCHES,
        SAMPLE_CONVERSATIONS,
    )

    assert 0 < MAX_DRIFT_ROWS < FAIL_DRIFT_ROWS
    assert 0 < WARN_SAMPLE_MISMATCHES < FAIL_SAMPLE_MISMATCHES
    assert SAMPLE_CONVERSATIONS == 20


def test_s3_layout_constants():
    """Bucket + prefix must match the exporter (rishi-1) side. Drift
    here = the verifier looks at the wrong place and sees nothing."""
    from services.etl_integrity import S3_BUCKET, S3_INTEGRITY_PREFIX

    assert S3_BUCKET == "rishi-yral"
    assert S3_INTEGRITY_PREFIX == "yral-chat-ai/incremental-sync/_integrity"


# ─── 2026-06-26 asymmetric-rule regression tests ──────────────────────────
#
# Sentry issue YRAL-RISHI-AGENT-56: the previous symmetric `abs(diff)`
# rule fired 600+ false positives in 8 days because v2 is a peer writer
# (agent.rishi.yral.com routes directly to v2). The rule is now
# one-sided: v2 BEHIND chat-ai = fire (real A4 risk), v2 AHEAD =
# silent (by-design v2-native growth). These two tests pin both ends.


class _StubPoolWithCount:
    """asyncpg-pool-shaped stub. `count` is what fetchval returns —
    we set it per-test to drive both sides of the asymmetry."""

    def __init__(self, count):
        self._count = count

    async def fetchval(self, query, *args):
        return self._count


def test_verify_hourly_silent_when_v2_ahead_by_50000():
    """v2_native traffic grows v2's row count above chat-ai's. The
    rule MUST stay silent — this is the architectural expectation
    documented in `_verify_hourly`'s docstring."""
    import asyncio

    from services.etl_integrity import _verify_hourly

    # chat_ai says 100K rows; v2 has 150K (alpha-team + 100%-prod-flip
    # native traffic). diff = +50_000, way above the 50-row tolerance
    # on the +diff side — but the one-sided rule should NOT fire.
    payload = {
        "watermark_iso": "2026-06-26T03:45:00+00:00",
        "layer_1_row_counts": {"messages": 100_000},
    }
    pool = _StubPoolWithCount(150_000)
    passed, total_drift, details = asyncio.run(_verify_hourly(pool, payload))
    assert passed is True, (
        "v2 ahead of chat-ai is by-design growth, not drift — the rule must stay silent"
    )
    assert total_drift == 0
    assert details["per_table"] == {}


def test_verify_hourly_fires_when_v2_behind_by_51():
    """The one direction the rule cares about: v2 BEHIND chat-ai by
    more than the 50-row tolerance. This is the real A4 signal
    (chat-ai shipped rows that the ETL didn't apply to v2)."""
    import asyncio

    from services.etl_integrity import _verify_hourly

    # chat_ai says 100,051; v2 has 100,000. diff = -51, just past
    # the tolerance — fire.
    payload = {
        "watermark_iso": "2026-06-26T03:45:00+00:00",
        "layer_1_row_counts": {"messages": 100_051},
    }
    pool = _StubPoolWithCount(100_000)
    passed, total_drift, details = asyncio.run(_verify_hourly(pool, payload))
    assert passed is False, (
        "v2 behind chat-ai = real ETL dropped-row risk — the rule must fire"
    )
    assert total_drift == 51
    assert details["per_table"]["messages"]["diff"] == -51


def test_verify_hourly_silent_at_negative_tolerance_boundary():
    """diff = -50 is exactly at the tolerance — must NOT fire (strict
    inequality `< -50` in the new code; equality stays silent)."""
    import asyncio

    from services.etl_integrity import _verify_hourly

    payload = {
        "watermark_iso": "2026-06-26T03:45:00+00:00",
        "layer_1_row_counts": {"messages": 100_050},
    }
    pool = _StubPoolWithCount(100_000)
    passed, total_drift, details = asyncio.run(_verify_hourly(pool, payload))
    assert passed is True
    assert total_drift == 0
