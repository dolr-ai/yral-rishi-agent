"""21γ.P26 — Langfuse trace input/output rollup fix.

Pre-fix: `trace_generation()` propagated input/output onto the
generation body (so the child generation rendered correctly in Langfuse)
but NOT onto the trace body. Result: the Langfuse UI's trace-summary
view showed "Looks like this trace didn't receive an input or output"
on every chat-response trace, while the child generation was fully
populated.

Post-fix: trace-create body carries `input` + `output` with the same
2000-char cap the generation uses. Trace summary rollup populates
correctly.

Source-pin tests + one behavioural pin via httpx-mock are below.
Behavioural test verifies the actual payload shape that gets posted to
`/api/public/ingestion` — that's what Langfuse parses, so that's where
a regression would bite first.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "app" / "services" / "langfuse_tracing.py"


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── source-pin: trace body carries input + output ──────────────────────


def test_trace_body_includes_input_field():
    """The trace body dict MUST include `input`. Pin the literal so a
    future refactor that drops it (intentional or not) fails CI.

    2026-06-26 PR #421: the trace body was extracted to a local
    `trace_body = {...}` so the sessionId can be set conditionally.
    Anchor the search on `trace_body =` and scan to the closing `}`
    so the literal still resolves to the same dict as before."""
    src = MODULE.read_text()
    pos = src.find("trace_body: dict = {")
    assert pos != -1, "trace_body assignment not found"
    end = src.find("\n    if conversation_id:", pos)
    trace_block = src[pos:end] if end != -1 else src[pos : pos + 2000]
    assert '"input": input_text[:2000]' in trace_block


def test_trace_body_includes_output_field():
    """Same as above for `output`. Both fields together drive the
    Langfuse UI's trace-summary rollup."""
    src = MODULE.read_text()
    pos = src.find("trace_body: dict = {")
    end = src.find("\n    if conversation_id:", pos)
    trace_block = src[pos:end] if end != -1 else src[pos : pos + 2000]
    assert '"output": output_text[:2000]' in trace_block


def test_trace_and_generation_use_same_truncation_cap():
    """Both trace + generation render the same snippet in the Langfuse
    UI. Mismatched truncation would show different text at the two
    levels and confuse triage. Pin both halves to the same 2000-char
    cap so a future tweak to one without the other is caught."""
    src = MODULE.read_text()
    # Count the [:2000] truncation appearances — should be exactly 4:
    # input + output on trace body + input + output on generation body
    assert src.count("[:2000]") >= 4, (
        "expected ≥4 [:2000] truncation appearances (input + output × "
        "trace body + generation body); fewer means the trace + "
        "generation truncation policies have drifted apart"
    )


def test_trace_body_metadata_field_unchanged():
    """Belt-and-braces: the metadata field stays where it was. A
    refactor that reshuffled the trace body could accidentally drop
    metadata (which carries conversation_id) — pin it stays."""
    src = MODULE.read_text()
    pos = src.find("trace_body: dict = {")
    end = src.find("\n    if conversation_id:", pos)
    trace_block = src[pos:end] if end != -1 else src[pos : pos + 2000]
    assert '"metadata":' in trace_block
    assert "conversation_id" in trace_block


# ─── behavioural pin: real payload shape via httpx_mock ────────────────


def test_trace_generation_posts_input_and_output_on_trace_body(monkeypatch):
    """End-to-end: when `trace_generation()` runs with real input +
    output strings, the JSON payload posted to /api/public/ingestion
    has `input` + `output` populated on the TRACE body (not just the
    generation body). This is the byte-level regression guard for
    the actual Langfuse contract — a refactor that breaks the source-
    pin tests would also break this one."""
    import pytest

    httpx = pytest.importorskip("httpx")
    captured_payload: dict = {}

    class _FakeResponse:
        status_code = 207

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured_payload["url"] = url
        captured_payload["body"] = json
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", _fake_post)

    # Force auth to a known-non-None state so the early-return guard
    # in _get_auth() doesn't skip the post entirely.
    from services import langfuse_tracing

    monkeypatch.setattr(langfuse_tracing, "_auth_header", "Basic fake")

    langfuse_tracing.trace_generation(
        trace_name="chat-response",
        user_id="test-user",
        model="gemini-2.5-flash",
        provider="openrouter",
        input_text="hello there",
        output_text="hi back",
        latency_ms=600,
        conversation_id="conv-abc",
    )

    assert "body" in captured_payload, "httpx.post never called"
    batch = captured_payload["body"]["batch"]
    trace_entry = next(b for b in batch if b["type"] == "trace-create")
    gen_entry = next(b for b in batch if b["type"] == "generation-create")

    # The whole point of this PR — input + output on the trace body
    assert trace_entry["body"]["input"] == "hello there"
    assert trace_entry["body"]["output"] == "hi back"

    # Regression: generation body still carries them too (pre-existing
    # behaviour we're NOT breaking)
    assert gen_entry["body"]["input"] == "hello there"
    assert gen_entry["body"]["output"] == "hi back"

    # conversation_id still on trace metadata
    assert trace_entry["body"]["metadata"]["conversation_id"] == "conv-abc"
