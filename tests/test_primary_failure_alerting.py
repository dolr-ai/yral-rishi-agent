"""Brief task 4 (2026-06-26) — runpod_vllm primary failure alerting.

Today the 6 background processes with runpod_vllm primary + internal_vllm
fallback (proactive_generation, quality_scorer, memory_extraction,
memory_consolidation, nudge_generation, video_idea_generation) fail
SOFT — a primary brown-out silently routes to internal_vllm and the
user sees nothing. Task 9 (fallback removal) will surface those
failures to users; first we need alerting so the brown-out is visible
in Sentry + the admin dashboard.

These tests pin:

  - Counter behaviour: _record_primary_failure / primary_failure_counts_last_hour
    increment + window-trim correctly. The dashboard tile reads them.

  - Sentry capture shape: enriched tags (process / primary_provider /
    fallback_provider / error_type) so a Sentry alert rule can filter
    on the right dimensions. A future refactor that drops the tags
    would silently break the alert rule.

  - End-to-end via call(): a stubbed primary that always fails →
    counter ticks, Sentry capture fires with tags, fallback still
    serves the request (because soft-failure is the WHOLE POINT for
    this phase — task 4 alerts, task 9 removes the fallback).
"""

import asyncio
import sys
import time
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]

try:
    import httpx  # noqa: F401

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

requires_httpx = pytest.mark.skipif(
    not _HTTPX_AVAILABLE, reason="httpx not installed (CI only)"
)


# ─── source-pin ─────────────────────────────────────────────────────────


def test_fallback_path_carries_structured_sentry_tags():
    """The Sentry warning at the fallback site MUST carry structured
    tags — that's what a Sentry alert rule filters on. A refactor that
    drops the tags reduces the alert to a noisy free-text-message
    match and operators lose the per-process / per-provider dimension."""
    src = (REPO / "app" / "services" / "llm_registry.py").read_text()
    pos = src.find("LLM fallback activated:")
    assert pos != -1, "fallback Sentry message moved or removed"
    # Scan a generous window around the capture site.
    window = src[max(0, pos - 1500) : pos + 500]
    for tag in (
        'scope.set_tag("process"',
        'scope.set_tag("primary_provider"',
        'scope.set_tag("fallback_provider"',
        'scope.set_tag("error_type"',
        'scope.set_extra("error_summary"',
    ):
        assert tag in window, (
            f"missing structured tag at the fallback Sentry capture site: {tag}"
        )


def test_record_primary_failure_runs_before_fallback_attempt():
    """The counter increment must precede the fallback _do_complete()
    call — otherwise a slow fallback that itself fails could mask the
    primary failure entirely from the dashboard."""
    src = (REPO / "app" / "services" / "llm_registry.py").read_text()
    rec_pos = src.find("_record_primary_failure(process, provider)")
    # Find the SECOND _do_complete call (the fallback attempt).
    first_complete = src.find("return await _do_complete(")
    second_complete = src.find("return await _do_complete(", first_complete + 1)
    assert rec_pos != -1, "counter call removed"
    assert second_complete != -1, "fallback _do_complete moved"
    assert rec_pos < second_complete, (
        "counter must increment BEFORE the fallback attempt"
    )


# ─── behavioural — the counter ──────────────────────────────────────────


@requires_httpx
def test_counter_increments_and_drains_with_time_window():
    """Recording several primary failures bumps the count; entries
    older than _PRIMARY_FAILURE_WINDOW_SEC trim out on read."""
    from services import llm_registry

    # Clean slate.
    with llm_registry._PRIMARY_FAILURES_LOCK:
        llm_registry._PRIMARY_FAILURES.clear()

    llm_registry._record_primary_failure("quality_scorer", "runpod_vllm")
    llm_registry._record_primary_failure("quality_scorer", "runpod_vllm")
    llm_registry._record_primary_failure("memory_extraction", "runpod_vllm")

    counts = llm_registry.primary_failure_counts_last_hour()
    assert counts[("quality_scorer", "runpod_vllm")] == 2
    assert counts[("memory_extraction", "runpod_vllm")] == 1

    # Simulate a stale entry: stuff a timestamp older than the window
    # in directly. Read must trim it.
    key = ("quality_scorer", "runpod_vllm")
    with llm_registry._PRIMARY_FAILURES_LOCK:
        llm_registry._PRIMARY_FAILURES[key].appendleft(
            time.time() - llm_registry._PRIMARY_FAILURE_WINDOW_SEC - 60
        )
    counts2 = llm_registry.primary_failure_counts_last_hour()
    assert counts2[key] == 2, "stale entry past the 1h window must be trimmed"

    # Clean up.
    with llm_registry._PRIMARY_FAILURES_LOCK:
        llm_registry._PRIMARY_FAILURES.clear()


@requires_httpx
def test_counter_bounded_per_key_to_cap_memory():
    """A runaway outage must not grow the deque unbounded."""
    from services import llm_registry

    with llm_registry._PRIMARY_FAILURES_LOCK:
        llm_registry._PRIMARY_FAILURES.clear()

    cap = llm_registry._PRIMARY_FAILURE_MAX_PER_KEY
    for _ in range(cap + 50):
        llm_registry._record_primary_failure("nudge_generation", "runpod_vllm")

    counts = llm_registry.primary_failure_counts_last_hour()
    assert counts[("nudge_generation", "runpod_vllm")] == cap, (
        "deque grew past _PRIMARY_FAILURE_MAX_PER_KEY — memory risk"
    )

    with llm_registry._PRIMARY_FAILURES_LOCK:
        llm_registry._PRIMARY_FAILURES.clear()


