"""Integrity verifier — pin thresholds + check-type set.

Live behavior exercised after deploy via /admin/etl-integrity + the
hourly background loop."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_thresholds_in_order():
    """MAX_DRIFT_ROWS (warn line) must be < FAIL_DRIFT_ROWS (fail line);
    otherwise a row that warns also fails. WARN_SAMPLE same idea."""
    from services.etl_integrity import (
        MAX_DRIFT_ROWS,
        FAIL_DRIFT_ROWS,
        WARN_SAMPLE_MISMATCHES,
        FAIL_SAMPLE_MISMATCHES,
    )

    assert 0 < MAX_DRIFT_ROWS < FAIL_DRIFT_ROWS
    assert 0 < WARN_SAMPLE_MISMATCHES < FAIL_SAMPLE_MISMATCHES


def test_interval_hourly():
    """Hourly per the spec. Below 5 min we'd hammer chat-ai with
    COUNT(*) scans; above 6h we'd miss drift for too long."""
    from services.etl_integrity import INTEGRITY_INTERVAL_SEC

    assert 5 * 60 <= INTEGRITY_INTERVAL_SEC <= 6 * 60 * 60


def test_initial_delay_after_etl_warm():
    """ETL has a 1-min initial delay + needs a tick or two to populate.
    Integrity should wait so first pass sees actual data."""
    from services.etl_integrity import INITIAL_DELAY_SEC
    from services.etl_chat_ai import INITIAL_DELAY_SEC as ETL_DELAY

    assert INITIAL_DELAY_SEC >= ETL_DELAY


def test_checked_tables_match_etl_synced_tables():
    """If ETL syncs a table but integrity doesn't check it, drift goes
    unobserved. The two lists should track each other."""
    from services.etl_integrity import CHECKED_TABLES
    from services.etl_chat_ai import SYNCED_TABLES

    synced = {t["name"] for t in SYNCED_TABLES}
    checked = set(CHECKED_TABLES)
    assert checked == synced


def test_sample_size_reasonable():
    """20 conversations per pass means up to ~200 message-content compares
    per hour. Below 5 is too noisy to detect drift; above 100 burns
    chat-ai read budget."""
    from services.etl_integrity import SAMPLE_CONVERSATIONS

    assert 5 <= SAMPLE_CONVERSATIONS <= 100
