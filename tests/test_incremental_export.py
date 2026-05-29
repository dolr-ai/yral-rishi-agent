"""Static config + parser tests for scripts/incremental_export.py.

The live behavior (subprocess + boto3 + filesystem) is exercised on
rishi-1; here we just pin parsing + table list + threshold sanity."""

import importlib.util
import os
import sys
from pathlib import Path


def _load_module():
    """The export script lives in scripts/ and isn't importable as a
    package. Load it via importlib for testing."""
    here = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "incremental_export", here / "scripts" / "incremental_export.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_synced_tables_match_v2_etl():
    """Drift here means V2 thinks a table is being synced when the
    exporter isn't actually exporting it (or vice-versa) — silent data
    gaps."""
    mod = _load_module()
    # The new V2 ETL (Phase 2) will derive its list from the same source
    # eventually; for Phase 1 just pin the explicit set.
    assert mod.SYNCED_TABLES == ("ai_influencers", "conversations", "messages")


def test_thresholds_ordered():
    """RUN_WARN_SEC < RUN_FAIL_SEC < cron interval (300s). Otherwise a
    slow run overlaps the next tick or fail/warn lines invert."""
    mod = _load_module()
    assert 0 < mod.RUN_WARN_SEC < mod.RUN_FAIL_SEC < 300


def test_watermark_keeps_pace():
    """Watermark must be << 5-min cron interval; too long means too much
    lag, too short means racing in-flight inserts."""
    mod = _load_module()
    assert 10 <= mod.WATERMARK_SECONDS <= 120


def test_s3_layout_constants():
    """The bucket/prefix is part of the V2 contract too — V2's fetcher
    looks at exactly this prefix. Test catches accidental renames."""
    mod = _load_module()
    assert mod.S3_BUCKET == "rishi-yral"
    assert mod.S3_PREFIX == "yral-chat-ai/incremental-sync"
    assert mod.S3_ENDPOINT.startswith("https://")


def test_load_credentials_parses_shell_style(tmp_path, monkeypatch):
    """Operator drops a KEY=VALUE file at ~/.etl-export/credentials.
    Must handle quoted values, comments, blank lines."""
    mod = _load_module()
    cred_file = tmp_path / "credentials"
    cred_file.write_text(
        "# comment line\n"
        "ETL_PG_PASSWORD=plain-pwd\n"
        "BACKUP_S3_ACCESS_KEY='quoted-key'\n"
        '\n'
        'BACKUP_S3_SECRET_KEY="double-quoted"\n'
    )
    monkeypatch.setattr(mod, "CRED_FILE", cred_file)
    creds = mod.load_credentials()
    assert creds["ETL_PG_PASSWORD"] == "plain-pwd"
    assert creds["BACKUP_S3_ACCESS_KEY"] == "quoted-key"
    assert creds["BACKUP_S3_SECRET_KEY"] == "double-quoted"


def test_load_credentials_errors_on_missing_keys(tmp_path, monkeypatch):
    """Half-configured file = hard fail. Don't silently start uploading
    nothing."""
    mod = _load_module()
    cred_file = tmp_path / "credentials"
    cred_file.write_text("ETL_PG_PASSWORD=only-this-one\n")
    monkeypatch.setattr(mod, "CRED_FILE", cred_file)
    import pytest
    with pytest.raises(SystemExit) as e:
        mod.load_credentials()
    assert "missing keys" in str(e.value)


def test_state_round_trip(tmp_path, monkeypatch):
    """save then load returns the same shape. atomic write via .tmp +
    replace, so a crash mid-save can't leave a half-written file. The
    integrity fields (_last_*) auto-fill to None on load if absent."""
    mod = _load_module()
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(mod, "STATE_FILE", state_file)
    payload = {
        "ai_influencers": "2026-05-26T08:00:00+00:00",
        "conversations": "2026-05-26T08:00:00+00:00",
        "messages": "2026-05-26T08:05:00+00:00",
        "_last_hourly_emit": None,
        "_last_sample_emit": None,
        "_last_sentinel_emit": None,
    }
    mod.save_state(payload)
    assert mod.load_state() == payload


