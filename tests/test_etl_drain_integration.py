"""Integration test for the drain orchestration loop.

Uses a fake pool + fake etl services so the test runs in-process
without DB or S3. The goal: assert the drain logic correctly:
  - calls importer run_once until 2 consecutive empties
  - calls integrity run_once until all 3 required layers are fresh
  - hits its deadline gracefully when run_once never settles
  - injects drain telemetry into the final report
  - flips GREEN → INVESTIGATE when the drain hit its deadline

Source-pin tests live in the workflow + reconciliation test files.
This file is the behavioral coverage.
"""

import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


# Hardcoded `datetime(2026, 6, 11, 11, 59, ...)` fixtures made this
# file wall-clock-fragile: once the real clock drifted past the
# HEARTBEAT_STALE_SEC threshold (30 min), reconciliation flagged the
# fixture's heartbeat as stale and the verdict math changed under the
# test's feet (the `drain_deadline_hit` warning is suppressed when the
# heartbeat is already flagged). "Now minus 1 min" keeps fixtures
# fresh forever without pulling in freezegun.
def _recent_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=1)


# ─── fakes ────────────────────────────────────────────────────────────────


class FakeRow(dict):
    """asyncpg.Row is dict-ish — fetchrow returns this shape."""


class FakePool:
    """Just enough surface for drain() + reconciliation()."""

    def __init__(self, *, hourly_payload=None, layer_results=None):
        # Backing store for etl_integrity_results-shaped rows.
        # layer_results: list[(layer, verified_at, passed, details)]
        self._integrity_rows = list(layer_results or [])
        # The "latest hourly integrity payload" — what
        # _chat_ai_counts_from_hourly_payload reads.
        self._hourly_payload = hourly_payload
        # Skip rows: list[(table, reason, count)]
        self._skip_rows: list[tuple[str, str, int]] = []
        # V2 row counts dict
        self._v2_counts = {"ai_influencers": 50, "conversations": 200, "messages": 1000}
        # latest processed_at on etl_processed_files
        self._v2_latest_import = _recent_utc()

    async def fetchrow(self, query, *args):
        q = query.strip()
        if "FROM etl_integrity_results" in q and "ORDER BY verified_at DESC LIMIT 1" in q:
            # Latest hourly payload
            if not self._integrity_rows:
                return None
            # Filter to hourly + return most recent
            hourly = [r for r in self._integrity_rows if r[0] == "hourly"]
            if not hourly:
                return None
            row = max(hourly, key=lambda r: r[1])
            return FakeRow(
                {
                    "details": row[3],
                    "snapshot_iso": row[1].isoformat(),
                    "verified_at": row[1],
                }
            )
        if "MAX(CASE WHEN layer=" in q:
            # The "are all 3 layers fresh past $1" query
            since = args[0]
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            by_layer = {}
            for layer, vt, *_ in self._integrity_rows:
                if vt > since_dt:
                    if layer not in by_layer or vt > by_layer[layer]:
                        by_layer[layer] = vt
            return FakeRow(
                {
                    "hourly_at": by_layer.get("hourly"),
                    "sample_at": by_layer.get("sample"),
                    "sentinel_at": by_layer.get("sentinel"),
                }
            )
        raise NotImplementedError(f"FakePool.fetchrow: {q[:80]}")

    async def fetch(self, query, *args):
        q = query.strip()
        if "FROM ai_influencers" in q and "UNION ALL" in q:
            return [
                FakeRow({"name": k, "n": v}) for k, v in self._v2_counts.items()
            ]
        if "FROM etl_skipped_rows" in q and "GROUP BY table_name, reason" in q:
            return [
                FakeRow({"table_name": t, "reason": r, "n": n})
                for t, r, n in self._skip_rows
            ]
        if "FROM etl_skipped_rows" in q and "DISTINCT reason" in q:
            # No new skip reasons in the fixture by default
            return []
        if (
            "FROM etl_integrity_results" in q
            and "GROUP BY layer" in q
            and "FILTER (WHERE passed)" in q
        ):
            # Per-layer 24h pass/fail. Read off the in-memory list.
            from collections import defaultdict

            agg: dict[str, dict[str, int]] = defaultdict(
                lambda: {"passes": 0, "fails": 0}
            )
            for layer, _vt, passed, _details in self._integrity_rows:
                agg[layer]["passes" if passed else "fails"] += 1
            return [
                FakeRow({"layer": layer, "passes": v["passes"], "fails": v["fails"]})
                for layer, v in agg.items()
            ]
        raise NotImplementedError(f"FakePool.fetch: {q[:80]}")

    async def fetchval(self, query, *args):
        q = query.strip()
        if "SELECT EXISTS" in q and "system_instructions_history" in str(args):
            return False  # pretend table doesn't exist in this fixture
        if "SELECT EXISTS" in q:
            return False
        if "MAX(processed_at)" in q:
            return self._v2_latest_import
        raise NotImplementedError(f"FakePool.fetchval: {q[:80]}")

    # Used by _new_skip_reasons + _integrity_summary_24h
    async def _fetch_distinct_reasons(self):
        return []

    async def _fetch_integrity_summary(self):
        return []


