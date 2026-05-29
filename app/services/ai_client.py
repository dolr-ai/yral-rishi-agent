import json
import base64
import logging
import time
from dataclasses import dataclass

import httpx
from openai import AsyncOpenAI

import config
from services import langfuse_tracing

logger = logging.getLogger(__name__)

GEMINI_NATIVE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Phase 3.8: tailored fallback text by failure class. Mobile reads
# LlmResponse.error_code to pick icon/color/retry button.
ERROR_MESSAGES = {
    "BLOCKED_CONTENT": "I can't reply to that — try asking me something else.",
    "TRANSIENT": "I'm having trouble connecting right now. Try again in a moment.",
    "NO_PROVIDER": "Chat is temporarily unavailable. Please try again later.",
}
RETRYABLE_CODES = {"TRANSIENT"}


class LlmBlockedError(Exception):
    """Gemini/OpenRouter refused to generate due to safety/policy."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class LlmResponse:
    content: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    is_fallback: bool = False
    error_code: str | None = None  # one of ERROR_MESSAGES keys, or None on success


_openrouter_client: AsyncOpenAI | None = None
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_IMAGE_DOWNLOAD_TIMEOUT = 5.0


def get_openrouter_client() -> AsyncOpenAI | None:
    global _openrouter_client
    if _openrouter_client is None:
        if not config.OPENROUTER_API_KEY:
            return None
        _openrouter_client = AsyncOpenAI(
            api_key=config.OPENROUTER_API_KEY,
            base_url=config.OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "https://yral.com",
                "X-Title": "Yral AI Chat",
            },
        )
    return _openrouter_client


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


async def _build_gemini_contents(
    system_instructions: str,
    conversation_history: list[dict],
    user_message: str,
    media_urls: list[str] | None = None,
) -> tuple[dict, list]:
    import asyncio

    system_instruction = {"parts": [{"text": system_instructions}]}

    history_len = len(conversation_history)
    window = config.IMAGE_HISTORY_WINDOW
    recent_start = max(0, history_len - window)

    contents = []
    image_tasks = []

    for i, msg in enumerate(conversation_history):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        gemini_role = "model" if role == "assistant" else "user"

        parts = []
        if content:
            parts.append({"text": content})

        if role == "user":
            msg_media = msg.get("media_urls")
            if isinstance(msg_media, str):
                try:
                    msg_media = json.loads(msg_media)
                except (json.JSONDecodeError, TypeError):
                    msg_media = None

            if msg_media:
                if i >= recent_start:
                    for url in msg_media[:5]:
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
                    parts.append(
                        {
                            "text": f"[User sent {len(msg_media)} image(s) — see AI's earlier response for description]"
                        }
                    )

        if parts:
            contents.append({"role": gemini_role, "parts": parts})

    user_parts = []
    if user_message:
        user_parts.append({"text": user_message})
    if media_urls:
        for url in media_urls[:5]:
            placeholder_idx = len(user_parts)
            user_parts.append(None)
            image_tasks.append(
                (len(contents), placeholder_idx, _fetch_and_encode_image(url))
            )
    if user_parts:
        contents.append({"role": "user", "parts": user_parts})

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

    return system_instruction, contents


async def _call_gemini(
    contents: list,
    system_instruction: dict | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    safety_settings: list[dict] | None = None,
) -> tuple[str, int]:
    url = f"{GEMINI_NATIVE_URL}/models/{config.GEMINI_MODEL}:generateContent"

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if system_instruction:
        payload["systemInstruction"] = system_instruction
    if safety_settings:
        payload["safetySettings"] = safety_settings

    async with httpx.AsyncClient(timeout=config.GEMINI_TIMEOUT) as http:
        response = await http.post(
            url,
            json=payload,
            params={"key": config.GEMINI_API_KEY},
            timeout=config.GEMINI_TIMEOUT,
        )
        response.raise_for_status()

    data = response.json()

    candidates = data.get("candidates", [])
    if not candidates:
        feedback = data.get("promptFeedback") or {}
        block_reason = feedback.get("blockReason", "UNKNOWN")
        # Phase 3.8: explicit block classification so mobile shows "rephrase" UX.
        raise LlmBlockedError(f"blockReason={block_reason}")

    parts = candidates[0].get("content", {}).get("parts", [])
    response_text = ""
    for part in parts:
        if "text" in part:
            response_text += part["text"]
    response_text = response_text.strip()

    if not response_text:
        finish_reason = candidates[0].get("finishReason", "UNKNOWN")
        # SAFETY / RECITATION / PROHIBITED_CONTENT all mean "we filtered this."
        # MAX_TOKENS / OTHER are also empty but not policy blocks; treat them as
        # transient since retry-with-shorter-context could plausibly work.
        if finish_reason in {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"}:
            raise LlmBlockedError(f"finishReason={finish_reason}")
        raise ValueError(
            f"Gemini returned candidate with no text (finishReason={finish_reason})"
        )

    usage = data.get("usageMetadata", {})
    token_count = usage.get("candidatesTokenCount", 0)
    if not token_count and response_text:
        token_count = int(len(response_text) / 4)

    return response_text, token_count


async def _stream_gemini(
    contents: list,
    system_instruction: dict | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
):
    """Phase 2.7 (SSE) — yields text chunks as they arrive from Gemini.

    Uses Gemini's :streamGenerateContent endpoint with alt=sse, which emits
    `data: {json}\\n\\n` lines per chunk. We parse each line and yield the
    text fragment. Final yield is a tuple ('__DONE__', token_count). On a
    safety/policy block, raises LlmBlockedError exactly like _call_gemini
    so the route's error handling shape stays consistent.
    """
    url = f"{GEMINI_NATIVE_URL}/models/{config.GEMINI_MODEL}:streamGenerateContent"
    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if system_instruction:
        payload["systemInstruction"] = system_instruction

    total_text = ""
    token_count = 0
    finish_reason: str | None = None
    async with httpx.AsyncClient(timeout=config.GEMINI_TIMEOUT) as http:
        async with http.stream(
            "POST",
            url,
            json=payload,
            params={"key": config.GEMINI_API_KEY, "alt": "sse"},
            timeout=config.GEMINI_TIMEOUT,
        ) as response:
            response.raise_for_status()
            async for raw_line in response.aiter_lines():
                if not raw_line or not raw_line.startswith("data:"):
                    continue
                data_str = raw_line[len("data:") :].strip()
                if not data_str:
                    continue
                try:
                    obj = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                # Capture block reasons surfaced in promptFeedback
                feedback = obj.get("promptFeedback") or {}
                if feedback.get("blockReason"):
                    raise LlmBlockedError(f"blockReason={feedback['blockReason']}")
                for candidate in obj.get("candidates", []) or []:
                    finish_reason = candidate.get("finishReason") or finish_reason
                    for part in candidate.get("content", {}).get("parts", []) or []:
                        chunk = part.get("text")
                        if chunk:
                            total_text += chunk
                            yield ("text", chunk)
                usage = obj.get("usageMetadata") or {}
                if usage.get("candidatesTokenCount"):
                    token_count = usage["candidatesTokenCount"]

    if not total_text.strip():
        if finish_reason in {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"}:
            raise LlmBlockedError(f"finishReason={finish_reason}")
        raise ValueError(
            f"Gemini stream returned no text (finishReason={finish_reason})"
        )

    if not token_count and total_text:
        token_count = int(len(total_text) / 4)
    yield ("done", {"text": total_text, "token_count": token_count})


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
    if is_nsfw or not config.GEMINI_API_KEY:
        # Caller will fall back to non-streaming path. Yielding an error here
        # lets the route surface a typed error rather than hanging.
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

    t0 = time.monotonic()
    system_instruction, contents = await _build_gemini_contents(
        system_instructions, conversation_history, user_message, media_urls
    )

    # Phase 12: per-archetype LLM tuning. Lookup is non-fatal — unknown
    # archetypes fall back to config defaults (current behavior).
    from services.soul_file import tuning_for

    tuning = tuning_for(archetype)
    temperature = (tuning or {}).get("temperature", config.GEMINI_TEMPERATURE)
    max_tokens = (tuning or {}).get("max_tokens", config.GEMINI_MAX_TOKENS)

    total_text = ""
    token_count = 0
    try:
        async for kind, value in _stream_gemini(
            contents=contents,
            system_instruction=system_instruction,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            if kind == "text":
                total_text += value
                yield ("text", value)
            elif kind == "done":
                token_count = value.get("token_count", 0)
                total_text = value.get("text", total_text)
        latency_ms = (time.monotonic() - t0) * 1000
        langfuse_tracing.trace_generation(
            trace_name="chat-response-stream",
            user_id=user_id,
            model=config.GEMINI_MODEL,
            provider="gemini",
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
                provider="gemini",
                model=config.GEMINI_MODEL,
                input_tokens=0,
                output_tokens=token_count,
                latency_ms=latency_ms,
            ),
        )
    except LlmBlockedError as e:
        logger.warning(f"Gemini stream blocked: {e.reason}")
        yield (
            "error",
            LlmResponse(
                content=ERROR_MESSAGES["BLOCKED_CONTENT"],
                provider="gemini",
                model=config.GEMINI_MODEL,
                input_tokens=0,
                output_tokens=0,
                latency_ms=(time.monotonic() - t0) * 1000,
                is_fallback=True,
                error_code="BLOCKED_CONTENT",
            ),
        )
    except Exception as e:
        logger.error(f"Gemini stream failed: {e}")
        yield (
            "error",
            LlmResponse(
                content=ERROR_MESSAGES["TRANSIENT"],
                provider="gemini",
                model=config.GEMINI_MODEL,
                input_tokens=0,
                output_tokens=0,
                latency_ms=(time.monotonic() - t0) * 1000,
                is_fallback=True,
                error_code="TRANSIENT",
            ),
        )


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
    # Phase 12: per-archetype LLM tuning. Lookup is non-fatal — unknown
    # archetypes fall back to config defaults.
    from services.soul_file import tuning_for

    _tuning = tuning_for(archetype)
    _temperature = (_tuning or {}).get("temperature", config.GEMINI_TEMPERATURE)
    _max_tokens = (_tuning or {}).get("max_tokens", config.GEMINI_MAX_TOKENS)
    if is_nsfw:
        client = get_openrouter_client()
        if client:
            try:
                t0 = time.monotonic()
                messages = [{"role": "system", "content": system_instructions}]
                for msg in conversation_history:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
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
                                "content": await _build_user_content(
                                    content, msg_media
                                ),
                            }
                        )
                    else:
                        messages.append({"role": "assistant", "content": content or ""})
                messages.append(
                    {
                        "role": "user",
                        "content": await _build_user_content(user_message, media_urls),
                    }
                )

                # Phase 12: archetype tuning also applies to the OpenRouter
                # (NSFW) path so per-archetype caps work for NSFW companions etc.
                _or_temp = (
                    (_tuning or {}).get("temperature")
                    if _tuning is not None
                    else config.OPENROUTER_TEMPERATURE
                )
                _or_max = (
                    (_tuning or {}).get("max_tokens")
                    if _tuning is not None
                    else config.OPENROUTER_MAX_TOKENS
                )
                response = await client.chat.completions.create(
                    model=config.OPENROUTER_MODEL,
                    messages=messages,
                    max_tokens=_or_max,
                    temperature=_or_temp,
                )
                latency_ms = (time.monotonic() - t0) * 1000

                choices = response.choices or []
                if not choices:
                    raise RuntimeError(
                        f"OpenRouter returned no choices (model={config.OPENROUTER_MODEL})"
                    )
                message = choices[0].message
                response_text = (message.content if message else None) or ""
                response_text = response_text.strip()

                input_tokens = 0
                token_count = 0
                if response.usage:
                    input_tokens = response.usage.prompt_tokens or 0
                    token_count = response.usage.completion_tokens or 0
                if not token_count and response_text:
                    token_count = int(len(response_text) / 4)

                langfuse_tracing.trace_generation(
                    trace_name="chat-response",
                    user_id=user_id,
                    model=config.OPENROUTER_MODEL,
                    provider="openrouter",
                    input_text=user_message,
                    output_text=response_text,
                    input_tokens=input_tokens,
                    output_tokens=token_count,
                    latency_ms=latency_ms,
                    conversation_id=conversation_id,
                )

                return LlmResponse(
                    content=response_text,
                    provider="openrouter",
                    model=config.OPENROUTER_MODEL,
                    input_tokens=input_tokens,
                    output_tokens=token_count,
                    latency_ms=latency_ms,
                )
            except Exception as e:
                logger.error(f"OpenRouter generation failed: {e}")

    if not config.GEMINI_API_KEY:
        logger.error("No AI client available (GEMINI_API_KEY not set)")
        return LlmResponse(
            content=ERROR_MESSAGES["NO_PROVIDER"],
            provider="none",
            model="none",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            is_fallback=True,
            error_code="NO_PROVIDER",
        )

    try:
        t0 = time.monotonic()
        system_instruction, contents = await _build_gemini_contents(
            system_instructions,
            conversation_history,
            user_message,
            media_urls,
        )
        response_text, token_count = await _call_gemini(
            contents=contents,
            system_instruction=system_instruction,
            temperature=_temperature,
            max_tokens=_max_tokens,
        )
        latency_ms = (time.monotonic() - t0) * 1000

        langfuse_tracing.trace_generation(
            trace_name="chat-response",
            user_id=user_id,
            model=config.GEMINI_MODEL,
            provider="gemini",
            input_text=user_message,
            output_text=response_text,
            input_tokens=0,
            output_tokens=token_count,
            latency_ms=latency_ms,
            conversation_id=conversation_id,
        )

        return LlmResponse(
            content=response_text,
            provider="gemini",
            model=config.GEMINI_MODEL,
            input_tokens=0,
            output_tokens=token_count,
            latency_ms=latency_ms,
        )
    except LlmBlockedError as e:
        logger.warning(f"Gemini blocked the response: {e.reason}")
        elapsed = (time.monotonic() - t0) * 1000 if "t0" in locals() else 0
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
    except Exception as e:
        logger.error(f"Gemini generation failed: {e}")
        elapsed = (time.monotonic() - t0) * 1000 if "t0" in locals() else 0
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


MEMORY_EXTRACTION_PROMPT = """Extract factual information about the user from this conversation exchange.

