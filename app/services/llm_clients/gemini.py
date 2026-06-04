"""Phase 25.3 — Gemini native-API client.

Symmetric counterpart to openai_compatible.py — same complete() /
complete_stream() interface, OpenAI messages-list input, LlmResponse
output. Internally translates OpenAI messages → Gemini's
`contents` + `systemInstruction` shape.

Why a separate client instead of running Gemini through
openai_compatible.py: Gemini's native API has materially different
wire format (parts arrays, candidates, finishReason / blockReason
classifications, generationConfig sub-object). Adapting at the client
level is cleaner than at the registry level — registry stays
provider-agnostic.

Phase 25.10 (2026-06-03): the legacy `ai_client._call_gemini`,
`_stream_gemini`, and `_build_gemini_contents` were 0-caller post-25.3b
and have been removed. All Gemini traffic now flows through this module.
"""

import logging
import time
from typing import AsyncIterator

import httpx

from services.llm_types import LlmBlockedError, LlmResponse

logger = logging.getLogger(__name__)

GEMINI_NATIVE_URL = "https://generativelanguage.googleapis.com/v1beta"


async def _messages_to_gemini_contents(
    messages: list[dict],
) -> tuple[list[dict], dict | None]:
    """Convert OpenAI messages → (contents, system_instruction) for Gemini.

    OpenAI shape:
      [{"role": "system|user|assistant", "content": "..."}]  # string
      [{"role": "user", "content": [
          {"type": "text", "text": "..."},
          {"type": "image_url", "image_url": {"url": "..."}},
      ]}]  # multimodal content list

    Gemini shape:
      contents: [{"role": "user|model", "parts": [{"text": "..."}]}]
      systemInstruction: {"parts": [{"text": "..."}]}  (separate, not in contents)

    Notes:
      - Gemini uses "model" where OpenAI uses "assistant" — rename.
      - System messages get hoisted out of contents into the dedicated
        systemInstruction field. Multiple system messages concatenate.
      - Multimodal (image_url) content is fetched+base64-encoded into
        Gemini's inlineData format. Image fetch is lazy-imported from
        ai_client to avoid a circular import; that helper already has
        size-limit + SSRF defenses.
    """
    import asyncio

    system_parts: list[str] = []
    contents: list[dict] = []
    image_tasks: list[tuple[int, int, object]] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")
        if role == "system":
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue
        gemini_role = "model" if role == "assistant" else "user"

        parts: list[dict | None] = []
        if isinstance(content, str):
            if content:
                parts.append({"text": content})
        elif isinstance(content, list):
            # OpenAI multimodal content array
            from services.ai_client import _fetch_and_encode_image

            for item in content:
                if not isinstance(item, dict):
                    continue
                t = item.get("type")
                if t == "text":
                    text = item.get("text") or ""
                    if text:
                        parts.append({"text": text})
                elif t == "image_url":
                    url_obj = item.get("image_url") or {}
                    url = url_obj.get("url") if isinstance(url_obj, dict) else url_obj
                    if not url:
                        continue
                    # ai_client._fetch_and_encode_image_openai already fetches +
                    # base64-encodes mobile-sent storage_keys into a "data:..."
                    # URL for the OpenAI wire format. Detect that shape and
                    # parse it directly into Gemini's inlineData — re-fetching
                    # the data URL was the 2026-06-03 bug (fetch tried to
                    # presign "data:image/jpeg;base64,..." as a storage key
                    # → 404 → image silently became "[image attachment —
                    # failed to load]" and the assistant replied "I can't
                    # see it.").
                    if url.startswith("data:"):
                        try:
                            header, b64 = url.split(",", 1)
                            mime = header[len("data:") :].split(";")[0] or "image/jpeg"
                            parts.append(
                                {"inlineData": {"mimeType": mime, "data": b64}}
                            )
                        except (ValueError, IndexError):
                            parts.append(
                                {"text": "[image attachment — malformed data URL]"}
                            )
                    else:
                        # Raw HTTPS URL or storage_key — fetch via helper which
                        # handles presigning + size cap. Kept for forward
                        # compatibility if _build_user_content ever stops
                        # pre-encoding.
                        placeholder_idx = len(parts)
                        parts.append(None)
                        image_tasks.append(
                            (
                                len(contents),
                                placeholder_idx,
                                _fetch_and_encode_image(url),
                            )
                        )
                else:
                    # Phase 25.10 follow-up — surface dropped content
                    # types (e.g. OpenAI `input_audio` or future
                    # `tool_use` items the gemini client doesn't know
                    # how to translate). Previously silent, which
                    # masked the "user sent audio-in-messages and
                    # Gemini saw nothing" failure mode. Latent today
                    # because the chat hot path doesn't emit anything
                    # other than text/image_url, but the next feature
                    # that does will get a Sentry breadcrumb instead
                    # of a silent drop.
                    logger.warning(
                        "gemini: dropping unknown content item type=%r (keys=%s)",
                        t,
                        list(item.keys()),
                    )

        if parts:
            contents.append({"role": gemini_role, "parts": parts})

    if image_tasks:
        coroutines = [task[2] for task in image_tasks]
        results = await asyncio.gather(*coroutines, return_exceptions=True)
        for (content_idx, part_idx, _), result in zip(image_tasks, results):
            if isinstance(result, Exception):
                contents[content_idx]["parts"][part_idx] = {
                    "text": "[image — failed to load]"
                }
            else:
                contents[content_idx]["parts"][part_idx] = result
        for entry in contents:
            entry["parts"] = [p for p in entry["parts"] if p is not None]

    system_instruction = (
        {"parts": [{"text": "\n\n".join(system_parts)}]} if system_parts else None
    )
    return contents, system_instruction


