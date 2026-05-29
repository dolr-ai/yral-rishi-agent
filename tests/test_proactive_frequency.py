"""Task D — proactive frequency: pin the allowed values + default.

Behavioral tests against the DB are exercised by the live smoke test
after deploy."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_allowed_frequencies():
    """Migration 012 has the same set in a CHECK constraint. If these
    drift, INSERTs will fail at the DB layer with a constraint error;
    the test catches it earlier."""
    from routes.chat import PROACTIVE_FREQUENCIES

    assert PROACTIVE_FREQUENCIES == {"default", "daily", "weekly", "off"}


def test_default_value_in_migration():
    """The migration must default existing rows to 'default' so behavior
    is unchanged on rollout. Light check — read the SQL file directly."""
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parent.parent
        / "migrations"
        / "012_proactive_frequency.sql"
    ).read_text()
    assert "DEFAULT 'default'" in sql
    assert "CHECK (proactive_frequency IN ('default', 'daily', 'weekly', 'off'))" in sql