CATEGORIES (use these as key prefixes):
- identity: name, age, gender, location, occupation, language
- preferences: favorite_food, hobbies, interests, music_taste, style
- goals: fitness_goal, career_goal, learning_goal, relationship_goal
- context: relationship_status, family, pets, living_situation
- emotional: current_mood, stress_level, recent_events

Recent exchange:
User: {user_message}
Assistant: {assistant_response}

Current memories:
{memories_text}

Rules:
- Return ONLY a JSON object with key-value pairs
- Use lowercase keys with underscores
- Only extract EXPLICIT facts the user stated, not inferences
- If the user corrects a previous fact, use the new value
- If no new information, return empty object {{}}
- Keep values concise (under 50 chars each)
Format: {{"identity_name": "Rahul", "goals_fitness": "lose 10kg by August"}}"""


async def extract_memories(
    user_message: str,
    assistant_response: str,
    existing_memories: dict,
    is_nsfw: bool = False,
) -> dict:
    if existing_memories:
        memories_text = "\n".join(f"- {k}: {v}" for k, v in existing_memories.items())
    else:
        memories_text = "(none yet)"

    prompt = MEMORY_EXTRACTION_PROMPT.format(
        user_message=user_message,
        assistant_response=assistant_response,
        memories_text=memories_text,
    )

    try:
        if is_nsfw:
            client = get_openrouter_client()
            if client:
                response = await client.chat.completions.create(
                    model=config.OPENROUTER_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a helpful assistant that returns valid JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=1024,
                    temperature=0.1,
                )
                response_text = response.choices[0].message.content or ""
                start = response_text.find("{")
                end = response_text.rfind("}") + 1
                if start >= 0 and end > start:
                    new_memories = json.loads(response_text[start:end])
                    if isinstance(new_memories, dict):
                        return {**existing_memories, **new_memories}
                return existing_memories

        if not config.GEMINI_API_KEY:
            return existing_memories

        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        system_instruction = {
            "parts": [{"text": "You are a helpful assistant that returns valid JSON."}]
        }

        response_text, _ = await _call_gemini(
            contents=contents,
            system_instruction=system_instruction,
            temperature=0.1,
            max_tokens=1024,
        )

        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            new_memories = json.loads(response_text[start:end])
            if isinstance(new_memories, dict):
                return {**existing_memories, **new_memories}
        return existing_memories
    except Exception as e:
        logger.warning(f"Memory extraction failed (non-fatal): {e}")
        return existing_memories


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
    if not config.GEMINI_API_KEY:
        return None
    if not _is_safe_url(audio_url):
        logger.error(f"Audio URL blocked (SSRF protection): {audio_url[:50]}")
        return None

    try:
        async with httpx.AsyncClient(timeout=60) as http:
            download_response = await http.get(audio_url, timeout=15)
            download_response.raise_for_status()
            audio_bytes = download_response.content
            content_type = download_response.headers.get("content-type", "audio/mpeg")
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

            url = f"{GEMINI_NATIVE_URL}/models/{config.GEMINI_MODEL}:generateContent"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {
                                "text": "Please transcribe this audio file accurately. Only return the transcription text without any additional commentary."
                            },
                            {
                                "inlineData": {
                                    "mimeType": content_type,
                                    "data": audio_b64,
                                }
                            },
                        ],
                    }
                ],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096},
            }
            response = await http.post(
                url, json=payload, params={"key": config.GEMINI_API_KEY}, timeout=60
            )
            response.raise_for_status()

            data = response.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                for part in parts:
                    if "text" in part:
                        return part["text"].strip()
            return None
    except Exception as e:
        logger.error(f"Audio transcription failed: {e}")
        return None
