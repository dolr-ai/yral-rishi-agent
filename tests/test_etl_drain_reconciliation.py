"""Unit tests for the verdict computation in services.etl_drain.

The verdict logic in `_compute_verdict` is the load-bearing piece of
the on-demand drain. All four verdict classes (GREEN, DRAIN_AGAIN,
INVESTIGATE with multiple sub-reasons) get a test here so a refactor
can't quietly change Rishi's signal.

Pure-function tests — no DB, no S3, no asyncio.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _fresh_integrity(passes_per_layer: int = 5) -> dict:
    """Helper — all layers fresh, no fails."""
    return {
        "tick_layer_passes": passes_per_layer,
        "tick_layer_fails": 0,
        "hourly_layer_passes": passes_per_layer,
        "hourly_layer_fails": 0,
        "sample_layer_passes": passes_per_layer,
        "sample_layer_fails": 0,
        "sentinel_layer_passes": passes_per_layer,
        "sentinel_layer_fails": 0,
    }


def _balanced_tables(skips: dict | None = None) -> list[dict]:
    """Helper — all ETL tables in parity (delta == sum of skips)."""
    skips = skips or {}
    out = []
    for tbl in ("ai_influencers", "conversations", "messages"):
        tbl_skips = skips.get(tbl, {})
        skip_total = sum(tbl_skips.values())
        out.append(
            {
                "name": tbl,
                "kind": "etl_covered",
                "chat_ai_count": 1000 + skip_total,
                "v2_count": 1000,
                "delta": skip_total,
                "skipped_breakdown": tbl_skips,
                "skips_explain_delta": True,
            }
        )
    return out


# ─── GREEN path ───────────────────────────────────────────────────────────


def test_green_when_balanced_and_all_layers_pass_and_fresh_export():
    from services.etl_drain import _compute_verdict

    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    export = now - timedelta(minutes=2)
    warnings: list[str] = []
    blocking: list[str] = []
    verdict, explanation = _compute_verdict(
        tables=_balanced_tables(),
        integrity=_fresh_integrity(),
        chat_ai_export_ts=export,
        new_skip_reasons=[],
        now=now,
        warnings=warnings,
        blocking_issues=blocking,
    )
    assert verdict == "GREEN"
    assert "parity" in explanation.lower()
    assert blocking == []


def test_green_when_balanced_with_318_8932_skips():
    """The exact 2026-06-04 re-bootstrap numbers from the plan §5 —
    318 duplicate convs + 8,932 orphan messages — must verdict GREEN."""
    from services.etl_drain import _compute_verdict

    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    export = now - timedelta(minutes=2)
    skips = {
        "conversations": {"conflict": 318},
        "messages": {"orphan": 8932},
    }
    verdict, explanation = _compute_verdict(
        tables=_balanced_tables(skips=skips),
        integrity=_fresh_integrity(),
        chat_ai_export_ts=export,
        new_skip_reasons=[],
        now=now,
        warnings=[],
        blocking_issues=[],
    )
    assert verdict == "GREEN"
    assert "318" in explanation
    assert "8932" in explanation


# ─── DRAIN_AGAIN path ────────────────────────────────────────────────────


def test_drain_again_when_delta_not_explained_by_skips():
    """Importer is behind chat-ai — re-trigger drain to catch up."""
    from services.etl_drain import _compute_verdict

    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    export = now - timedelta(minutes=2)
    tables = [
        {
            "name": "messages",
            "kind": "etl_covered",
            "chat_ai_count": 1010,
            "v2_count": 1000,
            "delta": 10,
            "skipped_breakdown": {},  # no skips explain the 10-row delta
            "skips_explain_delta": False,
        },
        # Other two tables in parity, just to isolate
        {
            "name": "ai_influencers",
            "kind": "etl_covered",
            "chat_ai_count": 50,
            "v2_count": 50,
            "delta": 0,
            "skipped_breakdown": {},
            "skips_explain_delta": True,
        },
        {
            "name": "conversations",
            "kind": "etl_covered",
            "chat_ai_count": 200,
            "v2_count": 200,
            "delta": 0,
            "skipped_breakdown": {},
            "skips_explain_delta": True,
        },
    ]
    verdict, explanation = _compute_verdict(
        tables=tables,
        integrity=_fresh_integrity(),
        chat_ai_export_ts=export,
        new_skip_reasons=[],
        now=now,
        warnings=[],
        blocking_issues=[],
    )
    assert verdict == "DRAIN_AGAIN"
    assert "messages" in explanation
    assert "in-flight" in explanation.lower()


# ─── INVESTIGATE — sub-cases ─────────────────────────────────────────────


def test_investigate_when_integrity_failure_in_24h():
    from services.etl_drain import _compute_verdict

    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    integrity = _fresh_integrity()
    integrity["sample_layer_fails"] = 1
    blocking: list[str] = []
    verdict, explanation = _compute_verdict(
        tables=_balanced_tables(),
        integrity=integrity,
        chat_ai_export_ts=now - timedelta(minutes=2),
        new_skip_reasons=[],
        now=now,
        warnings=[],
        blocking_issues=blocking,
    )
    assert verdict == "INVESTIGATE"
    assert "integrity" in explanation.lower()
    assert any("integrity failure" in b for b in blocking)


def test_investigate_when_exporter_heartbeat_stale():
    from services.etl_drain import _compute_verdict

    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    stale_export = now - timedelta(minutes=45)  # > 30 min threshold
    blocking: list[str] = []
    verdict, explanation = _compute_verdict(
        tables=_balanced_tables(),
        integrity=_fresh_integrity(),
        chat_ai_export_ts=stale_export,
        new_skip_reasons=[],
        now=now,
        warnings=[],
        blocking_issues=blocking,
    )
    assert verdict == "INVESTIGATE"
    assert "stale" in explanation.lower()
    assert any("stale" in b for b in blocking)


def test_investigate_when_new_skip_reason_appears():
    """If `etl_skipped_rows` has a reason not in our DELIBERATE allow-list,
    a brand-new failure mode has appeared — surface it for review."""
    from services.etl_drain import _compute_verdict

    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    blocking: list[str] = []
    verdict, explanation = _compute_verdict(
        tables=_balanced_tables(),
        integrity=_fresh_integrity(),
        chat_ai_export_ts=now - timedelta(minutes=2),
        new_skip_reasons=["weird_new_class"],
        now=now,
        warnings=[],
        blocking_issues=blocking,
    )
    assert verdict == "INVESTIGATE"
    assert "weird_new_class" in explanation
    assert any("weird_new_class" in b for b in blocking)


def test_investigate_when_layer_has_zero_passes_in_24h():
    """Cold layer — no recent PASS evidence. Can't grant GREEN without
    each required layer attesting."""
    from services.etl_drain import _compute_verdict

    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    integrity = _fresh_integrity()
    integrity["sample_layer_passes"] = 0  # cold sample layer
    blocking: list[str] = []
    verdict, explanation = _compute_verdict(
        tables=_balanced_tables(),
        integrity=integrity,
        chat_ai_export_ts=now - timedelta(minutes=2),
        new_skip_reasons=[],
        now=now,
        warnings=[],
        blocking_issues=blocking,
    )
    assert verdict == "INVESTIGATE"
    assert "sample" in explanation
    assert any("sample" in b for b in blocking)


def test_investigate_when_chat_ai_count_missing_for_any_etl_table():
    """No hourly integrity payload → we can't verify parity → cannot
    grant GREEN regardless of whether row counts happen to match."""
    from services.etl_drain import _compute_verdict

    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    tables = [
        {
            "name": "messages",
            "kind": "etl_covered",
            "chat_ai_count": None,  # missing payload
            "v2_count": 1000,
            "delta": None,
            "skipped_breakdown": {},
            "skips_explain_delta": False,
            "note": "no hourly integrity payload available",
        }
    ]
    blocking: list[str] = []
    verdict, explanation = _compute_verdict(
        tables=tables,
        integrity=_fresh_integrity(),
        chat_ai_export_ts=now - timedelta(minutes=2),
        new_skip_reasons=[],
        now=now,
        warnings=[],
        blocking_issues=blocking,
    )
    assert verdict == "INVESTIGATE"
    # The table name appears in blocking_issues; explanation is generic.
    assert any("messages" in b for b in blocking)


# ─── precedence ──────────────────────────────────────────────────────────


def test_precedence_missing_evidence_wins_over_drain_again():
    """A missing chat-ai count should INVESTIGATE before computing
    DRAIN_AGAIN on the other tables — can't reason about parity for
    the missing one."""
    from services.etl_drain import _compute_verdict

    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    tables = [
        # One missing — should preempt
        {
            "name": "messages",
            "kind": "etl_covered",
            "chat_ai_count": None,
            "v2_count": 1000,
            "delta": None,
            "skipped_breakdown": {},
            "skips_explain_delta": False,
        },
        # Another with unexplained delta — would normally trigger DRAIN_AGAIN
        {
            "name": "conversations",
            "kind": "etl_covered",
            "chat_ai_count": 210,
            "v2_count": 200,
            "delta": 10,
            "skipped_breakdown": {},
            "skips_explain_delta": False,
        },
    ]
    verdict, _ = _compute_verdict(
        tables=tables,
        integrity=_fresh_integrity(),
        chat_ai_export_ts=now - timedelta(minutes=2),
        new_skip_reasons=[],
        now=now,
        warnings=[],
        blocking_issues=[],
    )
    assert verdict == "INVESTIGATE"


# ─── module-level constants — pin so tests catch regression ──────────────


def test_deliberate_skip_reasons_match_plan():
    """Plan §5 enumerates `conflict` and `orphan` as the deliberate
    skip classes. If these change without updating the plan, we want
    a test failure."""
    from services.etl_drain import DELIBERATE_SKIP_REASONS

    assert DELIBERATE_SKIP_REASONS == frozenset({"conflict", "orphan"})


def test_required_layers_are_three_post_tick():
    """tick layer is informational; GREEN requires hourly + sample +
    sentinel per plan §3."""
    from services.etl_drain import REQUIRED_LAYERS

    assert set(REQUIRED_LAYERS) == {"hourly", "sample", "sentinel"}


def test_v2_native_tables_includes_system_instructions_history():
    """Per Rishi pre-decision §9.1: yes for system_instructions_history;
    simpler path = report v2_native, don't extend the exporter today."""
    from services.etl_drain import V2_NATIVE_TABLES

    assert "system_instructions_history" in V2_NATIVE_TABLES
