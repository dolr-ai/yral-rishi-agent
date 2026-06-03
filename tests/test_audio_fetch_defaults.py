"""Phase 25.3b extraction trailing-edge bug #2 — gemini.transcribe was
reusing the image fetcher (_fetch_image_bytes_and_mime) which defaults
to image/jpeg + 5 MB cap. Audio bytes labeled image/jpeg → Gemini
rejects → no candidates → mobile sees "transcription unavailable."

Fix (Option B per Rishi): forked _fetch_audio_bytes_and_mime with
audio defaults — audio/mp4 + 20 MB cap from config.MAX_AUDIO_SIZE_BYTES.
Symmetric, self-documenting at the call site (CLAUDE.md rule 1).
"""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


def test_audio_helper_exists_with_audio_defaults():
    """Pin the audio-shaped helper. Image and audio helpers stay
    parallel (Rishi's Option B) — neither call site can accidentally
    use the wrong defaults."""
    src = _read("app/services/ai_client.py")
    assert "async def _fetch_audio_bytes_and_mime(" in src
    # Audio MIME default — NOT image/jpeg
    assert '"audio/mp4"' in src
    # Audio size cap — pulled from config, NOT _MAX_IMAGE_BYTES
    assert "config.MAX_AUDIO_SIZE_BYTES" in src
    # Sanity check is for audio/ prefix
    assert 'mime.startswith("audio/")' in src


def test_audio_helper_uses_audio_specific_timeout():
    """Voice notes are ~MB; image timeout (5s) is too tight for cold
    fetches over 4G. Pin the dedicated _AUDIO_DOWNLOAD_TIMEOUT."""
    src = _read("app/services/ai_client.py")
    assert "_AUDIO_DOWNLOAD_TIMEOUT" in src
    # The audio function uses the audio timeout, not the image one
    audio_fn_start = src.find("async def _fetch_audio_bytes_and_mime(")
    audio_fn_body = src[audio_fn_start : audio_fn_start + 2000]
    assert "_AUDIO_DOWNLOAD_TIMEOUT" in audio_fn_body
    assert "_IMAGE_DOWNLOAD_TIMEOUT" not in audio_fn_body


def test_gemini_transcribe_calls_audio_helper_not_image_helper():
    """The actual fix: gemini.transcribe must CALL the audio helper.
    If a future refactor re-points it at the image helper, the bug
    returns silently — this test catches that.

    We look at the active call line specifically (with `await`) so the
    pre-fix mention in the WHY-comment doesn't trigger a false positive."""
    src = _read("app/services/llm_clients/gemini.py")
    transcribe_start = src.find("async def transcribe(")
    assert transcribe_start > 0, "gemini.transcribe not found"
    transcribe_body = src[transcribe_start : transcribe_start + 3000]
    # The active call must be the audio helper
    assert "await _fetch_audio_bytes_and_mime(" in transcribe_body
    # And the active image-helper call must be GONE (comments OK)
    assert "await _fetch_image_bytes_and_mime(" not in transcribe_body


def test_image_helper_unchanged_for_image_call_sites():
    """Image fetcher must keep image defaults — the multimodal path in
    gemini.complete still calls _fetch_image_bytes_and_mime for image
    attachments. Pin that the image helper still ships with image
    defaults (image/jpeg, _MAX_IMAGE_BYTES)."""
    src = _read("app/services/ai_client.py")
    image_fn_start = src.find("async def _fetch_image_bytes_and_mime(")
    image_fn_body = src[image_fn_start : image_fn_start + 2000]
    assert '"image/jpeg"' in image_fn_body
    assert "_MAX_IMAGE_BYTES" in image_fn_body
    assert 'mime.startswith("image/")' in image_fn_body
