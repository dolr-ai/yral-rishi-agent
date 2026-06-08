import json
import base64
import logging
import time

import httpx

import config
from services import langfuse_tracing

# Phase 25.3b: types moved to llm_types.py to break the ai_client →
# registry → gemini → ai_client circular import. Re-exported here so
# existing chat.py imports (`ai_client.ERROR_MESSAGES`,
# `ai_client.LlmResponse`, etc.) keep working unchanged.
from services.llm_types import (
    ERROR_MESSAGES,
    LlmBlockedError,
    LlmResponse,
    RETRYABLE_CODES,
)

__all__ = ["ERROR_MESSAGES", "LlmBlockedError", "LlmResponse", "RETRYABLE_CODES"]

logger = logging.getLogger(__name__)

GEMINI_NATIVE_URL = "https://generativelanguage.googleapis.com/v1beta"


_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_IMAGE_DOWNLOAD_TIMEOUT = 5.0


# Phase 25.10 (2026-06-03): removed orphan helpers — get_openrouter_client,
# _call_gemini, _stream_gemini, _build_gemini_contents, extract_memories,
# MEMORY_EXTRACTION_PROMPT. All 0-caller after Phase 25.3b extraction.
# NSFW path now flows through llm_registry.call(process="user_chat_main_nsfw")
# → openai_compatible client. Memory extraction via memory.py:extract_and_store
# → llm_registry.call(process="memory_extraction") with full cost + outcome
# tracking. See feedback_list_vs_detail_endpoint_gap.md.


