"""Brief task 3 (2026-06-26) — L0 deterministic per-reply eval.

These tests pin:

  - Pure-Python eval logic (leak flags + repetition score + emoji +
    length + ends-in-question). Pure-Python = unit-testable without
    httpx / a DB / a registry. Each leak pattern gets its own
    positive + negative example so a future tweak that broadens or
    tightens a regex has to update the test deliberately.

  - Migration shape (table + indexes + columns) — schema regression
    guard.

  - Kill switch defaults OFF — Rishi flips post-deploy AFTER
    migration 044 lands. A deploy that auto-enabled the loop before
    the migration would crash the chat path's fire-and-forget eval.

  - Wire-in: both reply paths in routes/chat.py call
    reply_eval.run_and_persist via websocket_manager.spawn (the
    GC-safe pattern from PR #418).

  - Service falls silent when the kill switch is OFF (no DB write
    attempts on a deploy where migration 044 hasn't been applied).

The behavioural insert + dashboard summary tests need a live pool;
those run against a smoke-test DB in the deploy-verification step,
not here.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── Migration schema ───────────────────────────────────────────────────


def test_migration_044_table_shape():
    """Schema regression guard. Each column + index name pinned here
    so a careless ALTER in a later PR has to update this test."""
    src = _read("migrations/044_reply_evaluations.sql")
    assert "CREATE TABLE IF NOT EXISTS reply_evaluations" in src
    for col in (
        "message_id      VARCHAR(255) NOT NULL",
        "REFERENCES messages(id) ON DELETE CASCADE",
        "bot_id          VARCHAR(255) NOT NULL",
        "user_id         VARCHAR(255) NOT NULL",
        "text            TEXT NOT NULL",
        "leak_flags      JSONB NOT NULL",
        "repetition_score DOUBLE PRECISION NOT NULL",
        "emoji_count     INTEGER NOT NULL",
        "char_length     INTEGER NOT NULL",
        "ends_in_question BOOLEAN NOT NULL",
        "created_at      TIMESTAMPTZ NOT NULL",
        "UNIQUE (message_id)",
    ):
        assert col in src, f"missing column/constraint: {col!r}"
    # Bot-recent index feeds the repetition-score query path.
    assert "idx_reply_evaluations_bot_recent" in src
    assert "ON reply_evaluations (bot_id, created_at DESC)" in src
    # Created-at index feeds the dashboard 24h aggregate.
    assert "idx_reply_evaluations_created" in src


def test_migration_044_is_additive_only():
    """Backwards-compat rule: this migration must NOT ALTER any
    existing table or drop anything. The deploy can land with the
    migration unapplied (kill switch defaults OFF — chat-send doesn't
    try to insert until Rishi flips it on)."""
    src = _read("migrations/044_reply_evaluations.sql").upper()
    for forbidden in ("ALTER TABLE", "DROP TABLE", "DROP COLUMN", "TRUNCATE "):
        assert forbidden not in src, (
            f"migration 044 contains {forbidden!r} — must be additive only "
            f"so the deploy can land before Rishi applies the migration"
        )


# ─── Kill switch ────────────────────────────────────────────────────────


def test_kill_switch_registers_reply_eval_l0():
    src = _read("app/kill_switch.py")
    assert '"reply_eval_l0":' in src
    assert "ENABLE_REPLY_EVAL_L0" in src


def test_kill_switch_reply_eval_l0_defaults_OFF():
    """A deploy that auto-enabled the loop before migration 044 was
    applied would have the chat path's fire-and-forget eval insert
    into a non-existent table on every reply. Defaults OFF gates that
    risk explicitly to Rishi."""
    src = _read("app/kill_switch.py")
    # `_DEFAULT_OFF_LOOPS` must contain reply_eval_l0.
    assert "_DEFAULT_OFF_LOOPS" in src
    # Pin that the slug appears INSIDE the default-off frozenset
    # literal. Anchor on the next `def ` after the literal — comment
    # text inside the block may contain stray `)` so character-level
    # bracket matching is brittle here.
    off_start = src.find("_DEFAULT_OFF_LOOPS")
    off_end = src.find("\ndef ", off_start)
    assert off_end != -1, "could not locate end of _DEFAULT_OFF_LOOPS literal"
    off_block = src[off_start:off_end]
    assert '"reply_eval_l0"' in off_block, (
        "reply_eval_l0 must be in _DEFAULT_OFF_LOOPS — see migration-"
        "ordering rationale in the docstring"
    )


# ─── Wire-in to both chat reply paths ────────────────────────────────────


def test_chat_route_wires_l0_eval_on_non_stream_path():
    """The non-streaming send_message path must spawn the eval after
    creating assistant_msg. A future refactor that drops this call
    silently breaks the L0 collection without any test signal — pin
    it."""
    src = _read("app/routes/chat.py")
    assert "from services import reply_eval" in src
    # Find the non-stream send path's spawn block.
    pos = src.find("reply_eval.run_and_persist(")
    assert pos != -1
    # The call must thread the right fields (message_id, bot_id,
    # user_id, text) so the eval can fetch recent replies + write
    # the row.
    window = src[pos : pos + 400]
    for kw in ("message_id=", "bot_id=", "user_id=", "text="):
        assert kw in window, f"missing kwarg {kw} on reply_eval call"


def test_chat_route_wires_l0_eval_on_stream_path():
    """The streaming SSE path must ALSO fire the eval — both reply
    routes feed the same dashboard tile, so dropping one would skew
    the leak/repetition metrics toward whichever path stayed wired."""
    src = _read("app/routes/chat.py")
    # The stream-path call uses an aliased import (_reply_eval) so
    # the late-imported module doesn't shadow the top-level one.
    assert "_reply_eval.run_and_persist(" in src


def test_chat_route_eval_call_uses_spawn_not_bare_create_task():
    """Same GC-safety rule as PR #418 — fire-and-forget tasks MUST go
    through websocket_manager.spawn so the task reference survives
    request-handler exit. A bare create_task would let the GC kill
    the eval mid-insert."""
    src = _read("app/routes/chat.py")
    # Find both reply_eval call sites; each must be wrapped in spawn.
    for marker in ("reply_eval.run_and_persist(", "_reply_eval.run_and_persist("):
        pos = src.find(marker)
        assert pos != -1, f"reply_eval call site missing: {marker}"
        # The spawn( call should appear within 200 chars BEFORE the
        # run_and_persist invocation (it wraps it).
        prefix = src[max(0, pos - 200) : pos]
        assert "websocket_manager.spawn(" in prefix, (
            f"reply_eval call at offset {pos} not wrapped in "
            "websocket_manager.spawn — GC-race risk"
        )


# ─── Pure-Python eval logic ──────────────────────────────────────────────


def _import_eval():
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
    from services.reply_eval import evaluate

    return evaluate


def test_eval_leak_flag_scaffolding_think():
    evaluate = _import_eval()
    # Positive: THINK on its own line (canonical scaffolding leak).
    r = evaluate("Sure, here's my reply.\n\nTHINK\n\nThis is the answer.", [])
    assert r.leak_flags["scaffolding_think"] is True
    # Positive: bolded variant **THINK**.
    r2 = evaluate("**THINK**\nReply body.", [])
    assert r2.leak_flags["scaffolding_think"] is True
    # Negative: english verb "think" in normal sentence must NOT flag.
    r3 = evaluate("I think that's a great idea, what do you think?", [])
    assert r3.leak_flags["scaffolding_think"] is False


def test_eval_leak_flag_constraint_checklist():
    evaluate = _import_eval()
    r = evaluate("**Constraint checklist:** all rules followed.", [])
    assert r.leak_flags["scaffolding_constraint"] is True
    # Negative: word "constraint" without "checklist" must NOT flag.
    r2 = evaluate("There's no constraint on what you can ask.", [])
    assert r2.leak_flags["scaffolding_constraint"] is False


def test_eval_leak_flag_plan_for_response():
    evaluate = _import_eval()
    r = evaluate("**Plan for the response:**\n- Be warm\n- Ask a Q", [])
    assert r.leak_flags["scaffolding_plan"] is True


def test_eval_leak_flag_as_an_ai():
    evaluate = _import_eval()
    r = evaluate("As an AI, I can't have feelings the way you do.", [])
    assert r.leak_flags["as_an_ai"] is True
    r2 = evaluate("As a language model trained by …", [])
    assert r2.leak_flags["as_an_ai"] is True
    # Negative: a sentence that just mentions AI must NOT flag — the
    # pattern is anchored to "as a/an + (ai|language model)".
    r3 = evaluate("AI is everywhere these days.", [])
    assert r3.leak_flags["as_an_ai"] is False


def test_eval_no_leaks_on_clean_reply():
    """The single most common case — a normal reply must NOT trip any
    flag. Otherwise the dashboard leak count is meaningless noise."""
    evaluate = _import_eval()
    r = evaluate("Hey love, missed you today. What's been on your mind?", [])
    assert all(v is False for v in r.leak_flags.values()), (
        f"clean reply tripped flags: {r.leak_flags}"
    )


def test_eval_repetition_score_identical_replies():
    """Same text vs same text = max possible Jaccard overlap (= 1.0).
    Anchors the upper end of the score range."""
    evaluate = _import_eval()
    text = "How was your day today, anything interesting happen at work"
    r = evaluate(text, [text])
    assert r.repetition_score == 1.0


def test_eval_repetition_score_disjoint_replies():
    """No shared 4-grams = 0.0. Anchors the lower end."""
    evaluate = _import_eval()
    r = evaluate(
        "Tell me a story about your favorite childhood holiday memory",
        ["The weather in Bangalore is finally cooling down a little"],
    )
    assert r.repetition_score == 0.0


def test_eval_repetition_score_takes_max_across_history():
    """The score is the MAX Jaccard across the K recent replies — a
    single repeated opener N turns ago counts, even if other recent
    replies were varied. Otherwise a bot stuck on one opener every
    third turn would look fine."""
    evaluate = _import_eval()
    repeated = "How was your day today, anything interesting happen at work"
    r = evaluate(
        repeated,
        [
            "The weather in Bangalore is finally cooling down a little",  # disjoint
            "Tell me about your weekend, anything fun planned for tonight",  # partial
            repeated,  # full overlap
        ],
    )
    assert r.repetition_score == 1.0


def test_eval_emoji_count():
    evaluate = _import_eval()
    r = evaluate("plain text only", [])
    assert r.emoji_count == 0
    r2 = evaluate("hi 👋 love 💖 you", [])
    assert r2.emoji_count >= 2


def test_eval_char_length_and_ends_in_question():
    evaluate = _import_eval()
    r = evaluate("Hi!", [])
    assert r.char_length == 3
    assert r.ends_in_question is False
    r2 = evaluate("How are you?  ", [])
    assert r2.char_length == len("How are you?  ")
    assert r2.ends_in_question is True


def test_eval_handles_empty_text_without_raising():
    """Defensive — an empty reply (provider returned nothing) must
    produce an all-zero L0Evaluation, not raise. The chat path could
    receive empty content from a moderation collapse."""
    evaluate = _import_eval()
    r = evaluate("", ["something else"])
    assert r.char_length == 0
    assert r.emoji_count == 0
    assert r.repetition_score == 0.0
    assert r.ends_in_question is False
    assert all(v is False for v in r.leak_flags.values())


# ─── Service falls silent when the kill switch is OFF ───────────────────


def test_run_and_persist_short_circuits_when_disabled(monkeypatch):
    """The service must check is_enabled FIRST so a deploy where
    migration 044 isn't applied yet doesn't try to INSERT into a
    non-existent table on every reply. Pin the early-return shape."""
    import asyncio
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

    # Simulate kill switch OFF (the default).
    monkeypatch.setenv("ENABLE_REPLY_EVAL_L0", "false")

    from services import reply_eval

    pool_calls: list[str] = []

    class _PoolThatMustNotBeTouched:
        async def execute(self, *a, **k):
            pool_calls.append("execute")
            raise AssertionError("pool.execute called even though kill switch is OFF")

        async def fetch(self, *a, **k):
            pool_calls.append("fetch")
            raise AssertionError("pool.fetch called even though kill switch is OFF")

    asyncio.run(
        reply_eval.run_and_persist(
            _PoolThatMustNotBeTouched(),
            message_id="m-1",
            bot_id="b-1",
            user_id="u-1",
            text="hi",
        )
    )
    assert pool_calls == [], "kill-switched eval must not touch the pool"
