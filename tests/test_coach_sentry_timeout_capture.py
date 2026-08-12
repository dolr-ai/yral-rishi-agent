"""Coach PR-5 — Sentry capture on soul_file_coach timeout.

Plan §4 item E + dev report's 4×110s server_error rows from 2026-06-09:
those fired NO Sentry alert because the existing leak-guard is scoped
to ASYNC_PROCESSES_NEVER_GEMINI (correctly excludes soul_file_coach
since it's a sync creator-waiting path). That left user-facing
timeouts silent.

Mix of source-pin + behavioral tests. The dispatch path itself has
its own coverage in test_llm_registry — this file pins the new
Sentry call.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[1]


# ─── source-pin: the alert site exists with the right gating ────────────


def test_record_outcome_has_sentry_capture_for_timeouts():
    """_record_outcome must end with a Sentry capture branch gated on
    outcome=='timeout' AND process in the user-facing allow-list."""
    src = (REPO / "app" / "services" / "llm_registry.py").read_text()
    pos = src.find("async def _record_outcome(")
    assert pos != -1
    end_pos = src.find("\n_USER_FACING_SYNC_PROCESSES", pos)
    body = src[pos : end_pos if end_pos != -1 else pos + 6000]
    # The two-pronged gate
    assert 'outcome == "timeout"' in body
    assert "_USER_FACING_SYNC_PROCESSES" in body
    # And the actual capture call
    assert "_sentry.capture_message(" in body


def test_user_facing_sync_processes_includes_soul_file_coach():
    """Plan §4 item E pins soul_file_coach as the explicit target.
    A future PR can extend the set; today this is the minimum scope."""
    from services.llm_registry import _USER_FACING_SYNC_PROCESSES

    assert "soul_file_coach" in _USER_FACING_SYNC_PROCESSES


def test_user_facing_sync_processes_excludes_async_background():
    """Async-background processes have their own observability via the
    leak-guard (_check_async_gemini_leak). Including them here would
    fire two Sentry events per timeout — the explicit allow-list
    keeps the new alert narrow."""
    from services.llm_registry import (
        ASYNC_PROCESSES_NEVER_GEMINI,
        _USER_FACING_SYNC_PROCESSES,
    )

    overlap = _USER_FACING_SYNC_PROCESSES & ASYNC_PROCESSES_NEVER_GEMINI
    assert overlap == set(), (
        f"async-background processes {overlap} are in BOTH sets — would "
        f"double-fire Sentry on timeout. Choose one alerting path."
    )


def test_capture_is_swallowed_on_sentry_import_failure():
    """Source-pin the try/except wrapping the sentry_sdk import so a
    sentry-side outage can't break the dispatch path."""
    src = (REPO / "app" / "services" / "llm_registry.py").read_text()
    pos = src.find("async def _record_outcome(")
    end_pos = src.find("\n_USER_FACING_SYNC_PROCESSES", pos)
    body = src[pos : end_pos if end_pos != -1 else pos + 6000]
    # The capture branch must be inside its own try/except
    # — find the section after the gate
    gate_pos = body.find('outcome == "timeout"')
    after_gate = body[gate_pos : gate_pos + 1500]
    assert "try:" in after_gate
    assert "import sentry_sdk" in after_gate
    assert "except Exception:" in after_gate


# ─── behavioral: capture fires (or not) on the right inputs ──────────────


def test_capture_fires_on_soul_file_coach_timeout(monkeypatch):
    """Real call to _record_outcome with outcome='timeout' + the
    target process MUST result in sentry_sdk.capture_message being
    called. Don't hit the DB — patch get_pool to no-op."""
    import asyncio

    import services.llm_registry as registry

    captured = []

    fake_sentry = MagicMock()

    def _fake_capture(msg, level=None):
        captured.append((msg, level))

    fake_sentry.capture_message = _fake_capture
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    # We don't need to stub get_pool: the existing DB try/except inside
    # _record_outcome already swallows any DB error (missing asyncpg in
    # the test venv, missing table, etc.) and the function continues to
    # the Sentry branch below. That's exactly the production behavior
    # we want — cost-recording failure must not block the alert.
    asyncio.run(
        registry._record_outcome(
            "soul_file_coach",
            provider="gemini",
            model="gemini-2.5-flash",
            outcome="timeout",
            latency_ms=110000.0,
        )
    )
    assert len(captured) == 1
    msg, level = captured[0]
    assert "soul_file_coach" in msg
    assert "timeout" in msg.lower()
    assert level == "error"


def test_capture_does_NOT_fire_on_success(monkeypatch):
    """Sanity: success outcomes must NOT fire a Sentry capture."""
    import asyncio

    import services.llm_registry as registry

    captured = []
    fake_sentry = MagicMock()
    fake_sentry.capture_message = lambda msg, level=None: captured.append(msg)
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    asyncio.run(
        registry._record_outcome(
            "soul_file_coach",
            provider="gemini",
            model="gemini-2.5-flash",
            outcome="success",
            latency_ms=9500.0,
        )
    )
    assert captured == []


def test_capture_does_NOT_fire_on_non_allowlisted_process(monkeypatch):
    """A timeout on quality_scorer (an async-background process) must
    NOT fire this Sentry alert — the existing leak-guard covers it."""
    import asyncio

    import services.llm_registry as registry

    captured = []
    fake_sentry = MagicMock()
    fake_sentry.capture_message = lambda msg, level=None: captured.append(msg)
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    asyncio.run(
        registry._record_outcome(
            "quality_scorer",
            provider="runpod_vllm",
            model="Qwen/Qwen3.6-35B-A3B-FP8",
            outcome="timeout",
            latency_ms=110000.0,
        )
    )
    assert captured == []


def test_capture_includes_provider_and_latency_in_message():
    """Sentry message must carry enough context to triage from the
    event alone — process + provider + latency_ms."""
    src = (REPO / "app" / "services" / "llm_registry.py").read_text()
    pos = src.find("async def _record_outcome(")
    end_pos = src.find("\n_USER_FACING_SYNC_PROCESSES", pos)
    body = src[pos : end_pos if end_pos != -1 else pos + 6000]
    # The capture message must contain {process}, {provider}, and {latency_ms}
    capture_pos = body.find("_sentry.capture_message(")
    msg_block = body[capture_pos : capture_pos + 600]
    assert "{process" in msg_block
    assert "{provider}" in msg_block
    assert "{latency_ms}" in msg_block