def test_state_load_empty_returns_epoch_defaults(tmp_path, monkeypatch):
    """No state file yet = first ever run. Default to epoch for cursors,
    None for integrity timestamps so all three emit on the very first
    tick after the script runs."""
    mod = _load_module()
    monkeypatch.setattr(mod, "STATE_FILE", tmp_path / "no-such-file.json")
    state = mod.load_state()
    # Table-name cursors all at epoch
    for table in mod.SYNCED_TABLES:
        assert state[table] == mod.EPOCH_ISO
    # Integrity timestamps unset → emit on first tick
    assert state["_last_hourly_emit"] is None
    assert state["_last_sample_emit"] is None
    assert state["_last_sentinel_emit"] is None


def test_integrity_emission_intervals_ordered():
    """The four emission cadences must be strictly ordered: sentinel
    (30min) < hourly (60min) < sample (6h). Otherwise a 'is overdue'
    gate fires the wrong layer."""
    mod = _load_module()
    assert mod.SENTINEL_INTERVAL_SEC < mod.HOURLY_INTERVAL_SEC < mod.SAMPLE_INTERVAL_SEC


def test_integrity_watermarks_wider_for_sample(tmp_path):
    """Sample integrity (every 6h) tolerates more drift than hourly,
    so its watermark backs off further to make sure all rows have
    finished their burst windows."""
    mod = _load_module()
    assert mod.SAMPLE_WATERMARK_SEC >= mod.HOURLY_WATERMARK_SEC


def test_integrity_s3_prefix_correct():
    """The exporter emits into _integrity/ under the same prefix as
    the CSV files — matches the V2 verifier's S3_INTEGRITY_PREFIX."""
    mod = _load_module()
    assert mod.S3_INTEGRITY_PREFIX == "yral-chat-ai/incremental-sync/_integrity"


def test_is_overdue_handles_none():
    """First-ever run = None state = emit immediately. Without this
    branch, the first sentinel/hourly/sample never fire."""
    mod = _load_module()
    assert mod._is_overdue(None, 60) is True


def test_is_overdue_recent_returns_false():
    """A recent emission keeps the gate closed."""
    from datetime import datetime, timezone

    mod = _load_module()
    now = datetime.now(timezone.utc).isoformat()
    assert mod._is_overdue(now, 3600) is False


def test_state_carries_integrity_timestamps(tmp_path, monkeypatch):
    """State file round-trips the integrity timestamps. Otherwise the
    gates fire every tick."""
    mod = _load_module()
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(mod, "STATE_FILE", state_file)
    payload = {
        "ai_influencers": "2026-05-26T08:00:00+00:00",
        "conversations": "2026-05-26T08:00:00+00:00",
        "messages": "2026-05-26T08:00:00+00:00",
        "_last_hourly_emit": "2026-05-29T13:00:00+00:00",
        "_last_sample_emit": "2026-05-29T08:00:00+00:00",
        "_last_sentinel_emit": "2026-05-29T13:30:00+00:00",
    }
    mod.save_state(payload)
    got = mod.load_state()
    assert got == payload


def test_state_load_upgrades_old_file(tmp_path, monkeypatch):
    """A pre-Phase-3 state file (without the _last_* keys) must
    auto-upgrade to None defaults rather than KeyError-ing."""
    mod = _load_module()
    state_file = tmp_path / "state.json"
    import json
    state_file.write_text(json.dumps({
        "ai_influencers": "2026-05-26T08:00:00+00:00",
        "conversations": "2026-05-26T08:00:00+00:00",
        "messages": "2026-05-26T08:00:00+00:00",
    }))
    monkeypatch.setattr(mod, "STATE_FILE", state_file)
    got = mod.load_state()
    assert got["_last_hourly_emit"] is None
    assert got["_last_sample_emit"] is None
    assert got["_last_sentinel_emit"] is None


def test_failure_counter_round_trip(tmp_path, monkeypatch):
    """3 consecutive failures = STUCK marker. Counter must persist
    across cron invocations, and reset on success."""
    mod = _load_module()
    monkeypatch.setattr(mod, "FAILURE_FILE", tmp_path / "fails")
    assert mod.bump_failure_counter() == 1
    assert mod.bump_failure_counter() == 2
    assert mod.bump_failure_counter() == 3
    mod.reset_failure_counter()
    assert not (tmp_path / "fails").exists()
    # After reset, next bump starts from 1 (not from previous max)
    assert mod.bump_failure_counter() == 1
