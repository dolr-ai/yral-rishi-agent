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
    ist = datetime(2026, 5, 30, 17, 30, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
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


def test_verify_tick_uses_naive_utc():
    """Regression for tick-verifier crash: created_at is TIMESTAMP (no
    tz). _to_naive_utc keeps asyncpg happy."""
    import inspect
    from services.etl_integrity import _verify_tick

    src = inspect.getsource(_verify_tick)
    assert "_to_naive_utc" in src


def test_verify_hourly_uses_naive_utc():
    """Same pattern as _verify_tick for the hourly variant."""
    import inspect
    from services.etl_integrity import _verify_hourly

    src = inspect.getsource(_verify_hourly)
    assert "_to_naive_utc" in src


def test_filename_regex_recognizes_all_four_layers():
    """The exporter emits four families of files into _integrity/.
    Drift here = the verifier silently ignores a layer."""
    from services.etl_integrity import _FILENAME_RE

    for layer in ("tick", "hourly", "sample", "sentinel"):
        m = _FILENAME_RE.match(f"{layer}_20260529T144530Z.json")
        assert m is not None
        assert m["layer"] == layer

    # Other filenames in the prefix (none expected today) must NOT match.
    assert _FILENAME_RE.match("garbage_20260529T144530Z.json") is None
    # Old non-versioned filenames must NOT match
    assert _FILENAME_RE.match("tick.json") is None


def test_dispatch_table_covers_all_layers():
    """The dispatch table is what routes a downloaded file to its
    verifier. A missing entry = silently-uncovered layer."""
    from services.etl_integrity import _VERIFIERS

    # Layers in the regex must match layers in the dispatch table
    # (cheap consistency invariant).
    regex_layers = {"tick", "hourly", "sample", "sentinel"}
    assert set(_VERIFIERS.keys()) == regex_layers


def test_intervals_and_thresholds_sensible():
    """Poll interval ≤ tick emission cadence (5 min), or we miss
    payloads. Initial delay ≥ etl warm time."""
    from services.etl_integrity import (
        INTEGRITY_INTERVAL_SEC,
        INITIAL_DELAY_SEC,
        TICK_DRIFT_TOLERANCE,
        HOURLY_DRIFT_TOLERANCE,
        SAMPLE_MISMATCH_TOLERANCE,
        SENTINEL_STALENESS_LIMIT_SEC,
    )
    from services.etl_chat_ai import INITIAL_DELAY_SEC as ETL_DELAY

    assert INTEGRITY_INTERVAL_SEC <= 5 * 60
    assert INITIAL_DELAY_SEC >= ETL_DELAY
    # tolerance bands ordered: any drift > tick is concerning, > hourly is failure
    assert 0 <= TICK_DRIFT_TOLERANCE < HOURLY_DRIFT_TOLERANCE
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
