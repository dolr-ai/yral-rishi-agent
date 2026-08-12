"""Phase 5.6 — streak tracker.

Pure-function pins. The SQL update is exercised against the live cluster via
the deploy verification step.
"""


def test_interval_is_daily():
    """24h cycle. Below this we'd thrash the DB; above it streaks lag a day."""
    from services.streak_tracker import STREAK_UPDATE_INTERVAL_SEC

    assert STREAK_UPDATE_INTERVAL_SEC == 24 * 60 * 60


def test_initial_delay_avoids_startup_thrash():
    """5 min startup delay so rolling deploys don't fire an immediate scan."""
    from services.streak_tracker import INITIAL_DELAY_SEC

    assert INITIAL_DELAY_SEC >= 60


def test_streak_block_silent_below_3():
    """Streaks 0-2 days are not interesting enough to mention; an empty
    block keeps the proactive prompt clean."""
    from services.proactive import _streak_block

    assert _streak_block(0) == ""
    assert _streak_block(1) == ""
    assert _streak_block(2) == ""


def test_streak_block_mentions_3_day_streak_optionally():
    from services.proactive import _streak_block

    block = _streak_block(3)
    assert "3 days in a row" in block
    assert "Optional" in block or "optional" in block.lower()


def test_streak_block_warmly_acknowledges_7_plus():
    """7+ days is a real signal; prompt nudges Gemini to acknowledge it
    without going overboard."""
    from services.proactive import _streak_block

    block = _streak_block(7)
    assert "7 days in a row" in block
    assert "warm" in block.lower() or "naturally" in block.lower()

    block30 = _streak_block(30)
    assert "30 days in a row" in block30


# ─── 2026-06-04 — empty-exception logging hygiene ───────────────────────


def _read_streak_source():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    return (repo / "app/services/streak_tracker.py").read_text()


def test_streak_loop_logs_exception_type_and_repr():
    """Some asyncpg / Redis errors stringify to "". Previous
    `f"...: {e}"` made the log line entirely blank — operator had
    nothing to triage. Pin the type-name + repr so the level line
    always carries signal."""
    src = _read_streak_source()
    assert "logger.exception(" in src, (
        "streak_loop must use logger.exception for traceback"
    )
    assert "type(e).__name__" in src
    assert "%r" in src or "{e!r}" in src, (
        "repr must appear so empty-str exceptions still log"
    )


def test_asyncpg_result_parse_failure_is_logged():
    """When pool.execute returns something we can't parse as 'UPDATE N',
    don't silently fall through — log the raw result so we can see what
    asyncpg actually sent.

    2026-06-26: the two parse sites were folded into _parse_count() as
    part of the deadlock-fix refactor; same safety property, one site."""
    src = _read_streak_source()
    assert "could not parse asyncpg result" in src
    assert "def _parse_count" in src, (
        "the shared parse helper must exist so both update + reset paths "
        "get the same logging on parse failure"
    )


# ─── 2026-06-26 — deadlock-fix invariants (Sentry #124 + #246) ──────────


def test_chunked_under_500_to_avoid_lock_storm():
    """The whole point of the deadlock fix is bounding the lock set per
    transaction. If CHUNK_SIZE drifts up to thousands, the deadlock
    pattern returns."""
    from services.streak_tracker import CHUNK_SIZE

    assert 0 < CHUNK_SIZE <= 500, (
        "CHUNK_SIZE must stay ≤500 — larger batches recreate the lock "
        "storm that caused Sentry #124 (TimeoutError) + #246 "
        "(DeadlockDetectedError) on 2026-06-24"
    )


def test_per_statement_timeout_caps_a_hot_row():
    """A 10s ceiling per statement prevents a single hot conversation
    row from stalling the whole pass for the original 5-minute
    statement-timeout window."""
    from services.streak_tracker import STATEMENT_TIMEOUT_MS

    assert STATEMENT_TIMEOUT_MS <= 30_000, (
        "STATEMENT_TIMEOUT_MS must stay ≤30s — bigger windows lose the "
        "guarantee that a hot lock can't stall the whole streak pass"
    )
    assert STATEMENT_TIMEOUT_MS >= 1_000, (
        "and ≥1s so normal-case chunks don't trip the cap"
    )


def test_update_sql_uses_skip_locked_with_deterministic_order():
    """ORDER BY id + FOR UPDATE SKIP LOCKED is the deadlock-avoidance
    contract. Deterministic order = all writers take locks in the same
    sequence, so no cycle. SKIP LOCKED = we yield to concurrent writers
    instead of waiting (those rows roll forward to the next 24h pass)."""
    from services.streak_tracker import _UPDATE_CHUNK_SQL, _RESET_CHUNK_SQL

    for sql in (_UPDATE_CHUNK_SQL, _RESET_CHUNK_SQL):
        normalized = " ".join(sql.split()).lower()
        assert "order by id" in normalized
        assert "for update skip locked" in normalized


def test_chunk_runs_inside_transaction_with_local_timeout():
    """SET LOCAL only takes effect inside a transaction; outside one it
    becomes a no-op session setting that could leak across pool checkouts.
    Pin both."""
    src = _read_streak_source()
    assert "async with conn.transaction():" in src
    assert "SET LOCAL statement_timeout" in src


def test_snapshot_pass_is_read_only_no_lock():
    """The Step-1 fetch on `messages` must not hold any conversations
    lock — that's the whole reason we split it out from the big UPDATE.
    Pin that the snapshot query is a pure SELECT against `messages`."""
    src = _read_streak_source()
    # Look for the fetch that reads from messages without FOR UPDATE.
    idx = src.find("FROM messages m")
    assert idx > 0, "the messages snapshot read must be a plain SELECT"
    window = src[idx : idx + 400].upper()
    assert "FOR UPDATE" not in window, (
        "the messages snapshot read must NOT hold a lock — that brings "
        "the deadlock back"
    )
