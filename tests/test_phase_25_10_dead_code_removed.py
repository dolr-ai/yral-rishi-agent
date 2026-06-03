"""Phase 25.10 — regression guard: orphan legacy helpers stay removed.

Five functions and one constant in ai_client.py were 0-caller after the
Phase 25.3b extraction. Removed in this audit pass. The risk if they
return via copy-paste: NSFW path or memory-extraction path silently
bypassing the registry (no cost recording, no rejection tracking).
"""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


def test_extract_memories_removed():
    """Superseded by memory.py:extract_and_store via
    llm_registry.call(process='memory_extraction'). The legacy path
    bypassed cost recording + outcome tracking. We assert the
    active-syntax forms are gone — the names can still appear in the
    removal-note comment for archaeology purposes."""
    src = _read("app/services/ai_client.py")
    assert "async def extract_memories(" not in src
    # The constant assignment (line-start, =) — distinct from the
    # comment mention which is preceded by '# '
    assert "\nMEMORY_EXTRACTION_PROMPT = " not in src


def test_call_gemini_removed():
    """Replaced by gemini.complete() via llm_registry.call(). Live
    callers were migrated in PR #251 (25.3 partial). The function
    became 0-caller after extract_memories was the last orphan
    caller — both removed together."""
    src = _read("app/services/ai_client.py")
    assert "async def _call_gemini(" not in src


def test_stream_gemini_removed():
    """Replaced by gemini.complete_stream() via llm_registry.call_stream()."""
    src = _read("app/services/ai_client.py")
    assert "async def _stream_gemini(" not in src


def test_build_gemini_contents_removed():
    """Replaced by gemini._messages_to_gemini_contents()."""
    src = _read("app/services/ai_client.py")
    assert "async def _build_gemini_contents(" not in src


def test_openrouter_client_helper_removed():
    """Replaced by openai_compatible.complete() via llm_registry.call()
    for the user_chat_main_nsfw process. The legacy SDK helper had a
    module-level singleton client + no concurrency cap — registry path
    has both."""
    src = _read("app/services/ai_client.py")
    assert "def get_openrouter_client(" not in src
    assert "_openrouter_client: AsyncOpenAI" not in src
    # AsyncOpenAI import removed (only user was get_openrouter_client)
    assert "from openai import AsyncOpenAI" not in src


def test_removal_note_documented_in_source():
    """A future reader looking at ai_client.py needs to know WHY these
    functions are missing so they don't copy-paste them back from git
    history. Pin the removal note."""
    src = _read("app/services/ai_client.py")
    assert "Phase 25.10" in src
    assert "get_openrouter_client" in src  # mentioned in the removal note
    assert "extract_memories" in src  # mentioned in the removal note