@pytest.fixture
def fake_etl_services(monkeypatch):
    """Patch the run_once functions on the real services.etl_chat_ai +
    services.etl_integrity modules. We can't just swap sys.modules
    because `from services import etl_chat_ai` inside drain() binds to
    the `services` package's `etl_chat_ai` attribute at call time, not
    via sys.modules lookup.

    Patching the function attribute is also more honest: the modules
    fully import (their config code runs); we only replace the entry
    point drain() invokes."""
    import services.etl_chat_ai as real_importer
    import services.etl_integrity as real_integrity

    calls = {"importer": 0, "integrity": 0}
    importer_file_counts: list[int] = [1, 1, 0, 0]  # 2 consecutive 0s → steady state

    async def importer_run_once(pool):
        calls["importer"] += 1
        idx = calls["importer"] - 1
        if idx < len(importer_file_counts):
            return {"files_processed": importer_file_counts[idx]}
        return {"files_processed": 0}

    async def integrity_run_once(pool):
        calls["integrity"] += 1
        # On each call, append a fresh row for one of the layers in
        # round-robin order so that after 3 calls all 3 are fresh.
        layers = ["hourly", "sample", "sentinel"]
        layer = layers[(calls["integrity"] - 1) % 3]
        pool._integrity_rows.append(  # type: ignore[attr-defined]
            (layer, datetime.now(timezone.utc), True, {})
        )

    monkeypatch.setattr(real_importer, "run_once", importer_run_once)
    monkeypatch.setattr(real_integrity, "run_once", integrity_run_once)

    return {
        "calls": calls,
        "importer_file_counts": importer_file_counts,
    }


# ─── drain — behavior ────────────────────────────────────────────────────


def test_drain_calls_importer_until_steady_state(fake_etl_services, monkeypatch):
    """Two consecutive empty importer ticks = steady state. Then the
    drain moves to phase 2."""
    from services.etl_drain import drain

    # Make sleep a no-op so the test runs instantly.
    async def no_sleep(_):
        return None

    monkeypatch.setattr("services.etl_drain.asyncio.sleep", no_sleep)

    pool = FakePool()
    # Pre-seed an hourly payload so reconciliation can compute deltas
    # at the end. Match V2 counts so the verdict can be GREEN if other
    # conditions hold (we're not asserting verdict here — just the loop).
    pool._integrity_rows.append(
        (
            "hourly",
            _recent_utc(),
            True,
            {"chat_ai_counts": {"ai_influencers": 50, "conversations": 200, "messages": 1000}},
        )
    )

    result = asyncio.run(drain(pool, deadline_sec=10))
    # Importer ran exactly 4 times in our fixture: [1, 1, 0, 0]
    # then steady-state hit (2 consecutive empties).
    assert fake_etl_services["calls"]["importer"] == 4
    # Integrity ran enough times to land all 3 layers.
    assert fake_etl_services["calls"]["integrity"] >= 3
    assert result["drain"]["importer_total_files_processed"] == 2
    assert result["drain"]["importer_hit_deadline"] is False


def test_drain_hits_deadline_when_run_once_never_settles(monkeypatch):
    """Importer never returns 0 → drain must give up at deadline and
    flag importer_hit_deadline. Verdict should then be INVESTIGATE
    even if balance-math would otherwise pass."""
    import services.etl_chat_ai as real_importer
    import services.etl_integrity as real_integrity
    from services.etl_drain import drain

    async def busy_importer(pool):
        return {"files_processed": 1}  # always has work

    async def busy_integrity(pool):
        return None

    monkeypatch.setattr(real_importer, "run_once", busy_importer)
    monkeypatch.setattr(real_integrity, "run_once", busy_integrity)

    async def no_sleep(_):
        return None

    monkeypatch.setattr("services.etl_drain.asyncio.sleep", no_sleep)

    pool = FakePool()
    pool._integrity_rows.append(
        (
            "hourly",
            _recent_utc(),
            True,
            {"chat_ai_counts": {"ai_influencers": 50, "conversations": 200, "messages": 1000}},
        )
    )
    pool._integrity_rows.append(
        ("sample", _recent_utc(), True, {})
    )
    pool._integrity_rows.append(
        ("sentinel", _recent_utc(), True, {})
    )

    # Very small deadline to force the timeout path.
    result = asyncio.run(drain(pool, deadline_sec=0.01))
    assert result["drain"]["importer_hit_deadline"] is True
    # Even though balance math would pass (the FakePool returns matching counts),
    # the deadline-hit case forces INVESTIGATE.
    assert result["verdict"] == "INVESTIGATE"
    assert "drain_deadline_hit" in result.get("warnings", [])


def test_drain_report_includes_required_top_level_fields():
    """Pin the report shape so the workflow's JSON-parse step keeps
    working. The plan §3 lists the required fields."""
    from services.etl_drain import reconciliation

    pool = FakePool()
    pool._integrity_rows.append(
        (
            "hourly",
            _recent_utc(),
            True,
            {"chat_ai_counts": {"ai_influencers": 50, "conversations": 200, "messages": 1000}},
        )
    )

    result = asyncio.run(reconciliation(pool))
    for key in (
        "as_of",
        "chat_ai_latest_export_ts",
        "v2_latest_import_ts",
        "lag_seconds",
        "verdict",
        "verdict_explanation",
        "tables",
        "integrity_last_24h",
        "warnings",
        "blocking_issues",
    ):
        assert key in result, f"reconciliation report missing required field {key!r}"