# ─── behavioural — end-to-end via call() ────────────────────────────────


@requires_httpx
def test_call_records_failure_alerts_then_serves_via_fallback(monkeypatch):
    """The full fallback path: primary raises → counter ticks → Sentry
    capture fires with enriched tags → fallback _do_complete serves
    the request → caller gets a normal LlmResponse (task 4 is alerting
    ONLY; task 9 removes the fallback)."""
    from services import llm_registry
    from services.llm_types import LlmResponse

    # Clean slate.
    with llm_registry._PRIMARY_FAILURES_LOCK:
        llm_registry._PRIMARY_FAILURES.clear()

    # Drive call() with quality_scorer (runpod_vllm primary →
    # internal_vllm fallback per LLM_DEFAULTS).
    process = "quality_scorer"
    cfg = llm_registry._process_config(process)
    primary_provider = cfg["provider"]
    fallback_provider = cfg["fallback_provider"]
    assert primary_provider == "runpod_vllm"
    assert fallback_provider == "internal_vllm"

    call_log: list[str] = []

    async def fake_do_complete(*, provider, model, **kwargs):
        call_log.append(provider)
        if provider == primary_provider:
            raise RuntimeError("simulated runpod_vllm 5xx")
        # fallback path returns a normal result
        return LlmResponse(
            content="fallback ok",
            provider=provider,
            model=model,
            input_tokens=10,
            output_tokens=5,
            latency_ms=42.0,
        )

    monkeypatch.setattr(llm_registry, "_do_complete", fake_do_complete)

    # Capture Sentry calls without depending on the real SDK.
    sentry_events: list[dict] = []

    class _StubScope:
        def __init__(self) -> None:
            self.tags: dict = {}
            self.extras: dict = {}

        def set_tag(self, k, v):
            self.tags[k] = v

        def set_extra(self, k, v):
            self.extras[k] = v

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _StubSentry:
        _current = None

        @staticmethod
        def push_scope():
            _StubSentry._current = _StubScope()
            return _StubSentry._current

        @staticmethod
        def capture_message(message, level=None):
            scope = _StubSentry._current
            sentry_events.append(
                {
                    "message": message,
                    "level": level,
                    "tags": dict(scope.tags) if scope else {},
                    "extras": dict(scope.extras) if scope else {},
                }
            )

    monkeypatch.setitem(sys.modules, "sentry_sdk", _StubSentry)

    result = asyncio.run(
        llm_registry.call(
            process=process,
            messages=[{"role": "user", "content": "score this"}],
        )
    )

    # The fallback served the call.
    assert result.content == "fallback ok"
    assert result.provider == fallback_provider
    # Both providers were attempted in order.
    assert call_log == [primary_provider, fallback_provider]

    # Counter ticked exactly once for the primary failure.
    counts = llm_registry.primary_failure_counts_last_hour()
    assert counts.get((process, primary_provider)) == 1

    # Sentry capture fired with the structured tags + summary.
    assert len(sentry_events) == 1
    ev = sentry_events[0]
    assert ev["level"] == "warning"
    assert ev["tags"]["process"] == process
    assert ev["tags"]["primary_provider"] == primary_provider
    assert ev["tags"]["fallback_provider"] == fallback_provider
    # _classify_outcome maps RuntimeError to "other" — pin the
    # mechanism, not the literal value (so future taxonomy tweaks
    # don't break this test). What matters is the tag IS set.
    assert "error_type" in ev["tags"]
    assert isinstance(ev["extras"].get("error_summary"), str)
    assert "simulated runpod_vllm 5xx" in ev["extras"]["error_summary"]

    # Clean up shared state.
    with llm_registry._PRIMARY_FAILURES_LOCK:
        llm_registry._PRIMARY_FAILURES.clear()


# ─── dashboard tile ─────────────────────────────────────────────────────


@requires_httpx
def test_dashboard_tile_reports_zero_when_no_failures():
    """Quiet state — the tile must report `ok` so the dashboard's
    traffic-light reads green."""
    from services import llm_registry
    from routes.admin_dashboard import _llm_primary_failures_tile

    with llm_registry._PRIMARY_FAILURES_LOCK:
        llm_registry._PRIMARY_FAILURES.clear()

    tile = asyncio.run(_llm_primary_failures_tile(pool=None))
    assert tile["status"] == "ok"
    assert "0 fallback" in tile["primary"]


@requires_httpx
def test_dashboard_tile_escalates_with_failure_count():
    """Per the brief: low counts read as `warn`, high counts as `fail`
    so the ADHD traffic-light prioritizes the right tile."""
    from services import llm_registry
    from routes.admin_dashboard import _llm_primary_failures_tile

    with llm_registry._PRIMARY_FAILURES_LOCK:
        llm_registry._PRIMARY_FAILURES.clear()

    # 3 failures → warn
    for _ in range(3):
        llm_registry._record_primary_failure("memory_extraction", "runpod_vllm")
    tile = asyncio.run(_llm_primary_failures_tile(pool=None))
    assert tile["status"] == "warn"
    assert "3" in tile["primary"]

    # 10 more failures → fail
    for _ in range(10):
        llm_registry._record_primary_failure("nudge_generation", "runpod_vllm")
    tile2 = asyncio.run(_llm_primary_failures_tile(pool=None))
    assert tile2["status"] == "fail"

    with llm_registry._PRIMARY_FAILURES_LOCK:
        llm_registry._PRIMARY_FAILURES.clear()
