"""Phase 2.7 — SSE streaming wire-format pins.

The actual stream is exercised end-to-end via curl after deploy. These tests
pin the wire format (event names, error code shape) so a refactor can't
silently break the contract docs/SSE-PROTOCOL.md ships to the mobile expert.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_sse_event_format_matches_docs():
    """`event: <name>\\ndata: <json>\\n\\n` — the spec the mobile expert
    is building EventSource handlers against."""
    from routes.chat import _sse_event

    out = _sse_event("token", {"text": "hi"})
    assert out.startswith("event: token\n")
    assert "\ndata: " in out
    assert out.endswith("\n\n")
    # Data line is parseable JSON
    data_line = [line for line in out.split("\n") if line.startswith("data: ")][0]
    parsed = json.loads(data_line[len("data: ") :])
    assert parsed == {"text": "hi"}


def test_sse_feature_flag_default_on():
    """Backend default is TRUE — mobile decides whether to USE the endpoint."""
    import config

    assert config.ENABLE_SSE_STREAMING is True


def test_sse_protocol_doc_has_three_event_types():
    """Doc must enumerate token + done + error — otherwise mobile won't know
    to handle each. Lightweight guard against accidental doc drift."""
    from pathlib import Path

    doc_path = Path(__file__).resolve().parent.parent / "docs" / "SSE-PROTOCOL.md"
    text = doc_path.read_text()
    assert "event: token" in text
    assert "event: done" in text
    assert "event: error" in text