async def _fetch_image_bytes_and_mime(url: str) -> tuple[str, bytes] | tuple[None, str]:
    if not (url.startswith("http://") or url.startswith("https://")):
        from services import storage as _storage

        presigned = _storage.generate_presigned_url(url)
        if not presigned:
            return (None, "missing")
        url = presigned

    try:
        async with httpx.AsyncClient(
            timeout=_IMAGE_DOWNLOAD_TIMEOUT, follow_redirects=True
        ) as http:
            resp = await http.get(url)
            resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Image fetch failed for {url[:80]}: {e}")
        return (None, "failed to load")

    data = resp.content
    if len(data) > _MAX_IMAGE_BYTES:
        return (None, "too large")
    if not data:
        return (None, "empty")

    mime = (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
    if not mime.startswith("image/"):
        mime = "image/jpeg"

    return (mime, data)


# Audio download timeout — longer than image since voice notes are ~MB
# while images are typically ~100KB. Caller (gemini.transcribe) has its
# own per-process timeout via the registry; this is just the HTTP fetch.
_AUDIO_DOWNLOAD_TIMEOUT = 30.0


async def _fetch_audio_bytes_and_mime(
    url: str,
) -> tuple[str, bytes] | tuple[None, str]:
    """Audio-shaped counterpart to _fetch_image_bytes_and_mime.

    Lifted from the image helper but with audio-appropriate defaults:
      - default MIME 'audio/mp4' (matches what mobile MediaRecorder /
        AVAudioRecorder writes, and what the upload route stores in S3)
      - max bytes from config.MAX_AUDIO_SIZE_BYTES (20 MB) — NOT the
        image cap (5 MB) which would silently fail voice notes > 5 MB
      - mime-prefix sanity check is 'audio/' not 'image/'

    Phase 25.3b extraction reused _fetch_image_bytes_and_mime for audio
    by accident — the image defaults caused Gemini to receive audio
    bytes labeled image/jpeg → empty candidates → LlmBlockedError → mobile
    "[Audio message - transcription unavailable]". Forked per Rishi's
    Option B for CLAUDE.md rule 1 symmetry — two parallel helpers, each
    self-documenting at the call site. No risk of accidental cross-use.
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        from services import storage as _storage

        presigned = _storage.generate_presigned_url(url)
        if not presigned:
            return (None, "missing")
        url = presigned

    try:
        async with httpx.AsyncClient(
            timeout=_AUDIO_DOWNLOAD_TIMEOUT, follow_redirects=True
        ) as http:
            resp = await http.get(url)
            resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Audio fetch failed for {url[:80]}: {e}")
        return (None, "failed to load")

    data = resp.content
    if len(data) > config.MAX_AUDIO_SIZE_BYTES:
        return (None, "too large")
    if not data:
        return (None, "empty")

    mime = (resp.headers.get("content-type") or "audio/mp4").split(";")[0].strip()
    if not mime.startswith("audio/"):
        # Storj sometimes returns application/octet-stream for m4a. Force
        # to audio/mp4 — what mobile MediaRecorder produces today.
        mime = "audio/mp4"

    return (mime, data)


async def _fetch_and_encode_image(url: str) -> dict:
    mime, data = await _fetch_image_bytes_and_mime(url)
    if mime is None:
        return {"text": f"[image attachment — {data}]"}
    return {
        "inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}
    }


async def _fetch_and_encode_image_openai(url: str) -> dict:
    mime, data = await _fetch_image_bytes_and_mime(url)
    if mime is None:
        return {"type": "text", "text": f"[image attachment — {data}]"}
    b64 = base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


async def _build_user_content(
    text: str | None, media_urls: list[str] | None
) -> str | list:
    if not media_urls:
        return text or ""
    parts = []
    if text:
        parts.append({"type": "text", "text": text})
    for url in media_urls[:5]:
        parts.append(await _fetch_and_encode_image_openai(url))
    return parts if parts else (text or "")


async def generate_response_stream(
    system_instructions: str,
    conversation_history: list[dict],
    user_message: str,
    is_nsfw: bool = False,
    media_urls: list[str] | None = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
    archetype: str | None = None,
):
    """Phase 2.7: streaming counterpart to generate_response.

    Yields ('text', chunk_str) tuples as tokens arrive. Final tuple is
    ('done', LlmResponse) carrying the full text + metadata so the caller
    can persist it. On policy block, yields ('error', LlmResponse) with
    error_code=BLOCKED_CONTENT (same shape as the non-streaming path).
    On other errors, yields ('error', LlmResponse) with error_code=TRANSIENT.

    NSFW influencers are NOT streamed today (OpenRouter SDK streaming would
    require a separate code path). The route falls back to the non-streaming
    endpoint for is_nsfw=True conversations.
    """
    # NSFW streaming intentionally not supported — yield NO_PROVIDER so
    # the route falls back to non-streaming generate_response (which
    # handles NSFW via user_chat_main_nsfw / OpenRouter).
    if is_nsfw:
        yield (
            "error",
            LlmResponse(
                content=ERROR_MESSAGES["NO_PROVIDER"],
                provider="none",
                model="none",
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
                is_fallback=True,
                error_code="NO_PROVIDER",
            ),
        )
        return

    from services import llm_registry
    from services.soul_file import tuning_for

    tuning = tuning_for(archetype) or {}
    temperature = tuning.get("temperature", config.GEMINI_TEMPERATURE)
    max_tokens = tuning.get("max_tokens", config.GEMINI_MAX_TOKENS)

    t0 = time.monotonic()
    total_text = ""
    token_count = 0

    try:
        messages = await _build_chat_messages(
            system_instructions, conversation_history, user_message, media_urls
        )
        # Phase 21αβ.H12 — vision-bearing chat routes via the dedicated
        # multimodal process so flipping user_chat_main to a text-only
        # provider (e.g. runpod_vllm for cost) doesn't silently break
        # image chats. Detection is post-build: the helper inspects the
        # actual outgoing payload, not the upstream media_urls signal.
        process = (
            "user_chat_main_multimodal"
            if llm_registry.has_image_content(messages)
            else "user_chat_main"
        )

        # Resolve provider/model AFTER process selection so Langfuse
        # tracing reflects what actually served the request.
        routed = llm_registry.current_config(process)
        final_provider = routed["provider"]
        final_model = routed["model"]

        async for kind, value in llm_registry.call_stream(
            process=process,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            if kind == "delta":
                total_text += value
                yield ("text", value)
            elif kind == "usage":
                import json as _json

                try:
                    usage = _json.loads(value)
                    token_count = int(
                        usage.get("candidatesTokenCount")
                        or usage.get("completion_tokens")
                        or 0
                    )
                except (ValueError, TypeError):
                    pass
            # 'done' marks end-of-stream; we emit our own ('done', LlmResponse) below

        latency_ms = (time.monotonic() - t0) * 1000
        langfuse_tracing.trace_generation(
            trace_name="chat-response-stream",
            user_id=user_id,
            model=final_model,
            provider=final_provider,
            input_text=user_message,
            output_text=total_text,
            input_tokens=0,
            output_tokens=token_count,
            latency_ms=latency_ms,
            conversation_id=conversation_id,
        )
        yield (
            "done",
            LlmResponse(
                content=total_text,
                provider=final_provider,
                model=final_model,
                input_tokens=0,
                output_tokens=token_count,
                latency_ms=latency_ms,
            ),
        )
    except LlmBlockedError as e:
        logger.warning(f"LLM stream blocked: {e.reason}")
        yield (
            "error",
            LlmResponse(
                content=ERROR_MESSAGES["BLOCKED_CONTENT"],
                provider=final_provider,
                model=final_model,
                input_tokens=0,
                output_tokens=0,
                latency_ms=(time.monotonic() - t0) * 1000,
                is_fallback=True,
                error_code="BLOCKED_CONTENT",
            ),
        )
    except RuntimeError as e:
        logger.error(f"LLM stream provider unavailable: {e}")
        yield (
            "error",
            LlmResponse(
                content=ERROR_MESSAGES["NO_PROVIDER"],
                provider="none",
                model="none",
                input_tokens=0,
                output_tokens=0,
                latency_ms=(time.monotonic() - t0) * 1000,
                is_fallback=True,
                error_code="NO_PROVIDER",
            ),
        )
    except Exception as e:
        logger.error(f"LLM stream failed: {e}")
        yield (
            "error",
            LlmResponse(
                content=ERROR_MESSAGES["TRANSIENT"],
                provider=final_provider,
                model=final_model,
                input_tokens=0,
                output_tokens=0,
                latency_ms=(time.monotonic() - t0) * 1000,
                is_fallback=True,
                error_code="TRANSIENT",
            ),
        )


async def _build_chat_messages(
    system_instructions: str,
    conversation_history: list[dict],
    user_message: str,
    media_urls: list[str] | None,
) -> list[dict]:
    """Build OpenAI messages-list from chat orchestration inputs.
    Multimodal content (image attachments) emits OpenAI content arrays;
    gemini._messages_to_gemini_contents knows how to translate them."""
    messages: list[dict] = [{"role": "system", "content": system_instructions or ""}]
    for msg in conversation_history:
        role = msg.get("role", "user")
        content = msg.get("content") or ""
        if role == "user":
            msg_media = msg.get("media_urls")
            if isinstance(msg_media, str):
                try:
                    msg_media = json.loads(msg_media)
                except (json.JSONDecodeError, TypeError):
                    msg_media = None
            messages.append(
                {
                    "role": "user",
                    "content": await _build_user_content(content, msg_media),
                }
            )
        else:
            messages.append({"role": "assistant", "content": content})
    messages.append(
        {"role": "user", "content": await _build_user_content(user_message, media_urls)}
    )
    return messages


async def generate_response(
    system_instructions: str,
    conversation_history: list[dict],
    user_message: str,
    is_nsfw: bool = False,
    media_urls: list[str] | None = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
    archetype: str | None = None,
) -> LlmResponse:
    """Chat orchestration shim — builds messages, routes through registry.

    25.3b: the bulk of the legacy 237-line implementation moved into
    llm_registry.call() + gemini.py / openai_compatible.py. This function
    is now an orchestrator that handles archetype tuning, NSFW routing,
    Langfuse tracing, and error mapping — leaving dispatch + retries +
    concurrency capping to the registry.
    """
    from services import llm_registry
    from services.soul_file import tuning_for

    _tuning = tuning_for(archetype) or {}
    _temperature = _tuning.get("temperature", config.GEMINI_TEMPERATURE)
    _max_tokens = _tuning.get("max_tokens", config.GEMINI_MAX_TOKENS)

    t0 = time.monotonic()

    try:
        messages = await _build_chat_messages(
            system_instructions, conversation_history, user_message, media_urls
        )
        # Phase 21αβ.H12 — route vision-bearing chat via the dedicated
        # multimodal process (text-only providers like runpod_vllm would
        # silently drop images). NSFW + vision is not supported today —
        # NSFW takes precedence, mirroring the pre-H12 behavior. If a
        # future product call needs NSFW+vision, add user_chat_main_nsfw_multimodal.
        if is_nsfw:
            process = "user_chat_main_nsfw"
        elif llm_registry.has_image_content(messages):
            process = "user_chat_main_multimodal"
        else:
            process = "user_chat_main"
        result = await llm_registry.call(
            process=process,
            messages=messages,
            temperature=_temperature,
            max_tokens=_max_tokens,
        )
        langfuse_tracing.trace_generation(
            trace_name="chat-response",
            user_id=user_id,
            model=result.model,
            provider=result.provider,
            input_text=user_message,
            output_text=result.content,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            conversation_id=conversation_id,
        )
        return result
    except LlmBlockedError as e:
        logger.warning(f"LLM blocked the response: {e.reason}")
        elapsed = (time.monotonic() - t0) * 1000
        langfuse_tracing.trace_generation(
            trace_name="chat-response",
            user_id=user_id,
            model=config.GEMINI_MODEL,
            provider="gemini",
            input_text=user_message,
            output_text=f"BLOCKED: {e.reason}",
            latency_ms=elapsed,
            is_error=True,
            conversation_id=conversation_id,
        )
        return LlmResponse(
            content=ERROR_MESSAGES["BLOCKED_CONTENT"],
            provider="gemini",
            model=config.GEMINI_MODEL,
            input_tokens=0,
            output_tokens=0,
            latency_ms=elapsed,
            is_fallback=True,
            error_code="BLOCKED_CONTENT",
        )
    except RuntimeError as e:
        # Provider misconfiguration (no API key, unsupported capability).
        # Surface as NO_PROVIDER so mobile shows the right error UX.
        logger.error(f"LLM provider unavailable for {process}: {e}")
        return LlmResponse(
            content=ERROR_MESSAGES["NO_PROVIDER"],
            provider="none",
            model="none",
            input_tokens=0,
            output_tokens=0,
            latency_ms=(time.monotonic() - t0) * 1000,
            is_fallback=True,
            error_code="NO_PROVIDER",
        )
    except Exception as e:
        logger.error(f"LLM generation failed for {process}: {e}")
        elapsed = (time.monotonic() - t0) * 1000
        langfuse_tracing.trace_generation(
            trace_name="chat-response",
            user_id=user_id,
            model=config.GEMINI_MODEL,
            provider="gemini",
            input_text=user_message,
            output_text=str(e),
            latency_ms=elapsed,
            is_error=True,
            conversation_id=conversation_id,
        )
        return LlmResponse(
            content=ERROR_MESSAGES["TRANSIENT"],
            provider="gemini",
            model=config.GEMINI_MODEL,
            input_tokens=0,
            output_tokens=0,
            latency_ms=elapsed,
            is_fallback=True,
            error_code="TRANSIENT",
        )


def _is_safe_url(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        if not host:
            return False
        blocked_prefixes = (
            "127.",
            "10.",
            "192.168.",
            "0.",
            "169.254.",
            "172.16.",
            "172.17.",
            "172.18.",
            "172.19.",
            "172.20.",
            "172.21.",
            "172.22.",
            "172.23.",
            "172.24.",
            "172.25.",
            "172.26.",
            "172.27.",
            "172.28.",
            "172.29.",
            "172.30.",
            "172.31.",
        )
        if any(host.startswith(p) for p in blocked_prefixes):
            return False
        if host in ("localhost", "metadata.google.internal"):
            return False
        return True
    except Exception:
        return False


async def transcribe_audio(audio_url: str) -> str | None:
    """Thin shim around llm_registry.call_transcribe(process="audio_transcription").

    25.3b: the wire-level Gemini audio call moved to gemini.transcribe;
    routing + concurrency capping + provider selection move to the
    registry. This function just preserves the existing call-site
    contract (audio_url → text or None on failure).

    Bugfix 2026-06-03: mobile sends raw S3 storage_keys (e.g.
    "chat-audio/abc.mp3") for the new mic-recording feature. The SSRF
    safety check was firing first and rejecting non-http URLs — so
    every audio message landed at transcribe → None → mobile shows
    "transcription unavailable". Mirror the resolution pattern from
    chat._format_message:48-50: presign storage_key → HTTPS URL FIRST,
    then run the SSRF check on the resolved URL.
    """
    if audio_url and not audio_url.startswith("http"):
        from services import storage

        audio_url = storage.generate_presigned_url(audio_url)
    if not audio_url or not _is_safe_url(audio_url):
        logger.error(f"Audio URL blocked (SSRF protection): {(audio_url or '')[:50]}")
        return None
    from services import llm_registry

    try:
        result = await llm_registry.call_transcribe(
            process="audio_transcription", audio_url=audio_url
        )
        return result.content.strip() if result.content else None
    except Exception as e:
        logger.error(f"Audio transcription failed: {e}")
        return None
