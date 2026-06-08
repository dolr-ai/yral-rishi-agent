"""Phase 25.10 follow-up — close the two latent gaps the audit surfaced.

Source-pin tests. Both fixes are tiny defensive additions; the pins
guarantee a refactor can't quietly remove them.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


def test_registry_call_gates_on_supports_chat():
    """call() must reject providers that explicitly set supports_chat=False.
    All current providers default True, so this is latent until someone
    adds a transcribe-only / embeddings-only provider — at which point
    the silent-misdispatch failure mode would resurface.

    2026-06-08 refactor: the supports_chat gate moved into the
    _do_complete() helper that backs both primary and fallback dispatches
    in call(). Behaviour preserved; assertion updated to look at the
    helper's body."""
    src = _read("app/services/llm_registry.py")
    # Find the _do_complete() function body — the gate now lives here.
    do_pos = src.find("async def _do_complete(")
    next_def = src.find("\nasync def ", do_pos + 1)
    body = src[do_pos:next_def]
    assert "supports_chat" in body
    assert "is False" in body, "use 'is False' to preserve default-True backward compat"
    assert "does not support chat" in body


def test_gemini_warns_on_unknown_content_item_type():
    """Silent drops of unknown content items masked the audio-in-messages
    failure mode. Pin the warning so the next feature that emits a new
    type (input_audio, tool_use, file_attachment) gets a breadcrumb
    instead of silent loss."""
    src = _read("app/services/llm_clients/gemini.py")
    assert "dropping unknown content item type" in src
    # Must log the keys too so triage can see what was sent without
    # leaking content (the value of t plus item.keys() is enough).
    assert "list(item.keys())" in src
