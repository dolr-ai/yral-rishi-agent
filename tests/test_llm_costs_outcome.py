"""Phase 25.5b — outcome + error tracking on llm_costs.

Source-pin tests for the migration shape, outcome classification, and
dispatch wiring. Behavioral verification (a real failure writes a row)
runs in production via psql after deploy.
"""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


# ─── migration 028 ────────────────────────────────────────────────────────


def test_migration_028_adds_outcome_and_error_columns():
    src = _read("migrations/028_llm_costs_outcome_tracking.sql")
    assert "ADD COLUMN IF NOT EXISTS outcome" in src
    assert "ADD COLUMN IF NOT EXISTS error_message" in src
    assert "DEFAULT 'success'" in src


def test_migration_028_indexes_dashboard_queries():
    """Per-day rejection rate per process is the load-bearing query.
    Pin the supporting indexes."""
    src = _read("migrations/028_llm_costs_outcome_tracking.sql")
    assert "idx_llm_costs_outcome_created" in src
    assert "idx_llm_costs_process_outcome" in src


def test_migration_028_documents_rule_9():
    src = _read("migrations/028_llm_costs_outcome_tracking.sql")
    assert "pg_dump" in src
    assert "Rule 9" in src


# ─── outcome classification ───────────────────────────────────────────────


def test_classify_outcome_covers_all_categories():
    """The outcome enum is what dashboards filter on. Pin every category
    that _classify_outcome can emit so a future refactor doesn't silently
    coalesce them."""
    src = _read("app/services/llm_registry.py")
    fn_start = src.find("def _classify_outcome(")
    fn_body = src[fn_start : fn_start + 2000]
    # Six categories: rate_limit / server_error / timeout / parse_error / blocked / other
    for outcome in (
        "rate_limit",
        "server_error",
        "timeout",
        "parse_error",
        "blocked",
        "other",
    ):
        assert f'"{outcome}"' in fn_body, f"missing outcome category: {outcome}"


def test_classify_outcome_dispatches_by_exception_type():
    """The mapping (exception type → outcome) is the dashboard's
    correctness contract. Pin the load-bearing instanceof checks."""
    src = _read("app/services/llm_registry.py")
    fn_start = src.find("def _classify_outcome(")
    fn_body = src[fn_start : fn_start + 2000]
    assert "LlmBlockedError" in fn_body
    assert "TimeoutException" in fn_body or "asyncio.TimeoutError" in fn_body
    assert "HTTPStatusError" in fn_body
    assert "429" in fn_body  # rate_limit


# ─── _record_outcome unified helper ───────────────────────────────────────


def test_record_outcome_helper_exists():
    src = _read("app/services/llm_registry.py")
    assert "async def _record_outcome(" in src


def test_record_outcome_truncates_error_message_at_500():
    """Per Rishi's spec: error_message truncated to 500 chars. Pin the
    slice so an unbounded error doesn't blow up the row."""
    src = _read("app/services/llm_registry.py")
    fn_start = src.find("async def _record_outcome(")
    fn_body = src[fn_start : fn_start + 3500]
    assert "[:500]" in fn_body


def test_record_outcome_writes_outcome_and_error_columns():
    src = _read("app/services/llm_registry.py")
    fn_start = src.find("async def _record_outcome(")
    fn_body = src[fn_start : fn_start + 3500]
    assert "outcome" in fn_body
    assert "error_message" in fn_body
    # INSERT must include both new columns
    assert "outcome, error_message" in fn_body.replace("\n", " ").replace("  ", " ")


def test_record_cost_now_delegates_to_record_outcome():
    """Back-compat: _record_cost stays as the success-path entry point
    but now writes through _record_outcome with outcome='success'."""
    src = _read("app/services/llm_registry.py")
    fn_start = src.find("async def _record_cost(")
    fn_body = src[fn_start : fn_start + 1500]
    assert "_record_outcome" in fn_body
    assert '"success"' in fn_body


# ─── dispatch wiring records failures too ─────────────────────────────────


def test_call_records_failure_on_exception():
    """call() must record outcome != success when the underlying client
    raises. Pre-25.5b, exceptions just propagated — no cost row written.

    2026-06-08 refactor: dispatch + outcome-recording lives in
    _do_complete() now (the fallback layer in call() runs _do_complete
    twice — once for primary, once for fallback). The failure-recording
    behaviour is asserted in _do_complete's body; call() must still
    re-raise when no fallback path saves the request."""
    src = _read("app/services/llm_registry.py")
    do_start = src.find("async def _do_complete(")
    call_start = src.find("async def call(\n    *,")
    do_body = src[do_start:call_start]
    assert "except Exception as exc:" in do_body
    assert "_record_outcome(" in do_body
    assert "_classify_outcome(exc)" in do_body
    assert "raise" in do_body  # exception still propagates out of _do_complete

    # call() must still propagate when no fallback exists.
    call_stream_start = src.find("async def call_stream(")
    call_body = src[call_start:call_stream_start]
    assert "raise" in call_body, "call() must re-raise when no fallback applies"


def test_call_stream_records_failure_on_exception():
    src = _read("app/services/llm_registry.py")
    stream_start = src.find("async def call_stream(")
    transcribe_start = src.find("async def call_transcribe(")
    stream_body = src[stream_start:transcribe_start]
    assert "except Exception as exc:" in stream_body
    assert "_record_outcome(" in stream_body
    assert "raise" in stream_body


def test_call_transcribe_records_failure_on_exception():
    src = _read("app/services/llm_registry.py")
    transcribe_start = src.find("async def call_transcribe(")
    transcribe_body = src[transcribe_start : transcribe_start + 4000]
    assert "except Exception as exc:" in transcribe_body
    assert "_record_outcome(" in transcribe_body
    assert "raise" in transcribe_body


def test_failure_rows_have_cost_usd_zero():
    """Failure rows must have cost_usd=0 (we didn't pay for them).
    _record_outcome computes cost from token counts, and failure paths
    pass tokens=0 implicitly (the default kwarg)."""
    src = _read("app/services/llm_registry.py")
    fn_start = src.find("async def _record_outcome(")
    fn_body = src[fn_start : fn_start + 3500]
    # The defaults on the signature: input_tokens=0, output_tokens=0
    assert "input_tokens: int = 0" in fn_body
    assert "output_tokens: int = 0" in fn_body
