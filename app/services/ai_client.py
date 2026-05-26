import json
import base64
import logging

import httpx
from openai import AsyncOpenAI

import config

logger = logging.getLogger(__name__)

FALLBACK_ERROR_MESSAGE = (
    "I'm having trouble responding right now. Please try again in a moment."
)
GEMINI_NATIVE_URL = "https://generativelanguage.googleapis.com/v1beta"

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
        raise ValueError(f"Gemini returned no candidates (blockReason={block_reason})")

    parts = candidates[0].get("content", {}).get("parts", [])
    response_text = ""
    for part in parts:
        if "text" in part:
            response_text += part["text"]
    response_text = response_text.strip()

    if not response_text:
        finish_reason = candidates[0].get("finishReason", "UNKNOWN")
        raise ValueError(
            f"Gemini returned candidate with no text (finishReason={finish_reason})"
        )

    usage = data.get("usageMetadata", {})
    token_count = usage.get("candidatesTokenCount", 0)
    if not token_count and response_text:
        token_count = int(len(response_text) / 4)

    return response_text, token_count


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


async def generate_response(
    system_instructions: str,
    conversation_history: list[dict],
    user_message: str,
    is_nsfw: bool = False,
    media_urls: list[str] | None = None,
) -> tuple[str, int, bool]:
    if is_nsfw:
        client = get_openrouter_client()
        if client:
            try:
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

                response = await client.chat.completions.create(
                    model=config.OPENROUTER_MODEL,
                    messages=messages,
                    max_tokens=config.OPENROUTER_MAX_TOKENS,
                    temperature=config.OPENROUTER_TEMPERATURE,
                )
                choices = response.choices or []
                if not choices:
                    raise RuntimeError(
                        f"OpenRouter returned no choices (model={config.OPENROUTER_MODEL})"
                    )
                message = choices[0].message
                response_text = (message.content if message else None) or ""
                response_text = response_text.strip()

                token_count = 0
                if response.usage:
                    token_count = response.usage.completion_tokens or 0
                if not token_count and response_text:
                    token_count = int(len(response_text) / 4)

                return (response_text, token_count, False)
            except Exception as e:
                logger.error(f"OpenRouter generation failed: {e}")

    if not config.GEMINI_API_KEY:
        logger.error("No AI client available (GEMINI_API_KEY not set)")
        return (FALLBACK_ERROR_MESSAGE, 0, True)

    try:
        system_instruction, contents = await _build_gemini_contents(
            system_instructions,
            conversation_history,
            user_message,
            media_urls,
        )
        response_text, token_count = await _call_gemini(
            contents=contents,
            system_instruction=system_instruction,
            temperature=config.GEMINI_TEMPERATURE,
            max_tokens=config.GEMINI_MAX_TOKENS,
        )
        return (response_text, token_count, False)
    except Exception as e:
        logger.error(f"Gemini generation failed: {e}")
        return (FALLBACK_ERROR_MESSAGE, 0, True)


MEMORY_EXTRACTION_PROMPT = """Extract any factual information about the user from this conversation that should be remembered for future interactions.

Examples of things to remember:
- Physical attributes: height, weight, age, appearance
- Personal information: name, location, occupation, interests
- Preferences: favorite foods, hobbies, goals
- Context: relationship status, family, pets

Recent conversation:
User: {user_message}
Assistant: {assistant_response}

Current memories:
{memories_text}

Return ONLY a JSON object with key-value pairs. Use lowercase keys with underscores (e.g., "height", "weight", "name").
If no new information was provided, return an empty object {{}}.
If information updates an existing memory, use the new value.
Format: {{"key1": "value1", "key2": "value2"}}"""


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
