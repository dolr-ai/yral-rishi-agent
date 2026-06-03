"""Image multimodal — data: URL handling in gemini._messages_to_gemini_contents.

The 2026-06-03 bug: ai_client._fetch_and_encode_image_openai already
fetches + base64-encodes the image into a 'data:image/...;base64,...'
URL inside the OpenAI multimodal content array. gemini._messages_to_gemini_contents
was treating that URL as a fetchable resource and trying to
storage.generate_presigned_url(...) it — predictably 404'd → image
silently replaced with '[image attachment — failed to load]' → Gemini
replied "I can't see it." Symptom matched mobile expert's brief
verbatim. Third 25.3b extraction trailing-edge (after audio SSRF order
+ audio MIME defaults).

Fix: detect data: URLs and parse directly into Gemini's inlineData
shape. Forward-compat: non-data: URLs still go through the fetch
helper, so future refactors that emit raw storage_keys / HTTPS URLs
also work.
"""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


def test_data_url_is_parsed_directly_not_refetched():
    """The fix: detect data:image/... URLs and emit inlineData directly,
    no fetch. Re-fetching the data URL was the bug — it'd get presigned
    as a storage_key and 404."""
    src = _read("app/services/llm_clients/gemini.py")
    fn_start = src.find("async def _messages_to_gemini_contents(")
    fn_body = src[fn_start : fn_start + 6000]
    # The data: branch must exist
    assert 'url.startswith("data:")' in fn_body
    # And must produce inlineData directly (no _fetch_and_encode_image call
    # in that branch)
    data_branch = fn_body[fn_body.find('url.startswith("data:")') :]
    next_else_or_end = data_branch.find("else:")
    if next_else_or_end > 0:
        data_branch = data_branch[:next_else_or_end]
    assert "inlineData" in data_branch
    assert "_fetch_and_encode_image" not in data_branch


def test_non_data_url_still_fetches():
    """Forward compat: if _build_user_content ever stops pre-encoding,
    raw URLs / storage_keys must still work via the fetch path."""
    src = _read("app/services/llm_clients/gemini.py")
    fn_start = src.find("async def _messages_to_gemini_contents(")
    fn_body = src[fn_start : fn_start + 6000]
    # The else branch must still call _fetch_and_encode_image
    assert "_fetch_and_encode_image(url)" in fn_body
    # And the comment / structure must distinguish the two paths
    assert "data:" in fn_body


def test_malformed_data_url_falls_back_to_text_placeholder():
    """Defensive: if a malformed data URL (no comma, no base64 body)
    sneaks in, we emit a text placeholder rather than crash. The bug
    was silent image loss; the fix shouldn't introduce noisy crashes."""
    src = _read("app/services/llm_clients/gemini.py")
    fn_start = src.find("async def _messages_to_gemini_contents(")
    fn_body = src[fn_start : fn_start + 6000]
    assert "except (ValueError, IndexError)" in fn_body
    assert "malformed data URL" in fn_body


def test_mime_default_for_data_url_is_image_jpeg():
    """Most callers pass data:image/jpeg;base64,...  but if the mime
    chunk is missing (data:;base64,...) we default to image/jpeg
    rather than emit an empty mimeType to Gemini."""
    src = _read("app/services/llm_clients/gemini.py")
    fn_start = src.find("async def _messages_to_gemini_contents(")
    fn_body = src[fn_start : fn_start + 6000]
    assert 'or "image/jpeg"' in fn_body
