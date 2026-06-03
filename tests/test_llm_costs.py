"""Phase 25.5 — per-call LLM cost recording.

Source-pin tests for the table schema + recording logic. Live wire-level
verification (a real call writes a real row) runs in production via psql
after deploy.
"""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


# ─── migration shape ──────────────────────────────────────────────────────


def test_migration_027_creates_llm_costs_table():
    src = _read("migrations/027_llm_costs.sql")
    assert "CREATE TABLE IF NOT EXISTS llm_costs" in src
    # Required columns for the design (Q4 cost_basis split)
    for col in (
        "process",
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "cost_basis",
        "user_id",
        "conversation_id",
        "request_id",
        "latency_ms",
        "created_at",
    ):
        assert col in src, f"missing column {col!r} in migration"


def test_migration_027_indexes_match_dashboard_queries():
    """Dashboard queries by created_at + (user_id, created_at) + (cost_basis,
    created_at). Pin the supporting indexes so a future schema cleanup
    doesn't drop them silently."""
    src = _read("migrations/027_llm_costs.sql")
    assert "idx_llm_costs_created_at" in src
    assert "idx_llm_costs_user_created" in src
    assert "idx_llm_costs_basis_created" in src


def test_migration_027_documents_rule_9():
    src = _read("migrations/027_llm_costs.sql")
    assert "pg_dump" in src
    assert "Rule 9" in src


def test_migration_027_explains_cost_basis_split():
    """The 'real' vs 'synthetic' distinction is the load-bearing reason
    for this whole table. Pin it in the SQL comments so a future
    reader doesn't have to chase the design doc."""
    src = _read("migrations/027_llm_costs.sql")
    assert "real" in src and "synthetic" in src
    assert "internal_vllm" in src


# ─── _record_cost helper ──────────────────────────────────────────────────


def test_record_cost_helper_exists():
    src = _read("app/services/llm_registry.py")
    assert "async def _record_cost(" in src


def test_record_cost_reads_rates_from_PROVIDERS():
    """Per-1k-token rates live in PROVIDERS dict, NOT hardcoded in the
    insert. That keeps internal_vllm's synthetic $0.00005/1k tunable
    without a code change. (25.5b: cost math moved into _record_outcome;
    _record_cost is now a thin shim that delegates.)"""
    src = _read("app/services/llm_registry.py")
    rec_fn_start = src.find("async def _record_outcome(")
    rec_fn_body = src[rec_fn_start : rec_fn_start + 3500]
    assert "PROVIDERS.get(provider)" in rec_fn_body
    assert "cost_per_1k_input_usd" in rec_fn_body
    assert "cost_per_1k_output_usd" in rec_fn_body


def test_record_cost_splits_real_vs_synthetic_via_cost_basis_column():
    """The cost_basis column is what powers the dashboard's "real $ vs
    compute share" split. Pin that the helper reads cost_basis from
    PROVIDERS and writes it as a column."""
    src = _read("app/services/llm_registry.py")
    rec_fn_start = src.find("async def _record_outcome(")
    rec_fn_body = src[rec_fn_start : rec_fn_start + 3500]
    assert "cost_basis" in rec_fn_body


def test_record_cost_is_best_effort_swallows_db_errors():
    """If migration 027 hasn't been applied or DB is temporarily down,
    cost recording MUST NOT break the LLM call. Pin the try/except
    pattern + the warning log."""
    src = _read("app/services/llm_registry.py")
    rec_fn_start = src.find("async def _record_outcome(")
    rec_fn_body = src[rec_fn_start : rec_fn_start + 3500]
    assert "try:" in rec_fn_body
    assert "except Exception" in rec_fn_body
    assert "logger.warning" in rec_fn_body


# ─── dispatch wiring ──────────────────────────────────────────────────────


def test_call_records_cost_after_success():
    """The non-streaming dispatch must call _record_cost on the LlmResponse
    BEFORE returning to the caller."""
    src = _read("app/services/llm_registry.py")
    # In call(), the result variable + _record_cost call must appear before return
    call_start = src.find("async def call(\n    *,")
    call_stream_start = src.find("async def call_stream(")
    call_body = src[call_start:call_stream_start]
    assert "await client_module.complete(" in call_body
    assert "await _record_cost(" in call_body


def test_call_stream_records_cost_after_stream_completes():
    """Streaming counterpart must tally tokens from the 'usage' yield
    (Anshuman gist quirk) then record after the stream drains."""
    src = _read("app/services/llm_registry.py")
    stream_start = src.find("async def call_stream(")
    transcribe_start = src.find("async def call_transcribe(")
    stream_body = src[stream_start:transcribe_start]
    # Must accumulate token counts from the 'usage' yield
    assert "input_tokens =" in stream_body or "prompt_tokens" in stream_body
    assert "output_tokens =" in stream_body or "candidatesTokenCount" in stream_body
    # And call _record_cost after the loop
    assert "await _record_cost(" in stream_body


def test_call_transcribe_records_cost():
    src = _read("app/services/llm_registry.py")
    transcribe_start = src.find("async def call_transcribe(")
    transcribe_body = src[transcribe_start : transcribe_start + 2500]
    assert "await _record_cost(" in transcribe_body


def test_all_3_dispatch_functions_accept_attribution_kwargs():
    """user_id / conversation_id / request_id are the attribution
    columns. All 3 dispatch functions accept them as optional kwargs
    so callers can pass them when known."""
    src = _read("app/services/llm_registry.py")
    for fn in (
        "async def call(",
        "async def call_stream(",
        "async def call_transcribe(",
    ):
        fn_start = src.find(fn)
        assert fn_start > 0, f"{fn} not found"
        # Look at the next 30 lines for the kwargs
        fn_sig = src[fn_start : fn_start + 1200]
        assert "user_id: str | None" in fn_sig, f"{fn} missing user_id kwarg"
        assert "conversation_id: str | None" in fn_sig, f"{fn} missing conversation_id"
        assert "request_id: str | None" in fn_sig, f"{fn} missing request_id"
