"""Regression test for the etl_integrity Sentry noise dedupe.

Sentry triage 2026-06-18 found `etl_integrity tick FAILED` events
(YRAL-RISHI-AGENT-1T + 1S) accounting for **24,817 events / week** —
the loudest noise in the project, drowning out real signal.

Root cause: every drift fired BOTH a `logger.error(...)` (auto-
captured by Sentry's logging integration) AND an explicit
`sentry_sdk.capture_message(...)`. No NX dedupe across replicas
either — both rishi-4 + rishi-5 fired for the same drift.

Fix: NX-dedupe on `(layer, drift_bucket)` with 1-hour TTL (mirrors
H11 cost_alerts pattern). Plus downgrade the log line to WARNING
so the logging-integration auto-capture stops firing.

Expected post-deploy delta: 24,817 events / week → tens of events,
one per (layer, bucket) per hour.
"""

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


# ─── source-pin ─────────────────────────────────────────────────────────


def test_capture_sentry_is_async_with_nx_dedup():
    """`_capture_sentry` must be async and call `_try_set_nx` before
    firing. Source-pin both properties — a future refactor that
    drops the dedup or makes it sync would silently regress to the
    pre-fix noise level."""
    src = (REPO / "app" / "services" / "etl_integrity.py").read_text()
    assert "async def _capture_sentry" in src
    assert "await _try_set_nx" in src


def test_call_site_awaits_capture():
    """The capture call site must `await` the (now-async) helper.
    Without await, the coroutine is created but never scheduled —
    silent regression."""
    src = (REPO / "app" / "services" / "etl_integrity.py").read_text()
    assert "await _capture_sentry(" in src


def test_log_line_is_warning_not_error():
    """`logger.error` triggers Sentry's logging integration auto-
    capture, which fires IN ADDITION TO the explicit capture. The
    fix downgrades to `logger.warning` so only the deduped capture
    path produces a Sentry event. Pin the downgrade so a future
    PR can't accidentally restore the error level."""
    src = (REPO / "app" / "services" / "etl_integrity.py").read_text()
    fn_start = src.index("if not passed:")
    fn_end = src.index("else:", fn_start)
    block = src[fn_start:fn_end]
    assert "logger.warning(" in block
    assert "logger.error(" not in block, (
        "etl_integrity FAILED log must stay at WARNING — Sentry's "
        "logging integration auto-captures ERROR + above which would "
        "regress to the pre-fix double-capture noise"
    )


def test_try_set_nx_pattern_matches_cost_alerts():
    """`_try_set_nx` mirrors the proven pattern from cost_alerts
    (Phase 21αβ.H11). Same Redis SET NX EX shape, same fail-closed-
    on-alert semantics."""
    src = (REPO / "app" / "services" / "etl_integrity.py").read_text()
    assert 'redis.set(key, "1", nx=True, ex=ttl_sec)' in src
    # Fail-closed: Redis down ⇒ return False (don't fire 5 alerts
    # every 5 min during a Redis outage).
    assert "(treating as not-acquired)" in src


# ─── behavioural — bucket math ──────────────────────────────────────────


def test_drift_bucket_zero():
    from services.etl_integrity import _drift_bucket

    assert _drift_bucket(0) == "0"


def test_drift_bucket_small_drifts():
    """Buckets are non-overlapping: a count of exactly 1 lives in its
    own '1-1' bucket; 2-10 maps to '2-10'. Tightens the bucket-
    overlap behaviour so a 1-drift escalation to 2 fires Sentry."""
    from services.etl_integrity import _drift_bucket

    assert _drift_bucket(1) == "1-1"
    assert _drift_bucket(2) == "2-10"
    assert _drift_bucket(5) == "2-10"
    assert _drift_bucket(10) == "2-10"


def test_drift_bucket_typical_steady_state():
    """Hourly tick produces ~30-50 drifts at steady state. All map
    to the same '11-50' bucket so a steady drift collapses to one
    Sentry event per (layer, bucket) per hour."""
    from services.etl_integrity import _drift_bucket

    assert _drift_bucket(27) == "11-50"  # the count from the 2026-06-18 sample
    assert _drift_bucket(11) == "11-50"
    assert _drift_bucket(50) == "11-50"


def test_drift_bucket_escalation_crosses_boundary():
    """A real jump from 30 → 100 crosses bucket boundary
    (11-50 → 51-100) and refires Sentry — ops still notices the
    escalation."""
    from services.etl_integrity import _drift_bucket

    assert _drift_bucket(30) == "11-50"
    assert _drift_bucket(75) == "51-100"
    assert _drift_bucket(100) == "51-100"
    assert _drift_bucket(101) == "101-500"
    assert _drift_bucket(500) == "101-500"
    assert _drift_bucket(501) == "501-5000"


def test_drift_bucket_above_cap_returns_overflow():
    from services.etl_integrity import _drift_bucket

    # Above the largest bucket upper bound → "<cap>+" label.
    assert _drift_bucket(10_000) == "5000+"
    assert _drift_bucket(999_999) == "5000+"


def test_dedup_key_includes_layer_and_bucket():
    """The dedup key MUST include both layer + bucket so different
    layers (hourly/sample/sentinel) deduplicate independently AND
    different drift severities fire separate events."""
    src = (REPO / "app" / "services" / "etl_integrity.py").read_text()
    # Look for the key format inside _capture_sentry
    assert 'f"sentry:etl_integrity:{layer}:bucket={bucket}"' in src


def test_dedup_ttl_is_one_hour():
    """1-hour TTL = at most 1 event per (layer, bucket) per hour.
    A future "let's tighten to 5 min" refactor would walk back the
    24k→tens improvement; pin the TTL so it's a conscious change."""
    src = (REPO / "app" / "services" / "etl_integrity.py").read_text()
    assert "_SENTRY_DEDUP_TTL_SEC = 60 * 60" in src
