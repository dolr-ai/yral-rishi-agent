"""Audio transcribe — storage_key → presigned-URL resolution.

Mobile sends raw S3 storage_keys (e.g. 'chat-audio/abc.mp3') for the
mic-recording feature. Pre-fix, the SSRF safety check rejected anything
that didn't start with http/https, so every audio message landed at
transcribe_audio → returned None → mobile showed "transcription
unavailable" and AI replied "I can't process audio." 2026-06-03 fix:
presign first, then SSRF-check the resolved URL.
"""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


def test_transcribe_audio_presigns_storage_key_before_ssrf_check():
    """The presign step must come BEFORE _is_safe_url. If the order is
    reversed (the pre-fix bug), storage_keys are rejected immediately."""
    src = _read("app/services/ai_client.py")

    # Locate transcribe_audio's body
    fn_start = src.find("async def transcribe_audio(")
    assert fn_start > 0, "transcribe_audio not found"
    # Take a generous window of the function body
    fn_body = src[fn_start : fn_start + 2000]

    # The startswith("http") check must come BEFORE _is_safe_url
    presign_pos = fn_body.find('not audio_url.startswith("http")')
    safety_pos = fn_body.find("_is_safe_url(")
    assert presign_pos > 0, "presign check (audio_url.startswith http) missing"
    assert safety_pos > 0, "_is_safe_url call missing"
    assert presign_pos < safety_pos, (
        "presign must precede _is_safe_url — otherwise storage_keys are "
        "rejected before they can be resolved (the pre-fix bug)"
    )


def test_transcribe_audio_uses_storage_presigned_url():
    """The resolution path must call storage.generate_presigned_url to
    convert storage_key → fetchable HTTPS URL. Same helper used in
    chat._format_message:48-50."""
    src = _read("app/services/ai_client.py")
    fn_start = src.find("async def transcribe_audio(")
    fn_body = src[fn_start : fn_start + 2000]
    assert "storage.generate_presigned_url" in fn_body


def test_transcribe_audio_handles_failed_presign_gracefully():
    """If generate_presigned_url returns None (storage key invalid or
    storage misconfigured), the function must return None rather than
    crashing on a None.startswith() / _is_safe_url(None) call."""
    src = _read("app/services/ai_client.py")
    fn_start = src.find("async def transcribe_audio(")
    fn_body = src[fn_start : fn_start + 2000]
    # The guard "if not audio_url or not _is_safe_url(audio_url)" is
    # what handles the failed-presign case.
    assert "not audio_url or not _is_safe_url(audio_url)" in fn_body


def test_format_message_pattern_still_present():
    """Sanity: the chat._format_message:48-50 pattern we mirrored must
    still exist. If it's refactored, this test points the refactorer at
    transcribe_audio for parallel update."""
    src = _read("app/routes/chat.py")
    assert "audio_url" in src
    assert 'not audio_url.startswith("http")' in src
    assert "storage.generate_presigned_url(audio_url)" in src