async def complete(
    *,
    provider: str,
    base_url: str | None,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra_body: dict | None = None,
    timeout: float = 60.0,
    max_retries: int = 3,
) -> LlmResponse:
    """Single-shot Gemini completion. Signature mirrors
    openai_compatible.complete() so the registry can dispatch by
    provider with no special-casing in the call() body.

    `base_url` and `extra_body` are accepted for interface symmetry but
    Gemini's native API uses fixed URL + payload shape — base_url is
    ignored, extra_body is merged into generationConfig if present.
    """
    contents, system_instruction = await _messages_to_gemini_contents(messages)

    payload: dict = {"contents": contents}
    gen_config: dict = {}
    if temperature is not None:
        gen_config["temperature"] = temperature
    if max_tokens is not None:
        gen_config["maxOutputTokens"] = max_tokens
    if gen_config:
        payload["generationConfig"] = gen_config
    if system_instruction:
        payload["systemInstruction"] = system_instruction
    # extra_body is a top-level merge (symmetric to openai_compatible).
    # For Gemini that means callers can pass things like
    # `extra_body={"safetySettings": [...]}` to tune content filters.
    if extra_body:
        payload.update(extra_body)

    url = f"{GEMINI_NATIVE_URL}/models/{model}:generateContent"
    started = time.monotonic()

    # Retry on transient errors (5xx, network). Same shape as
    # openai_compatible — exponential backoff + jitter.
    import asyncio
    import random

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                response = await http.post(
                    url, json=payload, params={"key": api_key}, timeout=timeout
                )
            if response.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"gemini upstream {response.status_code}",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()
            data = response.json()
            break
        except (httpx.HTTPStatusError, httpx.RequestError, asyncio.TimeoutError) as e:
            last_error = e
            if attempt < max_retries - 1:
                backoff = (0.2 * (2**attempt)) + random.uniform(-0.05, 0.05)
                logger.warning(
                    "gemini.complete retry %d/%d: %s", attempt + 1, max_retries, e
                )
                await asyncio.sleep(max(backoff, 0.05))
                continue
            raise
    else:
        # Loop exhausted without break — re-raise the last error.
        assert last_error is not None
        raise last_error

    candidates = data.get("candidates", [])
    if not candidates:
        feedback = data.get("promptFeedback") or {}
        block_reason = feedback.get("blockReason", "UNKNOWN")
        raise LlmBlockedError(f"blockReason={block_reason}")

    parts = candidates[0].get("content", {}).get("parts", []) or []
    text = "".join(p.get("text", "") for p in parts).strip()

    if not text:
        finish_reason = candidates[0].get("finishReason", "UNKNOWN")
        if finish_reason in {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"}:
            raise LlmBlockedError(f"finishReason={finish_reason}")
        raise ValueError(
            f"Gemini returned candidate with no text (finishReason={finish_reason})"
        )

    usage = data.get("usageMetadata", {}) or {}
    return LlmResponse(
        content=text,
        provider=provider,
        model=model,
        input_tokens=int(usage.get("promptTokenCount") or 0),
        output_tokens=int(usage.get("candidatesTokenCount") or 0),
        latency_ms=(time.monotonic() - started) * 1000.0,
    )


async def complete_stream(
    *,
    provider: str,
    base_url: str | None,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra_body: dict | None = None,
    timeout: float = 60.0,
) -> AsyncIterator[tuple[str, str]]:
    """Streaming Gemini completion. Yields the same ('delta'|'usage'|'done', value)
    contract as openai_compatible.complete_stream so the consumer doesn't
    have to branch on provider.

    Streaming intentionally does NOT retry — consumer is mid-stream.
    """
    import json

    contents, system_instruction = await _messages_to_gemini_contents(messages)
    payload: dict = {"contents": contents}
    gen_config: dict = {}
    if temperature is not None:
        gen_config["temperature"] = temperature
    if max_tokens is not None:
        gen_config["maxOutputTokens"] = max_tokens
    if gen_config:
        payload["generationConfig"] = gen_config
    if system_instruction:
        payload["systemInstruction"] = system_instruction
    if extra_body:
        payload.update(extra_body)

    url = f"{GEMINI_NATIVE_URL}/models/{model}:streamGenerateContent"

    async with httpx.AsyncClient(timeout=timeout) as http:
        async with http.stream(
            "POST", url, json=payload, params={"key": api_key, "alt": "sse"}
        ) as response:
            response.raise_for_status()
            final_usage: dict | None = None
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                raw = line[len("data: ") :].strip()
                if not raw:
                    continue
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                cands = chunk.get("candidates") or []
                if cands:
                    parts = cands[0].get("content", {}).get("parts", []) or []
                    text = "".join(p.get("text", "") for p in parts)
                    if text:
                        yield ("delta", text)

                usage = chunk.get("usageMetadata")
                if usage:
                    final_usage = usage

            if final_usage:
                yield ("usage", json.dumps(final_usage))
            yield ("done", "")


# ─── Audio modality (25.3b) ───────────────────────────────────────────────


async def transcribe(
    *,
    provider: str,
    base_url: str | None,
    api_key: str,
    model: str,
    audio_url: str,
    timeout: float = 60.0,
) -> LlmResponse:
    """Gemini audio transcription via the native generateContent endpoint
    with audio inline content. Different request shape from chat — audio
    bytes go in the user message parts as `inlineData`. Returns a
    LlmResponse where `content` is the transcript text.

    Distinct from complete() because the request shape differs and the
    registry exposes a separate capability flag (`supports_transcribe`)
    so admin routing can show which providers handle which modality.
    """
    started = time.monotonic()

    # Fetch + base64-encode audio. Uses the audio-shaped fetcher (forked
    # from the image one) so the default MIME is audio/mp4 and the size
    # cap is config.MAX_AUDIO_SIZE_BYTES (20 MB) instead of the image
    # 5 MB. Pre-fix: this called _fetch_image_bytes_and_mime which
    # defaulted to image/jpeg → Gemini rejected audio-as-image → returned
    # no candidates → mobile saw "transcription unavailable."
    from services.ai_client import _fetch_audio_bytes_and_mime

    mime, audio_bytes = await _fetch_audio_bytes_and_mime(audio_url)
    if not mime or not isinstance(audio_bytes, bytes):
        raise RuntimeError(f"gemini.transcribe: failed to fetch audio at {audio_url}")

    import base64

    encoded = base64.b64encode(audio_bytes).decode("ascii")

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": "Transcribe this audio. Return only the transcription."},
                    {"inlineData": {"mimeType": mime, "data": encoded}},
                ],
            }
        ]
    }
    url = f"{GEMINI_NATIVE_URL}/models/{model}:generateContent"

    async with httpx.AsyncClient(timeout=timeout) as http:
        response = await http.post(
            url, json=payload, params={"key": api_key}, timeout=timeout
        )
        response.raise_for_status()
        data = response.json()

    candidates = data.get("candidates") or []
    if not candidates:
        raise LlmBlockedError("transcription returned no candidates")
    parts = candidates[0].get("content", {}).get("parts", []) or []
    text = "".join(p.get("text", "") for p in parts).strip()
    usage = data.get("usageMetadata") or {}

    return LlmResponse(
        content=text,
        provider=provider,
        model=model,
        input_tokens=int(usage.get("promptTokenCount") or 0),
        output_tokens=int(usage.get("candidatesTokenCount") or 0),
        latency_ms=(time.monotonic() - started) * 1000.0,
    )
