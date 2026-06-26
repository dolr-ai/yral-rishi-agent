"""NSFW streaming gate — regression test for the 2026-06-26 fix.

Before this fix, `generate_response_stream(is_nsfw=True)` yielded a
NO_PROVIDER error event before any provider call. Mobile rendered the
canned "Bot is not available to chat right now, try again later" text
and the user couldn't talk to NSFW bots like Tara at all
(Rishi confirmed on prod 2026-06-25).

The fix routes NSFW through the non-streaming
`user_chat_main_nsfw` process and wraps the full reply in a single
synthetic ('text', content) event so mobile reads the SSE shape
unchanged — same `event: token` then `event: done` envelope as the
SFW path. The chat.py SSE consumer is none the wiser.

These tests pin (a) the new behavioural contract: NSFW yields a
non-empty text + a done LlmResponse, and (b) a regression guard
against the old unconditional NO_PROVIDER gate coming back.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

REPO = Path(__file__).resolve().parents[1]

try:
    import httpx  # noqa: F401

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

requires_httpx = pytest.mark.skipif(
    not _HTTPX_AVAILABLE, reason="httpx not installed (CI only)"
)


# ─── source-pin — the old NO_PROVIDER gate must not come back ───────────


def test_nsfw_gate_not_yielding_no_provider_before_call():
    """Regression guard: the old shape was

        if is_nsfw:
            yield ('error', LlmResponse(error_code='NO_PROVIDER', ...))
            return

    placed at the very top of generate_response_stream BEFORE any
    provider call. That returned a typed error to mobile on every NSFW
    chat (Tara, etc.) and rendered "Bot not available" — Rishi could
    not chat with Tara in prod 2026-06-25. A future refactor that puts
    the gate back must edit this test AND re-verify against the prod
    breakage that motivated removing it."""
    src = (REPO / "app" / "services" / "ai_client.py").read_text()
    pos = src.find("async def generate_response_stream(")
    end = src.find("\nasync def ", pos + 1)
    body = src[pos:end] if end != -1 else src[pos:]

    # The current shape calls `await llm_registry.call(process=
    # "user_chat_main_nsfw", ...)` inside the is_nsfw branch.
    assert 'process="user_chat_main_nsfw"' in body, (
        "NSFW path must invoke the registry's non-streaming nsfw process"
    )
    assert "if is_nsfw:" in body, "NSFW branch must still exist"

    # The pre-fix unconditional NO_PROVIDER yield was the FIRST thing
    # the function did. Specifically guard against THAT shape coming
    # back: a NO_PROVIDER yield textually preceding any registry call.
    no_provider_pos = body.find('error_code="NO_PROVIDER"')
    call_pos = body.find("llm_registry.call(")
    if no_provider_pos != -1:
        assert call_pos != -1, (
            "NSFW must call the registry; finding a NO_PROVIDER yield "
            "without any registry call means we regressed to the "
            "pre-2026-06-26 gate"
        )
        assert no_provider_pos > call_pos, (
            "a NO_PROVIDER yield that precedes any registry call is the "
            "pre-2026-06-26 regression shape — mobile would render "
            "'Bot not available' on every NSFW chat"
        )


# ─── behavioural — synthesize stream from non-streaming call ─────────────


@requires_httpx
def test_nsfw_stream_yields_text_then_done_with_real_content():
    """The load-bearing test. With a stubbed registry that returns a
    realistic LlmResponse, generate_response_stream(is_nsfw=True) must:
      1. Yield at least one ('text', non-empty) event so the chat.py
         SSE consumer has something to emit as a token event.
      2. Yield a ('done', LlmResponse) event so the SSE 'done' marker
         fires and the assistant message gets persisted.
      3. NOT yield ('error', ...).
    Mobile's SSE contract is preserved end-to-end."""
    from services import ai_client, llm_registry
    from services.llm_types import LlmResponse

    captured_process = {}

    async def fake_call(*, process, messages, temperature, max_tokens):
        captured_process["value"] = process
        return LlmResponse(
            content="Hey love, missed you. What's been on your mind?",
            provider="openrouter",
            model="google/gemini-2.5-flash",
            input_tokens=42,
            output_tokens=11,
            latency_ms=812.0,
        )

    async def run():
        events: list[tuple[str, object]] = []
        async for kind, value in ai_client.generate_response_stream(
            system_instructions="You are Tara.",
            conversation_history=[],
            user_message="hey",
            is_nsfw=True,
        ):
            events.append((kind, value))
        return events

    # Patch llm_registry.call without importing pytest's monkeypatch
    # (this test runs as a plain asyncio.run; pytest fixtures don't
    # flow through). Restore on teardown.
    original = getattr(llm_registry, "call", None)
    llm_registry.call = fake_call
    try:
        events = asyncio.run(run())
    finally:
        if original is not None:
            llm_registry.call = original

    kinds = [k for k, _ in events]
    assert kinds.count("error") == 0, (
        f"NSFW stream must not emit an error event on the happy path; "
        f"got events: {kinds}"
    )
    assert "text" in kinds, "NSFW stream must emit at least one text event"
    assert kinds[-1] == "done", "the final event must be ('done', LlmResponse)"

    text_events = [v for k, v in events if k == "text"]
    assert all(isinstance(v, str) for v in text_events)
    full = "".join(text_events)
    assert full == "Hey love, missed you. What's been on your mind?", (
        "the synthesized text event must carry the registry's full reply "
        "byte-for-byte — truncation here would silently shorten replies"
    )

    done_payload = events[-1][1]
    assert isinstance(done_payload, LlmResponse)
    assert done_payload.content == full
    assert done_payload.provider == "openrouter"
    assert done_payload.model == "google/gemini-2.5-flash"
    assert done_payload.input_tokens == 42
    assert done_payload.output_tokens == 11

    # And the routing decision was correct: NSFW chat → nsfw process.
    assert captured_process.get("value") == "user_chat_main_nsfw"


@requires_httpx
def test_nsfw_stream_handles_empty_reply_without_yielding_text():
    """If the underlying provider returns empty content (e.g. all the
    output was a moderation refusal collapsed to ''), we must NOT yield
    a `('text', '')` event — the chat.py consumer would emit an empty
    SSE token. Still yield ('done', ...) so the stream terminates
    cleanly; chat.py's downstream `not full_text.strip()` check then
    surfaces the empty-reply TRANSIENT error."""
    from services import ai_client, llm_registry
    from services.llm_types import LlmResponse

    async def fake_call(*, process, messages, temperature, max_tokens):
        return LlmResponse(
            content="",
            provider="openrouter",
            model="google/gemini-2.5-flash",
            input_tokens=10,
            output_tokens=0,
            latency_ms=120.0,
        )

    async def run():
        events: list[tuple[str, object]] = []
        async for kind, value in ai_client.generate_response_stream(
            system_instructions="",
            conversation_history=[],
            user_message="hey",
            is_nsfw=True,
        ):
            events.append((kind, value))
        return events

    original = llm_registry.call
    llm_registry.call = fake_call
    try:
        events = asyncio.run(run())
    finally:
        llm_registry.call = original

    kinds = [k for k, _ in events]
    assert "text" not in kinds, (
        "empty-content reply must not emit a text event — that would "
        "produce an empty SSE token event downstream"
    )
    assert kinds == ["done"], (
        f"empty-reply NSFW stream must yield just ('done', ...); got {kinds}"
    )
